import base64
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import anthropic
from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from job_os.auth import get_current_user
from job_os.db.models import Application, Job, Resume, ResumeRevisionMessage, ResumeVersion, User
from job_os.db.session import get_session
from job_os.schemas.resumes import (
    BuiltinTemplateSummary,
    ConfirmUploadRequest,
    ExportRequest,
    ExportResult,
    GeneratedTemplateResponse,
    PresignUploadRequest,
    PresignUploadResponse,
    ResumeChatRequest,
    ResumeChatResponse,
    ResumeCreate,
    ResumeDirectEditRequest,
    ResumeImportItem,
    ResumeImportResult,
    ResumePatch,
    ResumePreviewRequest,
    ResumeRead,
    ResumeRenderResponse,
    ResumeRenderReviewRequest,
    ResumeRenderReviewResponse,
    ResumeReviewResult,
    ResumeVersionCreate,
    ResumeVersionRead,
    ResumeVersionSummary,
    RevisionMessageRead,
    TailorRequest,
    TailorResponse,
)

router = APIRouter(prefix="/resumes")


@router.get("", response_model=list[ResumeRead])
async def list_resumes(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[Resume]:
    result = await session.execute(
        select(Resume)
        .where(Resume.user_id == user.id, Resume.archived_at.is_(None))
        .order_by(Resume.is_master.desc(), Resume.name)
    )
    resumes = list(result.scalars().all())

    # How many versions under each resume were actually tailored for a job,
    # vs. just generic drafts — the frontend uses this to surface the
    # resumes doing real work in a search over the ones that aren't.
    counts_result = await session.execute(
        select(ResumeVersion.resume_id, func.count(ResumeVersion.id))
        .where(
            ResumeVersion.resume_id.in_([r.id for r in resumes]),
            ResumeVersion.spawned_from_job_id.is_not(None),
            ResumeVersion.archived_at.is_(None),
        )
        .group_by(ResumeVersion.resume_id)
    )
    counts: dict[UUID, int] = dict(counts_result.all())  # type: ignore[arg-type]
    for resume in resumes:
        resume.tailored_count = counts.get(resume.id, 0)  # type: ignore[attr-defined]
    return resumes


@router.post("", response_model=ResumeRead, status_code=201)
async def create_resume(
    payload: ResumeCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Resume:
    existing = (
        await session.execute(
            select(Resume).where(Resume.user_id == user.id, Resume.name == payload.name)
        )
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(409, f"resume named {payload.name!r} already exists")
    if payload.is_master:
        master = (
            await session.execute(
                select(Resume).where(
                    Resume.user_id == user.id,
                    Resume.is_master.is_(True),
                    Resume.archived_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if master:
            raise HTTPException(409, "A master resume already exists.")
    resume = Resume(
        user_id=user.id,
        name=payload.name,
        base_role=payload.base_role,
        is_master=payload.is_master,
        source_kind=payload.source_kind,
        source_label=payload.source_label,
    )
    session.add(resume)
    await session.flush()
    return resume


@router.get("/versions/by-application/{application_id}", response_model=list[ResumeVersionRead])
async def list_versions_by_application(
    application_id: UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[ResumeVersion]:
    """Every resume version attached to one application — tailor-generated or
    uploaded — newest first. Ownership is checked through the application,
    not the version, since a version's own resume may belong to a resume the
    caller can otherwise no longer see (e.g. archived)."""
    application = await session.get(Application, application_id)
    if application is None or application.user_id != user.id:
        raise HTTPException(404, "application not found")

    result = await session.execute(
        select(ResumeVersion)
        .where(
            ResumeVersion.spawned_from_application_id == application_id,
            ResumeVersion.archived_at.is_(None),
        )
        .order_by(ResumeVersion.created_at.desc())
    )
    return list(result.scalars().all())


@router.post("/import", response_model=ResumeImportResult, status_code=201)
async def import_resume_files(
    files: list[UploadFile] = File(...),
    source_label: str = Form(default="iCloud Drive"),
    master_filename: str | None = Form(default=None),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ResumeImportResult:
    """Import master and role-specific PDF, DOCX, or JSON Resume files.

    Browsers cannot silently read iCloud Drive. The web picker supplies the
    selected files here, and this endpoint turns each into editable JSON while
    retaining the original PDF bytes when available.
    """
    from job_os.services.profile_extract import (
        extract_json_resume_from_docx,
        extract_json_resume_from_pdf,
    )
    from job_os.services.profile_import import import_json_resume
    from job_os.services.resume_engine import validate_json_resume_document

    if len(files) > 30:
        raise HTTPException(
            400,
            "Import at most 30 resumes per batch so every file can be extracted reliably.",
        )

    filenames = [Path(upload.filename or "resume").name for upload in files]
    if master_filename and master_filename not in filenames:
        raise HTTPException(400, "The selected master file is not in this import batch.")
    existing_master = (
        await session.execute(
            select(Resume).where(Resume.user_id == user.id, Resume.is_master.is_(True))
        )
    ).scalar_one_or_none()
    inferred_master = master_filename
    if inferred_master is None:
        inferred_master = next(
            (name for name in filenames if "master" in Path(name).stem.lower()),
            filenames[0] if existing_master is None and filenames else None,
        )

    result = ResumeImportResult()
    total_bytes = 0
    for upload in files:
        filename = Path(upload.filename or "resume").name
        raw = await upload.read()
        total_bytes += len(raw)
        if len(raw) > 12 * 1024 * 1024:
            result.items.append(
                ResumeImportItem(
                    filename=filename,
                    imported=False,
                    note="File exceeds the 12 MB resume limit.",
                )
            )
            continue
        if total_bytes > 30 * 1024 * 1024:
            result.items.append(
                ResumeImportItem(
                    filename=filename,
                    imported=False,
                    note="Batch exceeds the 30 MB import limit.",
                )
            )
            continue
        if not raw:
            result.items.append(
                ResumeImportItem(filename=filename, imported=False, note="Empty file")
            )
            continue
        try:
            lower = filename.lower()
            if lower.endswith(".pdf"):
                doc = await extract_json_resume_from_pdf(raw)
            elif lower.endswith(".docx"):
                doc = await extract_json_resume_from_docx(raw)
            elif lower.endswith(".json"):
                doc = json.loads(raw.decode("utf-8"))
            else:
                raise ValueError("Only PDF, DOCX, and JSON Resume files are supported.")
            validate_json_resume_document(doc)

            display_name = _resume_name_from_filename(filename)
            is_master = filename == inferred_master
            resume = (
                await session.execute(
                    select(Resume).where(
                        Resume.user_id == user.id,
                        Resume.is_master.is_(True) if is_master else Resume.name == display_name,
                    )
                )
            ).scalar_one_or_none()
            if resume is None:
                resume = Resume(
                    user_id=user.id,
                    name="Master" if is_master else display_name,
                    base_role="master" if is_master else display_name,
                    is_master=is_master,
                    source_kind="icloud",
                    source_label=source_label,
                )
                session.add(resume)
                await session.flush()

            version = ResumeVersion(
                resume_id=resume.id,
                json_resume=doc,
                approved_by_user=False,
                pdf_bytes=raw if lower.endswith(".pdf") else None,
                source_filename=filename,
                status="imported",
                revision_note=f"Imported from {source_label}",
            )
            session.add(version)
            await session.flush()

            if is_master:
                await import_json_resume(
                    session,
                    user=user,
                    doc=doc,
                    mark_verified=True,
                    replace_existing=False,
                )

            result.items.append(
                ResumeImportItem(
                    filename=filename,
                    resume_id=resume.id,
                    version_id=version.id,
                    imported=True,
                    is_master=is_master,
                    note="Imported as editable JSON Resume.",
                )
            )
        except (
            ValueError,
            json.JSONDecodeError,
            RuntimeError,
            ValidationError,
            anthropic.APIError,
        ) as exc:
            result.items.append(
                ResumeImportItem(filename=filename, imported=False, note=str(exc))
            )
    return result


@router.get("/templates/builtin", response_model=list[BuiltinTemplateSummary])
async def list_builtin_templates(
    _user: User = Depends(get_current_user),
) -> list[BuiltinTemplateSummary]:
    """The templates that ship with the app, with their real licences and caveats.

    The catalogue lives in the container because that is where the LaTeX lives.
    The seeding script writes the same records into Appwrite so the browser can
    read them without a round trip here.
    """
    from job_os.services.latex_catalog import BUILTIN_TEMPLATES

    return [
        BuiltinTemplateSummary(
            key=spec.key,
            name=spec.name,
            description=spec.description,
            columns=spec.columns,
            ats_note=spec.ats_note,
            upstream=spec.upstream,
            licence=spec.licence,
            author=spec.author,
            tags=list(spec.tags),
        )
        for spec in BUILTIN_TEMPLATES
    ]


@router.post("/preview")
async def preview_draft(
    payload: ResumePreviewRequest,
    _user: User = Depends(get_current_user),
) -> Response:
    """Render unsaved JSON Resume state without storing or mutating it.

    Returns a PDF rather than the HTML this used to return: the resume look is
    LaTeX now, and an HTML approximation of it would be a different document
    from the one that gets sent to an employer.
    """
    from job_os.services.latex_render import LatexRenderError, render_resume_pdf

    try:
        rendered = render_resume_pdf(
            payload.json_resume, template_key=payload.template_key
        )
    except LatexRenderError as exc:
        raise HTTPException(422, f"{exc} {_render_hint(exc)}".strip()) from exc
    return Response(
        rendered.bytes_,
        media_type="application/pdf",
        headers={"Content-Disposition": 'inline; filename="preview.pdf"'},
    )


@router.post("/render-review", response_model=ResumeRenderReviewResponse)
async def render_and_review_draft(
    payload: ResumeRenderReviewRequest,
    _user: User = Depends(get_current_user),
) -> ResumeRenderReviewResponse:
    """Render and review a document without storing or mutating anything.

    The Appwrite agent function cannot do this: its python runtime has no LaTeX
    engine, so a tailored version comes back there with no PDF and no page
    count. This container image ships Tectonic and a warm package cache (see
    Dockerfile.vercel), so the browser hands the tailored document here and
    writes the result back to Appwrite.

    Stateless on purpose. Resumes tailored through the Appwrite workspace do not
    exist in this database, so there is no row to look up. That is also why the
    caller has to hand over `verified_facts`: the reviewer needs the evidence
    vault to tell a verified claim from an invented one, and this service has no
    way to read the Appwrite one.
    """
    from job_os.services.latex_render import LatexRenderError
    from job_os.services.resume_engine import generate_latex_source, review_resume

    try:
        review, pdf_bytes = await review_resume(
            payload.json_resume,
            template_key=payload.template_key,
            latex_source=payload.latex_source,
            verified_facts=payload.verified_facts,
        )
    except LatexRenderError as exc:
        raise HTTPException(422, f"{exc} {_render_hint(exc)}".strip()) from exc
    return ResumeRenderReviewResponse(
        review=review,
        latex_source=generate_latex_source(payload.json_resume),
        pdf_base64=base64.b64encode(pdf_bytes).decode("ascii"),
    )


@router.post("/render", response_model=ResumeRenderResponse)
async def render_draft(
    payload: ResumeRenderReviewRequest,
    _user: User = Depends(get_current_user),
) -> ResumeRenderResponse:
    """Render a document to PDF WITHOUT the quality review, and return the bytes.

    The fast half of /render-review. The render takes a few seconds; the model
    review takes over a minute, so a caller that only needs a downloadable PDF, or
    a PDF to attach to storage before the review lands, hits this instead of paying
    for the review it does not need yet. Same stateless contract as /render-review:
    nothing is stored here. The Appwrite workspace attaches the returned PDF to the
    version so Download works immediately, then fetches the review separately.
    """
    from job_os.services.latex_render import LatexRenderError, render_resume_pdf
    from job_os.services.resume_engine import (
        generate_latex_source,
        validate_json_resume_document,
    )

    validate_json_resume_document(payload.json_resume)
    try:
        rendered = render_resume_pdf(
            payload.json_resume,
            template_key=payload.template_key,
            latex_source=payload.latex_source,
        )
    except LatexRenderError as exc:
        raise HTTPException(422, f"{exc} {_render_hint(exc)}".strip()) from exc
    return ResumeRenderResponse(
        latex_source=generate_latex_source(payload.json_resume),
        pdf_base64=base64.b64encode(rendered.bytes_).decode("ascii"),
    )


def _render_hint(exc: Exception) -> str:
    """The one line of a TeX log that says what went wrong, if it is in there.

    A LaTeX log is thousands of lines and unreadable to somebody who did not
    write LaTeX. The line beginning with `!` is the error.
    """
    log = getattr(exc, "log", "") or ""
    for line in log.splitlines():
        if line.startswith("!"):
            return line.strip()
    return ""


@router.post("/latex-template", response_model=GeneratedTemplateResponse)
async def build_latex_template(
    file: UploadFile = File(...),
    name: str | None = Form(default=None),
    _user: User = Depends(get_current_user),
) -> GeneratedTemplateResponse:
    """Turn an uploaded .tex or .pdf into a reusable LaTeX template.

    Lives here rather than in the Appwrite agent function because accepting a
    template means really compiling it, and that runtime has no LaTeX engine.

    A .tex upload is the accurate path: the design is already expressed as
    LaTeX and the model only has to replace the content with placeholders. A
    .pdf is reconstructed from the document itself, which is a best effort at
    the design rather than a copy of it.

    Either way the result has to compile with sample data before it is
    returned. A design that cannot be turned into a template that compiles
    returns 422 with the compiler's own reason, and nothing is stored.
    """
    from job_os.services.latex_from_document import (
        TemplateBuildError,
        build_template_from_upload,
    )

    raw = await file.read()
    if len(raw) > 12 * 1024 * 1024:
        raise HTTPException(400, "Template uploads are limited to 12 MB.")
    if not raw:
        raise HTTPException(400, "That file is empty.")
    try:
        candidate = await build_template_from_upload(
            raw,
            Path(file.filename or "template.tex").name,
            requested_name=(name or "").strip() or None,
        )
    except TemplateBuildError as exc:
        raise HTTPException(422, str(exc)) from exc
    return GeneratedTemplateResponse(
        name=candidate.name,
        latex_source=candidate.latex_source,
        notes=candidate.notes,
        pdf_base64=base64.b64encode(candidate.pdf_bytes).decode("ascii"),
        attempts=candidate.attempts,
        repairs=candidate.repairs,
    )


@router.patch("/{resume_id}", response_model=ResumeRead)
async def update_resume(
    resume_id: UUID,
    payload: ResumePatch,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Resume:
    resume = await _load_resume(session, resume_id, user)
    if payload.name is not None:
        name = payload.name.strip()
        if not name:
            raise HTTPException(400, "Resume name cannot be empty.")
        duplicate = (
            await session.execute(
                select(Resume).where(
                    Resume.user_id == user.id,
                    Resume.name == name,
                    Resume.id != resume.id,
                )
            )
        ).scalar_one_or_none()
        if duplicate:
            raise HTTPException(409, f"Resume named {name!r} already exists.")
        resume.name = name
    if payload.base_role is not None:
        resume.base_role = payload.base_role.strip() or None
    return resume


@router.delete("/{resume_id}", status_code=204)
async def delete_resume(
    resume_id: UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    resume = await _load_resume(session, resume_id, user)
    if resume.is_master:
        raise HTTPException(
            409,
            "The protected master cannot be archived. Import a replacement instead.",
        )
    resume.archived_at = datetime.now(UTC)
    return Response(status_code=204)


@router.get("/{resume_id}/versions", response_model=list[ResumeVersionSummary])
async def list_versions(
    resume_id: UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[ResumeVersion]:
    await _load_resume(session, resume_id, user)
    result = await session.execute(
        select(ResumeVersion)
        .where(
            ResumeVersion.resume_id == resume_id,
            ResumeVersion.archived_at.is_(None),
        )
        .order_by(ResumeVersion.created_at.desc())
    )
    return list(result.scalars().all())


@router.post("/{resume_id}/versions", response_model=ResumeVersionRead, status_code=201)
async def create_version(
    resume_id: UUID,
    payload: ResumeVersionCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ResumeVersion:
    from job_os.services.resume_engine import validate_json_resume_document

    await _load_resume(session, resume_id, user)
    validate_json_resume_document(payload.json_resume)
    if payload.parent_version_id is not None:
        await _load_version(
            session,
            resume_id,
            payload.parent_version_id,
            user,
        )
    version = ResumeVersion(
        resume_id=resume_id,
        json_resume=payload.json_resume,
        spawned_from_job_id=payload.spawned_from_job_id,
        spawned_from_application_id=payload.spawned_from_application_id,
        provenance=payload.provenance,
        ats_score=payload.ats_score,
        ats_report=payload.ats_report,
        parent_version_id=payload.parent_version_id,
        source_filename=payload.source_filename,
        revision_note=payload.revision_note,
    )
    session.add(version)
    await session.flush()
    return version


@router.post(
    "/{resume_id}/versions/{version_id}/edit",
    response_model=ResumeVersionRead,
    status_code=201,
)
async def edit_version(
    resume_id: UUID,
    version_id: UUID,
    payload: ResumeDirectEditRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ResumeVersion:
    from job_os.services.resume_engine import (
        generate_latex_source,
        validate_json_resume_document,
    )

    await _load_version(session, resume_id, version_id, user)
    validate_json_resume_document(payload.json_resume)
    edited = ResumeVersion(
        resume_id=resume_id,
        json_resume=payload.json_resume,
        parent_version_id=version_id,
        revision_note=payload.note,
        latex_source=generate_latex_source(payload.json_resume),
        status="draft",
        approved_by_user=False,
    )
    session.add(edited)
    await session.flush()
    return edited


@router.delete("/{resume_id}/versions/{version_id}", status_code=204)
async def delete_version(
    resume_id: UUID,
    version_id: UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    resume = await _load_resume(session, resume_id, user)
    version = await _load_version(session, resume_id, version_id, user)
    if resume.is_master:
        count = await session.scalar(
            select(func.count()).select_from(ResumeVersion).where(
                ResumeVersion.resume_id == resume_id,
                ResumeVersion.archived_at.is_(None),
            )
        )
        if int(count or 0) <= 1:
            raise HTTPException(409, "The only master version cannot be archived.")
    version.archived_at = datetime.now(UTC)
    return Response(status_code=204)


@router.post(
    "/{resume_id}/versions/{version_id}/review",
    response_model=ResumeReviewResult,
)
async def review_version(
    resume_id: UUID,
    version_id: UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ResumeReviewResult:
    from job_os.services.resume_engine import review_resume

    version = await _load_version(session, resume_id, version_id, user)
    review, pdf_bytes = await review_resume(version.json_resume)
    version.review_score = review.score
    version.review_report = review.model_dump(mode="json")
    version.pdf_bytes = pdf_bytes
    version.status = "reviewed" if review.passed else "needs_changes"
    return review


@router.post(
    "/{resume_id}/versions/{version_id}/chat",
    response_model=ResumeChatResponse,
)
async def chat_edit_version(
    resume_id: UUID,
    version_id: UUID,
    payload: ResumeChatRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ResumeChatResponse:
    from job_os.services.resume_engine import (
        generate_latex_source,
        review_resume,
        revise_resume,
    )
    from job_os.services.tailor import (
        _build_facts_payload,
        _load_bullets,
        _load_verified_facts,
    )

    current = await _load_version(session, resume_id, version_id, user)
    facts = await _load_verified_facts(session, user.id)
    bullets = await _load_bullets(session, [fact.id for fact in facts])
    verified_facts = _build_facts_payload(facts, bullets)

    user_message = ResumeRevisionMessage(
        resume_version_id=version_id,
        role="user",
        content=payload.message,
        applied=payload.apply,
    )
    session.add(user_message)

    try:
        revision = await revise_resume(
            current.json_resume,
            message=payload.message,
            verified_facts=verified_facts,
        )
    except (ValueError, ValidationError, RuntimeError) as exc:
        raise HTTPException(422, str(exc)) from exc

    new_version: ResumeVersion | None = None
    review: ResumeReviewResult | None = None
    if payload.apply:
        review, pdf_bytes = await review_resume(revision.json_resume)
        new_version = ResumeVersion(
            resume_id=resume_id,
            json_resume=revision.json_resume,
            parent_version_id=version_id,
            revision_note=payload.message,
            latex_source=generate_latex_source(revision.json_resume),
            status="reviewed" if review.passed else "needs_changes",
            review_score=review.score,
            review_report=review.model_dump(mode="json"),
            pdf_bytes=pdf_bytes,
            approved_by_user=False,
        )
        session.add(new_version)
        await session.flush()
        # Keep the full request/response pair on the resulting branch so the
        # conversation remains visible after the editor navigates to it.
        user_message.resume_version_id = new_version.id

    assistant_message = ResumeRevisionMessage(
        resume_version_id=new_version.id if new_version else version_id,
        role="assistant",
        content=revision.assistant_message,
        suggestions=revision.suggestions,
        proposed_json_resume=None if payload.apply else revision.json_resume,
        applied=payload.apply,
    )
    session.add(assistant_message)
    await session.flush()
    return ResumeChatResponse(
        message=revision.assistant_message,
        suggestions=revision.suggestions,
        proposal_id=None if payload.apply else assistant_message.id,
        proposed_json_resume=None if payload.apply else revision.json_resume,
        version=new_version,
        review=review,
    )


@router.post(
    "/{resume_id}/versions/{version_id}/messages/{message_id}/apply",
    response_model=ResumeChatResponse,
    status_code=201,
)
async def apply_revision_proposal(
    resume_id: UUID,
    version_id: UUID,
    message_id: UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ResumeChatResponse:
    """Apply a persisted AI proposal only after the user explicitly accepts it."""
    from job_os.services.resume_engine import generate_latex_source, review_resume

    await _load_version(session, resume_id, version_id, user)
    proposal = await session.get(ResumeRevisionMessage, message_id)
    if (
        proposal is None
        or proposal.resume_version_id != version_id
        or proposal.role != "assistant"
        or proposal.proposed_json_resume is None
    ):
        raise HTTPException(404, "revision proposal not found")
    if proposal.applied:
        raise HTTPException(409, "This revision proposal has already been applied.")

    review, pdf_bytes = await review_resume(proposal.proposed_json_resume)
    version = ResumeVersion(
        resume_id=resume_id,
        json_resume=proposal.proposed_json_resume,
        parent_version_id=version_id,
        revision_note=f"Accepted AI proposal: {proposal.content[:240]}",
        latex_source=generate_latex_source(proposal.proposed_json_resume),
        status="reviewed" if review.passed else "needs_changes",
        review_score=review.score,
        review_report=review.model_dump(mode="json"),
        pdf_bytes=pdf_bytes,
        approved_by_user=False,
    )
    session.add(version)
    proposal.applied = True
    await session.flush()
    return ResumeChatResponse(
        message="Proposal applied as a new recoverable revision.",
        suggestions=proposal.suggestions,
        proposal_id=proposal.id,
        proposed_json_resume=proposal.proposed_json_resume,
        version=version,
        review=review,
    )


@router.get(
    "/{resume_id}/versions/{version_id}/messages",
    response_model=list[RevisionMessageRead],
)
async def list_revision_messages(
    resume_id: UUID,
    version_id: UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[ResumeRevisionMessage]:
    current = await _load_version(session, resume_id, version_id, user)
    lineage_ids: list[UUID] = []
    seen: set[UUID] = set()
    while current.id not in seen and len(lineage_ids) < 50:
        seen.add(current.id)
        lineage_ids.append(current.id)
        if current.parent_version_id is None:
            break
        parent = await session.get(ResumeVersion, current.parent_version_id)
        if parent is None or parent.resume_id != resume_id:
            break
        current = parent
    result = await session.execute(
        select(ResumeRevisionMessage)
        .where(ResumeRevisionMessage.resume_version_id.in_(lineage_ids))
        .order_by(ResumeRevisionMessage.created_at)
    )
    return list(result.scalars().all())


@router.post(
    "/{resume_id}/versions/{version_id}/finalize",
    response_model=ResumeVersionRead,
)
async def finalize_version(
    resume_id: UUID,
    version_id: UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ResumeVersion:
    from job_os.services.resume_engine import generate_latex_source, review_resume

    version = await _load_version(session, resume_id, version_id, user)
    review, pdf_bytes = await review_resume(version.json_resume)
    version.review_score = review.score
    version.review_report = review.model_dump(mode="json")
    version.pdf_bytes = pdf_bytes
    version.latex_source = generate_latex_source(version.json_resume)
    if not review.passed:
        version.status = "needs_changes"
        # Preserve the useful review report even though the endpoint returns
        # a conflict response and the normal dependency would otherwise roll
        # the transaction back.
        await session.commit()
        raise HTTPException(
            409,
            {
                "message": "Resume did not pass the final quality gate.",
                "review": review.model_dump(mode="json"),
            },
        )
    version.status = "final"
    version.approved_by_user = True
    version.finalized_at = datetime.now(UTC)
    return version


@router.get("/{resume_id}/versions/{version_id}/preview")
async def preview_version(
    resume_id: UUID,
    version_id: UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """The stored version, rendered. A PDF, because that is what a resume is."""
    from job_os.services.latex_render import LatexRenderError, render_resume_pdf

    version = await _load_version(session, resume_id, version_id, user)
    try:
        rendered = render_resume_pdf(version.json_resume)
    except LatexRenderError as exc:
        raise HTTPException(422, f"{exc} {_render_hint(exc)}".strip()) from exc
    return Response(
        rendered.bytes_,
        media_type="application/pdf",
        headers={"Content-Disposition": 'inline; filename="preview.pdf"'},
    )


@router.get("/{resume_id}/versions/{version_id}", response_model=ResumeVersionRead)
async def get_version(
    resume_id: UUID,
    version_id: UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ResumeVersion:
    return await _load_version(session, resume_id, version_id, user)


@router.post("/{resume_id}/versions/{version_id}/export", response_model=ExportResult)
async def export_version(
    resume_id: UUID,
    version_id: UUID,
    payload: ExportRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ExportResult:
    """Render the version's JSON Resume to PDF with LaTeX, push to R2 if
    configured (otherwise just report the rendered byte count)."""
    from job_os.integrations import r2
    from job_os.services.latex_render import render_resume_pdf

    await _load_resume(session, resume_id, user)
    version = await session.get(ResumeVersion, version_id)
    if version is None or version.resume_id != resume_id:
        raise HTTPException(404, "version not found")
    if version.status != "final":
        raise HTTPException(
            409,
            "Only a finalized resume can be exported. Use Preview while editing.",
        )

    fmt = payload.format
    if fmt != "pdf":
        raise HTTPException(
            400, "Only `pdf` is supported today (DOCX renderer coming with template-2)."
        )

    rendered = render_resume_pdf(version.json_resume)
    key = f"resumes/{user.id}/{resume_id}/{version_id}/{uuid4().hex}.pdf"
    upload = await r2.upload(key, rendered.bytes_, rendered.content_type)
    presigned = await r2.presign_get(key) if upload else None
    if upload is None:
        return ExportResult(
            format=fmt,
            r2_key=None,
            presigned_url=None,
            rendered=True,
            note=(
                f"Rendered {len(rendered.bytes_)} bytes — "
                "R2 not configured, use /download instead."
            ),
        )

    setattr(version, f"{fmt}_r2_key", key)
    return ExportResult(format=fmt, r2_key=key, presigned_url=presigned, rendered=True)


@router.get("/{resume_id}/versions/{version_id}/download")
async def download_version(
    resume_id: UUID,
    version_id: UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Stream the rendered PDF.

    Serves the cached `pdf_bytes` if present (the tailor flow pre-renders
    via a BackgroundTask). On cache miss, render on demand and persist so
    the next click is instant. The cached path matters because the Vercel
    proxy that fronts this is on a tight serverless-function time budget
    and Render's free tier has a cold-start that already eats most of it.
    """
    await _load_resume(session, resume_id, user)
    version = await session.get(ResumeVersion, version_id)
    if version is None or version.resume_id != resume_id:
        raise HTTPException(404, "version not found")
    if version.status != "final":
        raise HTTPException(409, "Finalize this resume before downloading its PDF.")

    if version.pdf_bytes:
        pdf_bytes = bytes(version.pdf_bytes)
    elif version.pdf_r2_key:
        # An uploaded (not tailor-rendered) version: the bytes are the
        # actual artifact the user built, stored verbatim in R2 — or, with
        # no R2 configured, on local disk under the same key's local://
        # path (see upload_version). Never render json_resume for these;
        # it's only the {"uploaded": True, ...} stub, not a real document.
        if version.pdf_r2_key.startswith("local://"):
            pdf_bytes = Path(version.pdf_r2_key.removeprefix("local://")).read_bytes()
        else:
            from job_os.integrations import r2

            fetched = await r2.download(version.pdf_r2_key)
            if fetched is None:
                raise HTTPException(502, "Could not fetch the uploaded PDF from storage.")
            pdf_bytes = fetched
        # Persist for subsequent clicks. flush() not commit — the session
        # middleware commits at request end.
        version.pdf_bytes = pdf_bytes
        await session.flush()
    else:
        from job_os.services.latex_render import render_resume_pdf

        rendered = render_resume_pdf(version.json_resume)
        pdf_bytes = rendered.bytes_
        # Persist for subsequent clicks. flush() not commit — the session
        # middleware commits at request end.
        version.pdf_bytes = pdf_bytes
        await session.flush()

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="resume_{version_id}.pdf"',
        },
    )


@router.post("/{resume_id}/versions/upload", response_model=ResumeVersionRead, status_code=201)
async def upload_version(
    resume_id: UUID,
    file: UploadFile = File(...),
    note: str = Form(default=""),
    application_id: UUID | None = Form(default=None),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ResumeVersion:
    """Upload a pre-built tailored PDF/DOCX as a new version of a resume.

    The file is stored verbatim in R2 (if configured); `json_resume` is set to
    a minimal stub `{ "uploaded": True, "filename": ... }` since we don't
    extract on upload — the user provided the final artifact and we trust it.
    Trusting it means treating it as done: unlike a tailor-pipeline draft,
    an upload has no independent quality gate to pass, so it goes straight
    to `final` rather than sitting unreachable behind /approve (which
    requires a `review_report` uploads never have).
    """
    from job_os.integrations import r2

    await _load_resume(session, resume_id, user)
    if application_id is not None:
        application = await session.get(Application, application_id)
        if application is None or application.user_id != user.id:
            raise HTTPException(404, "application not found")

    content = await file.read()
    if not content:
        raise HTTPException(400, "empty file")

    name = (file.filename or "uploaded").lower()
    if name.endswith(".pdf"):
        ext, ct = "pdf", "application/pdf"
    elif name.endswith(".docx"):
        ext, ct = "docx", (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
    else:
        raise HTTPException(400, "only .pdf or .docx accepted")

    version = ResumeVersion(
        resume_id=resume_id,
        json_resume={"uploaded": True, "filename": file.filename, "note": note},
        approved_by_user=True,
        status="final",
        finalized_at=datetime.now(UTC),
        spawned_from_application_id=application_id,
    )
    session.add(version)
    await session.flush()

    key = f"resumes/{user.id}/{resume_id}/{version.id}/{uuid4().hex}.{ext}"
    upload = await r2.upload(key, content, ct)
    if upload is None:
        # No R2 — store the upload locally as a fallback. (Single-user dev.)
        from pathlib import Path

        local = Path.home() / ".job_os_uploads" / key
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(content)
        setattr(version, f"{ext}_r2_key", f"local://{local}")
    else:
        setattr(version, f"{ext}_r2_key", key)
    return version


def _ext_for_filename(filename: str) -> str:
    name = filename.lower()
    if name.endswith(".pdf"):
        return "pdf"
    if name.endswith(".docx"):
        return "docx"
    raise HTTPException(400, "only .pdf or .docx accepted")


@router.post("/{resume_id}/versions/presign-upload", response_model=PresignUploadResponse)
async def presign_upload(
    resume_id: UUID,
    payload: PresignUploadRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> PresignUploadResponse:
    """Step 1 of the no-inline-bytes upload path: a URL the caller PUTs the
    raw file to directly, for a client that has the file locally but no
    server of its own for job.os to fetch it from (the MCP connector's
    situation exactly). Not tied to a ResumeVersion row yet — that's created
    in confirm_upload, once the bytes are actually confirmed to be there."""
    from job_os.integrations import r2

    await _load_resume(session, resume_id, user)
    ext = _ext_for_filename(payload.filename)
    key = f"resumes/{user.id}/{resume_id}/uploaded/{uuid4().hex}.{ext}"
    upload_url = await r2.presign_put(key)
    if upload_url is None:
        raise HTTPException(503, "Upload storage is not configured.")
    return PresignUploadResponse(key=key, upload_url=upload_url, expires_in=900)


@router.post(
    "/{resume_id}/versions/confirm-upload", response_model=ResumeVersionRead, status_code=201
)
async def confirm_upload(
    resume_id: UUID,
    payload: ConfirmUploadRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ResumeVersion:
    """Step 2: the caller has PUT bytes to the URL from presign_upload:
    confirm they actually landed before creating anything, so a version
    row is never created for an upload that silently failed or was never
    attempted."""
    from job_os.integrations import r2

    await _load_resume(session, resume_id, user)
    if payload.application_id is not None:
        application = await session.get(Application, payload.application_id)
        if application is None or application.user_id != user.id:
            raise HTTPException(404, "application not found")

    # The key names this exact user and resume, so nothing lets one account
    # confirm an upload into a key that was presigned for another.
    if not payload.key.startswith(f"resumes/{user.id}/{resume_id}/"):
        raise HTTPException(400, "key does not belong to this resume")
    if not await r2.exists(payload.key):
        raise HTTPException(409, "Nothing has been uploaded to that key yet.")

    ext = _ext_for_filename(payload.filename)
    version = ResumeVersion(
        resume_id=resume_id,
        json_resume={"uploaded": True, "filename": payload.filename, "note": payload.note},
        approved_by_user=True,
        status="final",
        finalized_at=datetime.now(UTC),
        spawned_from_application_id=payload.application_id,
    )
    setattr(version, f"{ext}_r2_key", payload.key)
    session.add(version)
    await session.flush()
    return version


@router.post("/{resume_id}/versions/{version_id}/approve", response_model=ResumeVersionRead)
async def approve_version(
    resume_id: UUID,
    version_id: UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ResumeVersion:
    version = await _load_version(session, resume_id, version_id, user)
    report = version.review_report or {}
    if not report.get("passed"):
        raise HTTPException(
            409,
            "This version has not passed the independent quality gate. "
            "Open it in Resume Studio, review the issues, and finalize it there.",
        )
    version.approved_by_user = True
    version.status = "final"
    version.finalized_at = datetime.now(UTC)
    return version


async def _load_resume(session: AsyncSession, resume_id: UUID, user: User) -> Resume:
    resume = await session.get(Resume, resume_id)
    if resume is None or resume.user_id != user.id or resume.archived_at is not None:
        raise HTTPException(404, "resume not found")
    return resume


async def _load_version(
    session: AsyncSession,
    resume_id: UUID,
    version_id: UUID,
    user: User,
) -> ResumeVersion:
    await _load_resume(session, resume_id, user)
    version = await session.get(ResumeVersion, version_id)
    if (
        version is None
        or version.resume_id != resume_id
        or version.archived_at is not None
    ):
        raise HTTPException(404, "version not found")
    return version


def _resume_name_from_filename(filename: str) -> str:
    stem = Path(filename).stem
    for prefix in ("Hemnaath_Balasubramani_", "Hemnaath Balasubramani "):
        if stem.startswith(prefix):
            stem = stem[len(prefix) :]
    return " ".join(stem.replace("_", " ").replace("-", " ").split()) or "Imported Resume"


@router.post("/{resume_id}/versions/tailor", response_model=TailorResponse, status_code=201)
async def tailor_version(
    resume_id: UUID,
    payload: TailorRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> TailorResponse:
    """Run the tailoring agent against `payload.job_id` and persist the result
    as a new ResumeVersion under the requested role-specific Resume (SWE, ML,
    etc.). The baseline is always the master Resume's latest version — never
    a previously tailored variant — so each tailor run starts from a clean
    no-hallucination baseline. The new version is unapproved; the user
    reviews via `/versions/{id}` and either approves or re-tailors.
    """
    from job_os.services.tailor import tailor_resume

    resume = await _load_resume(session, resume_id, user)

    job = await session.get(Job, payload.job_id)
    if job is None:
        raise HTTPException(404, "job not found")

    master = (
        await session.execute(
            select(Resume).where(Resume.user_id == user.id, Resume.is_master.is_(True))
        )
    ).scalar_one_or_none()
    if master is None:
        raise HTTPException(
            409, "No master resume found — create one (is_master=true) and import a JSON Resume."
        )

    baseline = (
        await session.execute(
            select(ResumeVersion)
            .where(
                ResumeVersion.resume_id == master.id,
                ResumeVersion.archived_at.is_(None),
            )
            .order_by(ResumeVersion.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if baseline is None:
        raise HTTPException(
            409,
            "Master resume has no baseline version yet — import a JSON Resume into the master "
            "resume first (POST /profile/import).",
        )

    (
        json_resume,
        provenance,
        gap_questions,
        ats_score,
        ats_report,
        agent_note,
    ) = await tailor_resume(
        session, user=user, resume=resume, master_version=baseline, job=job
    )

    version = ResumeVersion(
        resume_id=resume_id,
        json_resume=json_resume,
        spawned_from_job_id=job.id,
        provenance=[p.model_dump(mode="json") for p in provenance],
        ats_score=ats_score,
        ats_report=ats_report,
        approved_by_user=False,
    )
    session.add(version)
    await session.flush()

    # A separate quality-model pass verifies every AI-generated draft before
    # it is shown to the user. The same pass performs deterministic one-page
    # and selectable-text checks, then caches the resulting PDF.
    try:
        from job_os.services.resume_engine import generate_latex_source, review_resume

        review, pdf_bytes = await review_resume(version.json_resume)
        version.review_score = review.score
        version.review_report = review.model_dump(mode="json")
        version.status = "reviewed" if review.passed else "needs_changes"
        version.pdf_bytes = pdf_bytes
        version.latex_source = generate_latex_source(version.json_resume)
        await session.flush()
    except Exception as e:  # noqa: BLE001 — render failure is non-fatal
        from structlog import get_logger

        version.status = "needs_changes"
        version.review_score = None
        version.review_report = {
            "passed": False,
            "issues": [
                {
                    "severity": "blocking",
                    "code": "review_unavailable",
                    "message": (
                        "The independent quality review could not complete. "
                        "Run Review in the resume editor before finalizing."
                    ),
                }
            ],
        }
        get_logger(__name__).warning(
            "tailor.review_failed",
            version_id=str(version.id),
            error=str(e),
        )

    return TailorResponse(
        id=version.id,
        created_at=version.created_at,
        updated_at=version.updated_at,
        resume_id=version.resume_id,
        spawned_from_job_id=version.spawned_from_job_id,
        spawned_from_application_id=version.spawned_from_application_id,
        ats_score=version.ats_score,
        ats_report=version.ats_report,
        approved_by_user=version.approved_by_user,
        pdf_r2_key=version.pdf_r2_key,
        docx_r2_key=version.docx_r2_key,
        json_resume=version.json_resume,
        provenance=version.provenance,
        status=version.status,
        review_score=version.review_score,
        review_report=version.review_report,
        parent_version_id=version.parent_version_id,
        source_filename=version.source_filename,
        revision_note=version.revision_note,
        finalized_at=version.finalized_at,
        latex_source=version.latex_source,
        gap_questions=gap_questions,
        agent_note=agent_note,
    )
