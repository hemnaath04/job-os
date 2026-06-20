from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from job_os.auth import get_current_user
from job_os.db.models import Resume, ResumeVersion, User
from job_os.db.session import get_session
from job_os.integrations import r2
from job_os.schemas.resumes import (
    ExportRequest,
    ExportResult,
    ResumeCreate,
    ResumeRead,
    ResumeVersionCreate,
    ResumeVersionRead,
    ResumeVersionSummary,
)
from job_os.services.pdf_render import render_resume_pdf

router = APIRouter(prefix="/resumes")


@router.get("", response_model=list[ResumeRead])
async def list_resumes(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[Resume]:
    result = await session.execute(
        select(Resume).where(Resume.user_id == user.id).order_by(Resume.is_master.desc(), Resume.name)
    )
    return list(result.scalars().all())


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
    resume = Resume(
        user_id=user.id,
        name=payload.name,
        base_role=payload.base_role,
        is_master=payload.is_master,
    )
    session.add(resume)
    await session.flush()
    return resume


@router.get("/{resume_id}/versions", response_model=list[ResumeVersionSummary])
async def list_versions(
    resume_id: UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[ResumeVersion]:
    await _load_resume(session, resume_id, user)
    result = await session.execute(
        select(ResumeVersion)
        .where(ResumeVersion.resume_id == resume_id)
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
    await _load_resume(session, resume_id, user)
    version = ResumeVersion(
        resume_id=resume_id,
        json_resume=payload.json_resume,
        spawned_from_job_id=payload.spawned_from_job_id,
        spawned_from_application_id=payload.spawned_from_application_id,
        provenance=payload.provenance,
        ats_score=payload.ats_score,
        ats_report=payload.ats_report,
    )
    session.add(version)
    await session.flush()
    return version


@router.get("/{resume_id}/versions/{version_id}", response_model=ResumeVersionRead)
async def get_version(
    resume_id: UUID,
    version_id: UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ResumeVersion:
    await _load_resume(session, resume_id, user)
    version = await session.get(ResumeVersion, version_id)
    if version is None or version.resume_id != resume_id:
        raise HTTPException(404, "version not found")
    return version


@router.post("/{resume_id}/versions/{version_id}/export", response_model=ExportResult)
async def export_version(
    resume_id: UUID,
    version_id: UUID,
    payload: ExportRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ExportResult:
    """Render the version's JSON Resume to PDF via WeasyPrint, push to R2 if
    configured (otherwise just report the rendered byte count)."""
    await _load_resume(session, resume_id, user)
    version = await session.get(ResumeVersion, version_id)
    if version is None or version.resume_id != resume_id:
        raise HTTPException(404, "version not found")

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
            note=f"Rendered {len(rendered.bytes_)} bytes — R2 not configured, use /download instead.",
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
    """Stream the rendered PDF directly — useful for previewing without R2."""
    await _load_resume(session, resume_id, user)
    version = await session.get(ResumeVersion, version_id)
    if version is None or version.resume_id != resume_id:
        raise HTTPException(404, "version not found")
    rendered = render_resume_pdf(version.json_resume)
    return Response(
        content=rendered.bytes_,
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
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ResumeVersion:
    """Upload a pre-built tailored PDF/DOCX as a new version of a resume.

    The file is stored verbatim in R2 (if configured); `json_resume` is set to
    a minimal stub `{ "uploaded": True, "filename": ... }` since we don't
    extract on upload — the user provided the final artifact and we trust it.
    """
    await _load_resume(session, resume_id, user)
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


@router.post("/{resume_id}/versions/{version_id}/approve", response_model=ResumeVersionRead)
async def approve_version(
    resume_id: UUID,
    version_id: UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ResumeVersion:
    await _load_resume(session, resume_id, user)
    version = await session.get(ResumeVersion, version_id)
    if version is None or version.resume_id != resume_id:
        raise HTTPException(404, "version not found")
    version.approved_by_user = True
    return version


async def _load_resume(session: AsyncSession, resume_id: UUID, user: User) -> Resume:
    resume = await session.get(Resume, resume_id)
    if resume is None or resume.user_id != user.id:
        raise HTTPException(404, "resume not found")
    return resume
