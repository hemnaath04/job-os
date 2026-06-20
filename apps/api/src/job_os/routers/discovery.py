"""Discovery feed (M4) — TheirStack first, one-click import to `jobs`.

The router only orchestrates: TheirStack lookup, dedupe annotation against
the user's existing jobs, and a separate import call that does the same
LLM-parse + Job-create dance as `POST /jobs/from-url`, but skips the
external fetch because TheirStack already gave us the JD text.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from job_os.auth import get_current_user
from job_os.db.models import Job, User
from job_os.db.session import get_session
from job_os.integrations.theirstack import (
    TheirStackUnavailableError,
    search_jobs,
)
from job_os.schemas.discovery import (
    DiscoveryImportRequest,
    DiscoveryResult,
    DiscoverySearchRequest,
)
from job_os.schemas.jobs import JobRead
from job_os.services.companies import upsert_company
from job_os.services.jd_parse import parse_jd

router = APIRouter(prefix="/discovery")


@router.post("/search", response_model=list[DiscoveryResult])
async def search(
    payload: DiscoverySearchRequest,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[DiscoveryResult]:
    try:
        results = await search_jobs(
            title_keywords=payload.title_keywords or None,
            description_keywords=payload.description_keywords or None,
            country_codes=payload.country_codes or None,
            technology_slugs=payload.technology_slugs or None,
            max_age_days=payload.max_age_days,
            limit=payload.limit,
            page=payload.page,
        )
    except TheirStackUnavailableError as e:
        raise HTTPException(503, str(e)) from e

    # Annotate which results we already have (so the FE can hide the Import
    # button or show a "Go to job" link instead).
    source_ids = [r.source_id for r in results if r.source_id]
    existing: set[str] = set()
    if source_ids:
        rows = await session.execute(
            select(Job.source_id).where(
                Job.source == "theirstack", Job.source_id.in_(source_ids)
            )
        )
        existing = {row[0] for row in rows.all() if row[0]}

    return [
        DiscoveryResult(
            source="theirstack",
            source_id=r.source_id,
            source_url=r.source_url,
            title=r.title,
            company_name=r.company_name,
            company_domain=r.company_domain,
            location=r.location,
            country_code=r.country_code,
            posted_at=r.posted_at,
            description=r.description,
            technologies=r.technologies,
            already_imported=r.source_id in existing,
        )
        for r in results
    ]


@router.post("/import", response_model=JobRead, status_code=201)
async def import_result(
    payload: DiscoveryImportRequest,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Job:
    # Dedup: if a Job with this (source, source_id) already exists, return it.
    if payload.source_id:
        existing_q = await session.execute(
            select(Job)
            .options(joinedload(Job.company))
            .where(Job.source == payload.source, Job.source_id == payload.source_id)
        )
        existing = existing_q.unique().scalar_one_or_none()
        if existing:
            return existing

    parsed = await parse_jd(payload.description, title_hint=payload.title)
    company = await upsert_company(
        session,
        name=payload.company_name or parsed.get("company") or "Unknown",
        domain=payload.company_domain or parsed.get("company_domain"),
    )

    job = Job(
        company_id=company.id,
        title=parsed.get("title") or payload.title,
        level=parsed.get("level"),
        function=parsed.get("function"),
        location=payload.location or parsed.get("location"),
        remote=parsed.get("remote"),
        salary_min=parsed.get("salary_min"),
        salary_max=parsed.get("salary_max"),
        salary_currency=parsed.get("salary_currency") or "USD",
        jd_raw=payload.description,
        jd_clean=payload.description,
        jd_parsed=parsed,
        source=payload.source,
        source_id=payload.source_id or None,
        source_url=payload.source_url or None,
        posted_at=payload.posted_at,
    )
    session.add(job)
    await session.flush()
    await session.refresh(job, attribute_names=["company"])
    return job
