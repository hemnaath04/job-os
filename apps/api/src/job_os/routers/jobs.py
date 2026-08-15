from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from job_os.auth import get_current_user
from job_os.db.models import Job, User
from job_os.db.session import get_session
from job_os.schemas.jobs import JobCreateManual, JobFromText, JobFromUrl, JobRead
from job_os.services.companies import upsert_company

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/jobs")


async def _load_job(session: AsyncSession, job_id: UUID) -> Job | None:
    """Fetch a job with company relationship eagerly loaded (async-safe)."""
    result = await session.execute(
        select(Job).options(joinedload(Job.company)).where(Job.id == job_id)
    )
    return result.unique().scalar_one_or_none()


@router.get("", response_model=list[JobRead])
async def list_jobs(
    *,
    q: str | None = None,
    function: str | None = None,
    level: str | None = None,
    location: str | None = None,
    active: bool = True,
    limit: int = Query(default=50, le=200),
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[Job]:
    stmt = select(Job).where(Job.active == active)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(Job.title.ilike(like))
    if function:
        stmt = stmt.where(Job.function == function)
    if level:
        stmt = stmt.where(Job.level == level)
    if location:
        stmt = stmt.where(Job.location.ilike(f"%{location}%"))
    stmt = stmt.order_by(Job.posted_at.desc().nullslast(), Job.created_at.desc()).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


@router.get("/{job_id}", response_model=JobRead)
async def get_job(
    job_id: UUID,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Job:
    job = await _load_job(session, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return job


@router.post("/manual", response_model=JobRead, status_code=201)
async def create_manual(
    payload: JobCreateManual,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Job:
    from job_os.services.jd_parse import parse_jd

    company = await upsert_company(
        session, name=payload.company_name, domain=payload.company_domain
    )
    parsed = await parse_jd(payload.jd_text, title_hint=payload.title)
    job = Job(
        company_id=company.id,
        title=payload.title,
        level=payload.level or parsed.get("level"),
        function=payload.function or parsed.get("function"),
        location=payload.location,
        remote=payload.remote,
        jd_raw=payload.jd_text,
        jd_clean=payload.jd_text,
        jd_parsed=parsed,
        source="manual",
        source_url=str(payload.source_url) if payload.source_url else None,
    )
    session.add(job)
    await session.flush()
    await session.refresh(job, attribute_names=["company"])
    return job


@router.post("/from-url", response_model=JobRead, status_code=201)
async def create_from_url(
    payload: JobFromUrl,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Job:
    from job_os.integrations.firecrawl import fetch_url_markdown
    from job_os.services.jd_parse import parse_jd

    url = str(payload.url)

    # Same dedup this app already does correctly for discovery imports
    # (discovery.py's import_result, keyed on source_id there). A URL paste
    # has no vendor id to key on, so source_url is the equivalent identity.
    # Without this, `source_id IS NULL` on every row here, and Postgres does
    # not treat NULL = NULL, so the unique constraint on (source, source_id)
    # never once caught a repeat paste of the same link.
    existing_q = await session.execute(
        select(Job)
        .options(joinedload(Job.company))
        .where(Job.source == "url", Job.source_url == url)
    )
    existing = existing_q.unique().scalar_one_or_none()
    if existing:
        return existing

    try:
        fetched = await fetch_url_markdown(url)
    except Exception as e:
        log.warning("jobs.from_url.fetch_failed", url=url, error=str(e))
        raise HTTPException(
            502,
            "Could not fetch that job posting right now — the fetch service is "
            "temporarily unavailable. Try again in a moment, or use "
            "'Paste the description' instead.",
        ) from e

    parsed = await parse_jd(fetched.markdown, title_hint=fetched.title)

    company_name = parsed.get("company") or fetched.company_hint or "Unknown"
    domain = parsed.get("company_domain")
    company = await upsert_company(session, name=company_name, domain=domain)

    job = Job(
        company_id=company.id,
        title=parsed.get("title") or fetched.title or "Untitled",
        level=parsed.get("level"),
        function=parsed.get("function"),
        location=parsed.get("location"),
        remote=parsed.get("remote"),
        salary_min=parsed.get("salary_min"),
        salary_max=parsed.get("salary_max"),
        salary_currency=parsed.get("salary_currency") or "USD",
        jd_raw=fetched.raw,
        jd_clean=fetched.markdown,
        jd_parsed=parsed,
        source="url",
        source_url=url,
    )
    session.add(job)
    await session.flush()
    await session.refresh(job, attribute_names=["company"])
    return job


@router.post("/from-text", response_model=JobRead, status_code=201)
async def create_from_text(
    payload: JobFromText,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Job:
    from job_os.services.jd_parse import parse_jd

    source_url = str(payload.source_url) if payload.source_url else None

    # Same reasoning as create_from_url: source_id is never set here either,
    # so the DB constraint alone never caught a repeat paste. A source_url,
    # when the user gave one, is the strongest identity available. Lacking
    # that, the pasted text itself is the only identity a raw JD paste has.
    dedup_clause = (
        Job.source_url == source_url
        if source_url
        else Job.jd_clean == payload.jd_text
    )
    existing_q = await session.execute(
        select(Job)
        .options(joinedload(Job.company))
        .where(Job.source == "text", dedup_clause)
    )
    existing = existing_q.unique().scalar_one_or_none()
    if existing:
        return existing

    parsed = await parse_jd(payload.jd_text)
    company = await upsert_company(
        session,
        name=parsed.get("company") or payload.company_hint or "Unknown",
        domain=parsed.get("company_domain"),
    )
    job = Job(
        company_id=company.id,
        title=parsed.get("title") or "Untitled",
        level=parsed.get("level"),
        function=parsed.get("function"),
        location=parsed.get("location"),
        remote=parsed.get("remote"),
        jd_raw=payload.jd_text,
        jd_clean=payload.jd_text,
        jd_parsed=parsed,
        source="text",
        source_url=str(payload.source_url) if payload.source_url else None,
    )
    session.add(job)
    await session.flush()
    await session.refresh(job, attribute_names=["company"])
    return job
