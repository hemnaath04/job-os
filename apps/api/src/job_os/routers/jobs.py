from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from job_os.auth import get_current_user
from job_os.db.models import Job, User
from job_os.db.session import get_session
from job_os.schemas.jobs import JobCreateManual, JobFromText, JobFromUrl, JobRead
from job_os.services.companies import upsert_company

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
    fetched = await fetch_url_markdown(url)
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
