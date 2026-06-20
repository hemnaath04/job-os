"""Discovery feed (M4) — TheirStack + GitHub (SimplifyJobs) sources.

The router fans out to each requested source in parallel, merges the results,
dedupes against the user's existing jobs by (source, source_id), and sorts by
posted_at desc before applying the final limit.

Import path: for sources that ship a real JD (TheirStack), the description
goes straight to `services.jd_parse`. For sources that only ship a link
(GitHub), we run Firecrawl on the source_url first to grab the JD text.
"""
import asyncio
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from job_os.auth import get_current_user
from job_os.db.models import Job, SavedSearch, User
from job_os.db.session import get_session
from job_os.integrations import github_jobs
from job_os.integrations.firecrawl import fetch_url_markdown
from job_os.integrations.theirstack import (
    TheirStackUnavailableError,
)
from job_os.integrations.theirstack import (
    search_jobs as theirstack_search,
)
from job_os.schemas.discovery import (
    DiscoveryImportRequest,
    DiscoveryResult,
    DiscoverySearchRequest,
    SavedSearchCreate,
    SavedSearchRead,
)
from job_os.schemas.jobs import JobRead
from job_os.services.companies import upsert_company
from job_os.services.jd_parse import parse_jd

router = APIRouter(prefix="/discovery")

_MIN_DESCRIPTION_CHARS = 200


@router.post("/search", response_model=list[DiscoveryResult])
async def search(
    payload: DiscoverySearchRequest,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[DiscoveryResult]:
    return await _run_search(payload, session)


async def _run_search(
    payload: DiscoverySearchRequest, session: AsyncSession
) -> list[DiscoveryResult]:
    if not payload.sources:
        raise HTTPException(400, "sources list cannot be empty")

    tasks = []
    if "theirstack" in payload.sources:
        tasks.append(_search_theirstack(payload))
    if "github" in payload.sources:
        tasks.append(_search_github(payload))

    gathered = await asyncio.gather(*tasks, return_exceptions=True)

    combined: list[DiscoveryResult] = []
    errors: list[str] = []
    for res in gathered:
        if isinstance(res, BaseException):
            errors.append(str(res))
            continue
        combined.extend(res)

    if combined:
        await _annotate_already_imported(session, combined)

    # Sort by posted_at desc, nulls last — then cap to `limit` across sources.
    combined.sort(
        key=lambda r: (r.posted_at is None, -(r.posted_at.timestamp() if r.posted_at else 0))
    )
    capped = combined[: payload.limit]

    # If every source we asked for blew up and we have nothing, surface a
    # sensible error so the FE doesn't show a blank "no results" screen.
    if not capped and errors:
        raise HTTPException(503, "; ".join(errors))
    return capped


async def _search_theirstack(payload: DiscoverySearchRequest) -> list[DiscoveryResult]:
    try:
        hits = await theirstack_search(
            title_keywords=payload.title_keywords or None,
            description_keywords=payload.description_keywords or None,
            country_codes=payload.country_codes or None,
            technology_slugs=payload.technology_slugs or None,
            max_age_days=payload.max_age_days,
            limit=payload.limit,
            page=payload.page,
        )
    except TheirStackUnavailableError as e:
        raise RuntimeError(f"theirstack unavailable: {e}") from e
    return [
        DiscoveryResult(
            source="theirstack",
            source_label="TheirStack",
            source_id=h.source_id,
            source_url=h.source_url,
            title=h.title,
            company_name=h.company_name,
            company_domain=h.company_domain,
            location=h.location,
            country_code=h.country_code,
            posted_at=h.posted_at,
            description=h.description,
            technologies=h.technologies,
        )
        for h in hits
    ]


async def _search_github(payload: DiscoverySearchRequest) -> list[DiscoveryResult]:
    # GitHub source honors title_keywords + max_age_days only; the others
    # don't have analogues in the SimplifyJobs tables.
    hits = await github_jobs.search_jobs(
        title_keywords=payload.title_keywords or None,
        max_age_days=payload.max_age_days,
        limit=payload.limit,
    )
    return [
        DiscoveryResult(
            source="github",
            source_label=h.repo_label,
            source_id=h.source_id,
            source_url=h.apply_url,
            title=h.role,
            company_name=h.company,
            location=h.location,
            posted_at=h.posted_at,
            description="",
        )
        for h in hits
    ]


async def _annotate_already_imported(
    session: AsyncSession, results: list[DiscoveryResult]
) -> None:
    pairs = {(r.source, r.source_id) for r in results if r.source_id}
    if not pairs:
        return
    # Single SELECT against (source, source_id) for all results. Postgres's
    # row-IN tuple syntax keeps this one round trip.
    rows = await session.execute(
        select(Job.source, Job.source_id).where(
            tuple_(Job.source, Job.source_id).in_(pairs),
            or_(Job.source_id.is_not(None)),
        )
    )
    existing = {(s, sid) for s, sid in rows.all()}
    for r in results:
        if (r.source, r.source_id) in existing:
            r.already_imported = True


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

    # If the description is missing/short (typical for github-sourced rows),
    # fetch the JD via Firecrawl before parsing. This keeps the import flow
    # uniform across sources without forcing the FE to know per-source rules.
    description = payload.description
    raw = payload.description
    if (len(description) < _MIN_DESCRIPTION_CHARS) and payload.source_url:
        try:
            fetched = await fetch_url_markdown(payload.source_url)
            description = fetched.markdown or description
            raw = fetched.raw or raw or description
        except Exception as e:  # noqa: BLE001 — fallback is acceptable
            # If the fetch fails we still create the Job with whatever we had,
            # so the user can manually paste the JD later via /jobs/from-text.
            from structlog import get_logger

            get_logger(__name__).warning(
                "discovery.import.fetch_failed", url=payload.source_url, error=str(e)
            )

    parsed = await parse_jd(description, title_hint=payload.title)
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
        jd_raw=raw or description,
        jd_clean=description,
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


# ---- Saved searches ---------------------------------------------------------


@router.get("/saved", response_model=list[SavedSearchRead])
async def list_saved(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[SavedSearch]:
    rows = await session.execute(
        select(SavedSearch)
        .where(SavedSearch.user_id == user.id)
        .order_by(SavedSearch.updated_at.desc())
    )
    return list(rows.scalars().all())


@router.post("/saved", response_model=SavedSearchRead, status_code=201)
async def create_saved(
    payload: SavedSearchCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SavedSearch:
    existing = await session.execute(
        select(SavedSearch).where(
            SavedSearch.user_id == user.id, SavedSearch.name == payload.name
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(409, f"saved search named {payload.name!r} already exists")
    saved = SavedSearch(
        user_id=user.id,
        name=payload.name,
        query=payload.query.model_dump(mode="json"),
    )
    session.add(saved)
    await session.flush()
    return saved


@router.delete("/saved/{saved_id}", status_code=204)
async def delete_saved(
    saved_id: UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    saved = await session.get(SavedSearch, saved_id)
    if saved is None or saved.user_id != user.id:
        raise HTTPException(404, "saved search not found")
    await session.delete(saved)


@router.post("/saved/{saved_id}/run", response_model=list[DiscoveryResult])
async def run_saved(
    saved_id: UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[DiscoveryResult]:
    saved = await session.get(SavedSearch, saved_id)
    if saved is None or saved.user_id != user.id:
        raise HTTPException(404, "saved search not found")

    query = DiscoverySearchRequest.model_validate(saved.query or {})
    results = await _run_search(query, session)

    saved.last_run_at = datetime.now(UTC)
    saved.last_run_count = len(results)
    await session.flush()
    return results
