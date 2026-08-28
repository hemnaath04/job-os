"""Cover letters: generate, version, edit, render.

Same shape as the resumes router. A letter is a container plus an append-only
list of versions, generation always starts from the master resume's baseline
rather than from a previous letter, and a hand edit lands as a new version with
the one it came from recorded as its parent.
"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from job_os.auth import get_current_user
from job_os.db.models import (
    CoverLetter,
    CoverLetterVersion,
    Job,
    Resume,
    ResumeVersion,
    User,
)
from job_os.db.session import get_session
from job_os.schemas.cover_letters import (
    CoverLetterEditRequest,
    CoverLetterGenerateRequest,
    CoverLetterRead,
    CoverLetterVersionRead,
    CoverLetterVersionSummary,
)

router = APIRouter(prefix="/cover-letters")


@router.get("", response_model=list[CoverLetterRead])
async def list_cover_letters(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[CoverLetter]:
    result = await session.execute(
        select(CoverLetter)
        .where(CoverLetter.user_id == user.id, CoverLetter.archived_at.is_(None))
        .order_by(CoverLetter.updated_at.desc())
    )
    return list(result.scalars().all())


@router.post("/generate", response_model=CoverLetterVersionRead, status_code=201)
async def generate_version(
    payload: CoverLetterGenerateRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> CoverLetterVersionRead:
    """Write a cover letter for one job from the verified fact vault.

    The baseline is always the master resume's latest version, never a previous
    letter, so every run starts from a clean no-fabrication base. That is the same
    rule the tailoring endpoint follows and for the same reason: regenerating from
    generated text is how a small overstatement compounds into a large one.

    Passing `parent_version_id` records this run as a revision of that version,
    which is what gives a letter history instead of a single mutable draft.
    """
    from job_os.services.cover_letter import generate_cover_letter

    job = await session.get(Job, payload.job_id)
    if job is None:
        raise HTTPException(404, "job not found")

    master = (
        await session.execute(
            select(Resume).where(
                Resume.user_id == user.id,
                Resume.is_master.is_(True),
                Resume.archived_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if master is None:
        raise HTTPException(
            409,
            "No master resume found. A cover letter is written from the same "
            "verified profile the resume is, so import one first.",
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
        # Named the endpoint the user was supposed to POST to, which is an
        # instruction for whoever wrote this rather than for whoever is trying
        # to send a letter.
        raise HTTPException(
            409,
            "Your master resume has no content yet. Upload or import a resume "
            "into it on the Resumes page, then write the letter.",
        )

    parent: CoverLetterVersion | None = None
    if payload.parent_version_id is not None:
        parent = await _load_version_by_id(session, payload.parent_version_id, user)

    try:
        result = await generate_cover_letter(
            session,
            user=user,
            job=job,
            master_version=baseline,
            tone=payload.tone,
            recipient_name=payload.recipient_name,
        )
    except ValueError as exc:
        # The vault is empty, which is a state the user can fix and not a bug.
        raise HTTPException(409, str(exc)) from exc

    parent_letter = None
    if parent is not None:
        parent_letter = await session.get(CoverLetter, parent.cover_letter_id)
    letter = parent_letter or await _letter_for_job(session, user=user, job=job)

    template_key = payload.template_key or _resume_template_key(baseline)
    version = CoverLetterVersion(
        cover_letter_id=letter.id,
        document=result.document.model_dump(mode="json"),
        provenance=[row.model_dump(mode="json") for row in result.provenance],
        gap_questions=[gap.model_dump(mode="json") for gap in result.gap_questions],
        refused=[row.model_dump(mode="json") for row in result.refused],
        quality_flags=result.quality_flags,
        tone=result.tone,
        template_key=template_key,
        word_count=result.document.word_count,
        agent_note=result.agent_note,
        spawned_from_job_id=job.id,
        parent_version_id=parent.id if parent else None,
        revision_note=payload.revision_note,
        status="draft",
    )
    session.add(version)
    await session.flush()

    # Render now rather than on the first download. The letter is the thing the
    # user is about to read, the page count is part of whether it is finished, and
    # a Typst compile is milliseconds. A render failure is not fatal: the letter
    # is still worth showing and the download path can retry.
    _render_into(version)
    await session.flush()
    return _read(version)


@router.get("/{letter_id}/versions", response_model=list[CoverLetterVersionSummary])
async def list_versions(
    letter_id: UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[CoverLetterVersion]:
    await _load_letter(session, letter_id, user)
    result = await session.execute(
        select(CoverLetterVersion)
        .where(
            CoverLetterVersion.cover_letter_id == letter_id,
            CoverLetterVersion.archived_at.is_(None),
        )
        .order_by(CoverLetterVersion.created_at.desc())
    )
    return list(result.scalars().all())


@router.get("/{letter_id}/versions/{version_id}", response_model=CoverLetterVersionRead)
async def get_version(
    letter_id: UUID,
    version_id: UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> CoverLetterVersionRead:
    return _read(await _load_version(session, letter_id, version_id, user))


@router.post(
    "/{letter_id}/versions/{version_id}/edit",
    response_model=CoverLetterVersionRead,
    status_code=201,
)
async def edit_version(
    letter_id: UUID,
    version_id: UUID,
    payload: CoverLetterEditRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> CoverLetterVersionRead:
    """Save a hand-edited letter as a new version.

    The edit is never rejected: it is the user's letter. What it cannot do is
    inherit a provenance row that no longer describes the sentence, so rows
    survive only for sentences whose text is unchanged. See
    `cover_letter.revalidate_edited_letter`.
    """
    from job_os.schemas.cover_letters import (
        CoverLetterDocument,
        CoverLetterProvenanceEntry,
    )
    from job_os.services.cover_letter import load_verified_vault, revalidate_edited_letter

    previous = await _load_version(session, letter_id, version_id, user)
    facts, bullets_by_fact = await load_verified_vault(session, user.id)
    result = revalidate_edited_letter(
        CoverLetterDocument.model_validate(previous.document),
        paragraphs=payload.paragraphs,
        provenance=[
            CoverLetterProvenanceEntry.model_validate(row)
            for row in (previous.provenance or [])
        ],
        facts=facts,
        bullets_by_fact=bullets_by_fact,
    )
    version = CoverLetterVersion(
        cover_letter_id=letter_id,
        document=result.document.model_dump(mode="json"),
        provenance=[row.model_dump(mode="json") for row in result.provenance],
        # Gaps belong to the generation that found them, so an edit carries them
        # forward rather than silently clearing them.
        gap_questions=list(previous.gap_questions or []),
        # An edit's refusals mean something weaker than a generation's: the
        # sentence is still printed, it just has nothing behind it. Stored rather
        # than dropped, because a claim the vault cannot back is exactly what the
        # user should be told about before they send the letter.
        refused=[row.model_dump(mode="json") for row in result.refused],
        quality_flags=result.quality_flags,
        tone=previous.tone,
        template_key=previous.template_key,
        word_count=result.document.word_count,
        agent_note=result.agent_note,
        spawned_from_job_id=previous.spawned_from_job_id,
        parent_version_id=previous.id,
        revision_note=payload.note,
        status="draft",
    )
    session.add(version)
    await session.flush()
    _render_into(version)
    await session.flush()
    return _read(version)


@router.post(
    "/{letter_id}/versions/{version_id}/approve", response_model=CoverLetterVersionRead
)
async def approve_version(
    letter_id: UUID,
    version_id: UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> CoverLetterVersionRead:
    """Mark a letter as the one being sent.

    Gated on the render, not on the writing. A letter that has not rendered has no
    proven page count and no proven text layer, and approving one would be
    approving something nobody has seen.
    """
    from datetime import UTC, datetime

    version = await _load_version(session, letter_id, version_id, user)
    if not version.pdf_bytes:
        _render_into(version)
    if not version.pdf_bytes:
        raise HTTPException(
            409,
            "This letter has not rendered yet, so its page count and text layer "
            "are unverified. Re-open it to render, then approve.",
        )
    version.approved_by_user = True
    version.status = "final"
    version.finalized_at = datetime.now(UTC)
    await session.flush()
    return _read(version)


@router.get("/{letter_id}/versions/{version_id}/download")
async def download_version(
    letter_id: UUID,
    version_id: UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Stream the rendered PDF, rendering on a cache miss and keeping the bytes."""
    version = await _load_version(session, letter_id, version_id, user)
    if not version.pdf_bytes:
        _render_into(version)
        await session.flush()
    if not version.pdf_bytes:
        raise HTTPException(
            503, "This runtime could not render the letter. Try again shortly."
        )
    return Response(
        content=bytes(version.pdf_bytes),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="cover_letter_{version_id}.pdf"',
        },
    )


@router.delete("/{letter_id}", status_code=204)
async def archive_cover_letter(
    letter_id: UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    from datetime import UTC, datetime

    letter = await _load_letter(session, letter_id, user)
    letter.archived_at = datetime.now(UTC)
    await session.flush()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _load_letter(session: AsyncSession, letter_id: UUID, user: User) -> CoverLetter:
    letter = await session.get(CoverLetter, letter_id)
    if letter is None or letter.user_id != user.id or letter.archived_at is not None:
        raise HTTPException(404, "cover letter not found")
    return letter


async def _load_version(
    session: AsyncSession, letter_id: UUID, version_id: UUID, user: User
) -> CoverLetterVersion:
    await _load_letter(session, letter_id, user)
    version = await session.get(CoverLetterVersion, version_id)
    if (
        version is None
        or version.cover_letter_id != letter_id
        or version.archived_at is not None
    ):
        raise HTTPException(404, "version not found")
    return version


async def _load_version_by_id(
    session: AsyncSession, version_id: UUID, user: User
) -> CoverLetterVersion:
    """A version reached without its letter id, ownership still checked.

    `parent_version_id` arrives on its own in a generate request, so the check
    cannot come from the path. It still has to happen: an id from a request body
    is not proof that the caller owns the row.
    """
    version = await session.get(CoverLetterVersion, version_id)
    if version is None or version.archived_at is not None:
        raise HTTPException(404, "parent version not found")
    await _load_letter(session, version.cover_letter_id, user)
    return version


async def _letter_for_job(
    session: AsyncSession, *, user: User, job: Job
) -> CoverLetter:
    """The letter this job's versions belong to, created on first generation.

    One letter per job, so regenerating in a different tone adds a version to the
    same history rather than starting a second, competing one.
    """
    existing = (
        await session.execute(
            select(CoverLetter).where(
                CoverLetter.user_id == user.id,
                CoverLetter.job_id == job.id,
                CoverLetter.archived_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    company = str((job.jd_parsed or {}).get("company") or "").strip()
    if not company and job.company is not None:
        company = str(job.company.name or "").strip()
    base = " at ".join(part for part in (job.title, company) if part) or "Cover letter"
    # Trimmed to the column's practical width. Uniqueness is enforced by
    # `uq_cover_letters_user_name`, and this is only reached when no letter for
    # this job exists yet, so a suffix is not needed for the ordinary case. A
    # collision on the same title at the same company surfaces as an integrity
    # error the client can report, which is what the resumes router does too.
    letter = CoverLetter(user_id=user.id, name=base[:200], job_id=job.id)
    session.add(letter)
    await session.flush()
    return letter


def _resume_template_key(baseline: ResumeVersion) -> str | None:
    """Which resume look this user's documents use, if the baseline recorded one.

    Read from the review report rather than stored twice. Nothing here invents a
    default: `cover_letter_render.style_for` owns that decision, so a letter with
    no recorded template still renders in the app default look.
    """
    report = baseline.review_report or {}
    key = report.get("template_key")
    return str(key) if key else None


def _render_into(version: CoverLetterVersion) -> None:
    """Render the letter and cache the bytes, recording problems on the version.

    Never raises. A letter that failed to render is still a letter the user should
    be able to read and edit, and the alternative is a 500 on the one endpoint
    that was supposed to hand them their draft.
    """
    from job_os.schemas.cover_letters import CoverLetterDocument
    from job_os.services.cover_letter_render import render_cover_letter_pdf

    try:
        rendered = render_cover_letter_pdf(
            CoverLetterDocument.model_validate(version.document),
            template_key=version.template_key,
        )
    except Exception as exc:  # noqa: BLE001 - a render failure must not fail the draft
        from structlog import get_logger

        get_logger(__name__).warning(
            "cover_letter.render_failed",
            version_id=str(version.id),
            error=str(exc)[:300],
        )
        flags = dict(version.quality_flags or {})
        flags["render"] = ["render_unavailable"]
        version.quality_flags = flags
        return
    version.pdf_bytes = rendered.bytes_
    flags = dict(version.quality_flags or {})
    flags.pop("render", None)
    if rendered.text_layer_issues:
        flags["render"] = list(rendered.text_layer_issues)
    version.quality_flags = flags


def _read(version: CoverLetterVersion) -> CoverLetterVersionRead:
    return CoverLetterVersionRead(
        id=version.id,
        created_at=version.created_at,
        updated_at=version.updated_at,
        cover_letter_id=version.cover_letter_id,
        spawned_from_job_id=version.spawned_from_job_id,
        spawned_from_application_id=version.spawned_from_application_id,
        parent_version_id=version.parent_version_id,
        status=version.status,
        tone=version.tone,
        template_key=version.template_key,
        word_count=version.word_count,
        approved_by_user=version.approved_by_user,
        revision_note=version.revision_note,
        finalized_at=version.finalized_at,
        archived_at=version.archived_at,
        document=version.document,
        provenance=list(version.provenance or []),
        gap_questions=list(version.gap_questions or []),
        refused=list(version.refused or []),
        quality_flags=dict(version.quality_flags or {}),
        agent_note=version.agent_note or "",
    )
