import json
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from job_os.auth import get_current_user
from job_os.db.models import FactBullet, ProfileFact, User
from job_os.db.session import get_session
from job_os.schemas.profile import (
    FactBulletCreate,
    FactBulletRead,
    ImportReport,
    JsonResumeImport,
    ProfileFactCreate,
    ProfileFactPatch,
    ProfileFactRead,
)
from job_os.settings import get_settings

router = APIRouter(prefix="/profile")


@router.get("/facts", response_model=list[ProfileFactRead])
async def list_facts(
    *,
    kind: str | None = Query(default=None),
    verified: bool | None = Query(default=None),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[ProfileFact]:
    stmt = select(ProfileFact).where(ProfileFact.user_id == user.id)
    if kind:
        stmt = stmt.where(ProfileFact.kind == kind)
    if verified is not None:
        stmt = stmt.where(ProfileFact.verified == verified)
    stmt = stmt.order_by(ProfileFact.kind, ProfileFact.start_date.desc().nullslast())
    result = await session.execute(stmt)
    return list(result.scalars().all())


@router.post("/facts", response_model=ProfileFactRead, status_code=201)
async def create_fact(
    payload: ProfileFactCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ProfileFact:
    from job_os.services.embeddings import embed_one

    fact = ProfileFact(
        user_id=user.id,
        kind=payload.kind,
        title=payload.title,
        org=payload.org,
        start_date=payload.start_date,
        end_date=payload.end_date,
        location=payload.location,
        payload=payload.payload,
        verified=payload.verified,
        source_url=payload.source_url,
    )
    session.add(fact)
    await session.flush()

    for b in payload.bullets:
        bullet = FactBullet(
            fact_id=fact.id,
            text=b.text,
            target_role=b.target_role,
            metric_verified=b.metric_verified,
            embedding=await embed_one(b.text),
        )
        session.add(bullet)

    await session.refresh(fact, attribute_names=["bullets"])
    return fact


@router.patch("/facts/{fact_id}", response_model=ProfileFactRead)
async def patch_fact(
    fact_id: UUID,
    payload: ProfileFactPatch,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ProfileFact:
    fact = await _load_fact(session, fact_id, user)
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(fact, k, v)
    await session.flush()
    return fact


@router.delete("/facts/{fact_id}", status_code=204)
async def delete_fact(
    fact_id: UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    fact = await _load_fact(session, fact_id, user)
    await session.delete(fact)


@router.post("/facts/{fact_id}/bullets", response_model=FactBulletRead, status_code=201)
async def add_bullet(
    fact_id: UUID,
    payload: FactBulletCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> FactBullet:
    from job_os.services.embeddings import embed_one

    fact = await _load_fact(session, fact_id, user)
    bullet = FactBullet(
        fact_id=fact.id,
        text=payload.text,
        target_role=payload.target_role,
        metric_verified=payload.metric_verified,
        embedding=await embed_one(payload.text),
    )
    session.add(bullet)
    await session.flush()
    return bullet


@router.delete("/bullets/{bullet_id}", status_code=204)
async def delete_bullet(
    bullet_id: UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    result = await session.execute(
        select(FactBullet)
        .join(ProfileFact, FactBullet.fact_id == ProfileFact.id)
        .where(FactBullet.id == bullet_id, ProfileFact.user_id == user.id)
    )
    bullet = result.scalar_one_or_none()
    if bullet is None:
        raise HTTPException(404, "bullet not found")
    await session.delete(bullet)


@router.post("/import/json-resume", response_model=ImportReport)
async def import_resume(
    payload: JsonResumeImport,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ImportReport:
    from job_os.services.profile_import import import_json_resume

    settings = get_settings()
    doc: dict[str, Any] | None = payload.json_resume

    if doc is None and payload.server_path:
        if not settings.is_dev:
            raise HTTPException(400, "server_path import only allowed in dev mode")
        path = Path(payload.server_path).expanduser()  # noqa: ASYNC240
        if not path.is_file():
            raise HTTPException(400, f"file not found: {path}")
        doc = json.loads(path.read_text())

    if doc is None:
        raise HTTPException(400, "provide json_resume or server_path")

    return await import_json_resume(
        session,
        user=user,
        doc=doc,
        mark_verified=payload.mark_verified,
        replace_existing=payload.replace_existing,
    )


@router.post("/upload-resume", response_model=ImportReport)
async def upload_resume(
    file: UploadFile = File(...),
    mark_verified: bool = True,
    replace_existing: bool = False,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ImportReport:
    """Accept a PDF, DOCX, or JSON resume and import it into the profile.

    Claude reads the file directly (PDF as a document block; DOCX text-extracted
    first); returns a JSON Resume document which we then run through the same
    import path as `/profile/import/json-resume`.
    """
    content = await file.read()
    if not content:
        raise HTTPException(400, "empty file")

    name = (file.filename or "").lower()
    ctype = (file.content_type or "").lower()

    if name.endswith(".json") or "json" in ctype:
        doc = json.loads(content.decode("utf-8"))
    elif name.endswith(".pdf") or "pdf" in ctype:
        from job_os.services.profile_extract import extract_json_resume_from_pdf

        doc = await extract_json_resume_from_pdf(content)
    elif name.endswith(".docx") or "wordprocessingml" in ctype:
        from job_os.services.profile_extract import extract_json_resume_from_docx

        doc = await extract_json_resume_from_docx(content)
    else:
        raise HTTPException(400, f"unsupported file type: {file.content_type} / {file.filename}")

    from job_os.services.profile_import import import_json_resume

    return await import_json_resume(
        session,
        user=user,
        doc=doc,
        mark_verified=mark_verified,
        replace_existing=replace_existing,
    )


async def _load_fact(session: AsyncSession, fact_id: UUID, user: User) -> ProfileFact:
    result = await session.execute(
        select(ProfileFact).where(
            ProfileFact.id == fact_id, ProfileFact.user_id == user.id
        )
    )
    fact = result.scalar_one_or_none()
    if fact is None:
        raise HTTPException(404, "fact not found")
    return fact
