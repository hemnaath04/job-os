"""Discovery feed (M4) — TheirStack + GitHub (SimplifyJobs) sources.

The router fans out to each requested source in parallel, merges the results,
dedupes against the user's existing jobs by (source, source_id), and sorts by
posted_at desc before applying the final limit.

Import path: for sources that ship a real JD (TheirStack), the description
goes straight to `services.jd_parse`. For sources that only ship a link
(GitHub), we run Firecrawl on the source_url first to grab the JD text.
"""
import asyncio
import math
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from job_os.auth import get_current_user
from job_os.db.models import Job, SavedSearch, User
from job_os.db.session import get_session
from job_os.integrations import apify, free_boards, github_jobs
from job_os.integrations.apify import (
    APIFY_BOARDS,
    BOARD_LABELS,
    ApifyUnavailableError,
    resolve_apify_token,
)
from job_os.integrations.firecrawl import fetch_url_markdown
from job_os.integrations.free_boards import FREE_BOARDS
from job_os.integrations.theirstack import (
    TheirStackUnavailableError,
)
from job_os.integrations.theirstack import (
    search_jobs as theirstack_search,
)
from job_os.schemas.discovery import (
    ApifyUsageResponse,
    DiscoveryImportRequest,
    DiscoveryResult,
    DiscoverySearchRequest,
    DiscoverySearchResponse,
    DiscoverySourceError,
    SavedSearchCreate,
    SavedSearchRead,
    SmartSearchRequest,
    SmartSearchResponse,
)
from job_os.schemas.jobs import JobRead
from job_os.services.companies import upsert_company
from job_os.services.discovery_smart_search import parse_smart_query
from job_os.services.jd_parse import parse_jd

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/discovery")

_MIN_DESCRIPTION_CHARS = 200


@router.post("/search", response_model=DiscoverySearchResponse)
async def search(
    payload: DiscoverySearchRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> DiscoverySearchResponse:
    return await _run_search(payload, session, user)


def _clean_err(exc: BaseException) -> str:
    """Trim noisy httpx errors to something the FE can pattern-match + show."""
    text = str(exc)
    return text if len(text) <= 300 else text[:300] + "…"


async def _run_search(
    payload: DiscoverySearchRequest, session: AsyncSession, user: User
) -> DiscoverySearchResponse:
    if not payload.sources:
        raise HTTPException(400, "sources list cannot be empty")

    requested = list(payload.sources)
    per_source_runners = {
        "theirstack": _search_theirstack,
        "github": _search_github,
    }
    # Apify boards are all served by ONE actor run (keeps credit spend + latency
    # down); the simple sources each get their own coroutine. Everything runs
    # concurrently and a failure in one never sinks the others.
    apify_boards: list[str] = [s for s in requested if s in APIFY_BOARDS]
    free_selected: list[str] = [s for s in requested if s in FREE_BOARDS]
    simple_sources: list[str] = [s for s in requested if s in per_source_runners]

    coros: list[Any] = []
    labels: list[tuple[str, object]] = []
    for src in simple_sources:
        coros.append(per_source_runners[src](payload))
        labels.append(("simple", src))
    # Free boards behave like simple sources (one source key each), just fetched
    # from their own keyless public APIs.
    for board in free_selected:
        coros.append(_search_free(payload, board))
        labels.append(("simple", board))
    if apify_boards:
        coros.append(_search_apify(payload, apify_boards, user))
        labels.append(("apify", apify_boards))

    gathered = await asyncio.gather(*coros, return_exceptions=True)

    combined: list[DiscoveryResult] = []
    errors: list[DiscoverySourceError] = []
    source_counts: dict[str, int] = {}
    for (kind, ref), res in zip(labels, gathered, strict=True):
        if kind == "simple":
            src = ref  # type: ignore[assignment]
            if isinstance(res, BaseException):
                errors.append(DiscoverySourceError(source=src, message=_clean_err(res)))
                source_counts[src] = 0
                log.warning("discovery.source_failed", source=src, error=str(res))
            else:
                source_counts[src] = len(res)
                combined.extend(res)
        else:  # apify — one call fanned out across several board keys
            boards: list[str] = ref  # type: ignore[assignment]
            if isinstance(res, BaseException):
                msg = _clean_err(res)
                log.warning("discovery.source_failed", source="apify", error=str(res))
                for b in boards:
                    errors.append(DiscoverySourceError(source=b, message=msg))
                    source_counts[b] = 0
            else:
                for b in boards:
                    source_counts.setdefault(b, 0)
                for r in res:
                    source_counts[r.source] = source_counts.get(r.source, 0) + 1
                combined.extend(res)

    if combined:
        await _annotate_already_imported(session, combined)

    # Sort by posted_at desc, nulls last — then cap to `limit` across sources.
    combined.sort(
        key=lambda r: (r.posted_at is None, -(r.posted_at.timestamp() if r.posted_at else 0))
    )
    capped = combined[: payload.limit]

    return DiscoverySearchResponse(
        results=capped,
        source_counts=source_counts,
        errors=errors,
    )


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


async def _search_free(
    payload: DiscoverySearchRequest, board: str
) -> list[DiscoveryResult]:
    """Adapter for a keyless free board (Remotive / RemoteOK / The Muse / …)."""
    hits = await free_boards.SEARCHERS[board](
        title_keywords=payload.title_keywords or None,
        location=payload.location or None,
        limit=payload.limit,
    )
    return [
        DiscoveryResult(
            source=board,
            source_label=free_boards.LABELS.get(board, board.title()),
            source_id=h.source_id,
            source_url=h.source_url,
            title=h.title,
            company_name=h.company_name,
            location=h.location,
            posted_at=h.posted_at,
            description=h.description,
            technologies=h.technologies,
        )
        for h in hits
        if h.source_id
    ]


async def _search_apify(
    payload: DiscoverySearchRequest, boards: list[str], user: User
) -> list[DiscoveryResult]:
    """Run the Apify actor once for all selected boards, tagging each result with
    its board so it lands in the right per-source bucket."""
    token = resolve_apify_token(user.settings)
    if not token:
        raise ApifyUnavailableError(
            "Apify API token not set — add it in Settings › Integrations"
        )
    search_term = " ".join(payload.title_keywords or []).strip()
    if not search_term:
        search_term = " ".join(payload.description_keywords or []).strip()

    # Spread the overall limit across the selected boards so total spend stays
    # near `limit` (the cross-source cap in _run_search trims any overflow).
    per_board = max(3, min(50, math.ceil(payload.limit / max(len(boards), 1))))

    hits = await apify.search_jobs(
        token=token,
        search_term=search_term,
        boards=boards,
        location=payload.location or None,
        country_codes=payload.country_codes or None,
        max_age_days=payload.max_age_days,
        limit_per_board=per_board,
    )
    return [
        DiscoveryResult(
            source=h.source,
            source_label=f"Apify · {BOARD_LABELS.get(h.source, h.source.title())}",
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
        if h.source_id
    ]


@router.get("/apify/usage", response_model=ApifyUsageResponse)
async def apify_usage(
    user: User = Depends(get_current_user),
) -> ApifyUsageResponse:
    """Apify credit snapshot for the Settings panel: remaining USD + an estimate
    of how many searches that buys."""
    from job_os.settings import get_settings

    token = resolve_apify_token(user.settings)
    if not token:
        return ApifyUsageResponse(configured=False)

    try:
        usage = await apify.get_usage(token)
    except httpx.HTTPStatusError as e:
        if e.response.status_code in (401, 403):
            return ApifyUsageResponse(
                configured=True,
                valid=False,
                error="Apify rejected this token (unauthorized). Rotate it and re-save.",
            )
        raise HTTPException(502, f"Apify usage lookup failed: {e}") from e
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Apify usage lookup failed: {e}") from e

    settings = get_settings()
    price = settings.apify_price_per_result_usd or 0.003
    est_results = apify.EST_RESULTS_PER_SEARCH
    cost_per_search = price * est_results
    est_searches = (
        int(usage.remaining_usd // cost_per_search) if cost_per_search > 0 else None
    )
    return ApifyUsageResponse(
        configured=True,
        valid=True,
        max_monthly_usd=usage.max_usd,
        used_usd=usage.used_usd,
        remaining_usd=usage.remaining_usd,
        cycle_start=usage.cycle_start,
        cycle_end=usage.cycle_end,
        price_per_result_usd=price,
        est_results_per_search=est_results,
        est_searches_left=est_searches,
    )


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


@router.post("/saved/{saved_id}/run", response_model=DiscoverySearchResponse)
async def run_saved(
    saved_id: UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> DiscoverySearchResponse:
    saved = await session.get(SavedSearch, saved_id)
    if saved is None or saved.user_id != user.id:
        raise HTTPException(404, "saved search not found")

    query = DiscoverySearchRequest.model_validate(saved.query or {})
    response = await _run_search(query, session, user)

    saved.last_run_at = datetime.now(UTC)
    saved.last_run_count = len(response.results)
    await session.flush()
    return response


@router.post("/smart-search", response_model=SmartSearchResponse)
async def smart_search(
    payload: SmartSearchRequest,
    _user: User = Depends(get_current_user),
) -> SmartSearchResponse:
    """Translate a natural-language sentence into a DiscoverySearchRequest.

    The FE hydrates the form fields from the returned filters and then runs
    a regular `/discovery/search`. Keeps the round-trip cheap (one Claude
    call) and lets the user tweak filters before spending TheirStack credits.
    """
    return await parse_smart_query(payload.query)
