"""Indexed job search.

A new router rather than a change to `/discovery/search`. The existing endpoint
fans out to live sources and the web app depends on that behaviour today, so this
lands alongside it and the swap is a deliberate step, documented in
`docs/ingest-index.md`, not a side effect of merging this branch.

Same auth posture as the rest of the API: every route requires a signed-in user.
The index is not public, and `get_current_user` fails closed.
"""
from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from job_os.auth import get_current_user
from job_os.db.models import User
from job_os.db.session import get_session
from job_os.schemas.job_index import (
    IndexHitRead,
    IndexSearchRequest,
    IndexSearchResponse,
    IndexStatsResponse,
    ScoreExplainRead,
)
from job_os.services import job_index

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/index")


@router.post("/search", response_model=IndexSearchResponse)
async def search(
    payload: IndexSearchRequest,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> IndexSearchResponse:
    query = job_index.IndexQuery(
        title_keywords=payload.title_keywords,
        query=payload.query,
        location=payload.location,
        country_codes=payload.country_codes,
        company=payload.company,
        sources=payload.sources,
        remote=payload.remote,
        max_age_days=payload.max_age_days,
        posted_within_days=payload.posted_within_days,
        include_inactive=payload.include_inactive,
        include_duplicates=payload.include_duplicates,
        require_description=payload.require_description,
        salary_min=payload.salary_min,
        limit=payload.limit,
        offset=payload.offset,
        explain=payload.explain,
    )
    result = await job_index.search_index(session, query)
    log.info("index.search", **result.as_dict())

    return IndexSearchResponse(
        results=[_to_read(hit) for hit in result.hits],
        total_matched=result.total_matched,
        total_matched_capped=result.total_matched_capped,
        candidates_considered=result.candidates_considered,
        took_ms=result.took_ms,
        keyword_query=result.keyword_query,
    )


@router.get("/stats", response_model=IndexStatsResponse)
async def stats(
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> IndexStatsResponse:
    from job_os.ingest.liveness import liveness_summary

    payload = await job_index.index_stats(session)
    return IndexStatsResponse(
        **payload,
        tokens=await liveness_summary(session),
        ranking=job_index.ranking_constants(),
    )


def _to_read(hit: job_index.IndexHit) -> IndexHitRead:
    return IndexHitRead(
        id=hit.id,
        source=hit.source,
        source_id=hit.source_id,
        source_url=hit.source_url,
        title=hit.title,
        company_name=hit.company_name,
        company_domain=hit.company_domain,
        location=hit.location,
        country_code=hit.country_code,
        remote=hit.remote,
        department=hit.department,
        employment_type=hit.employment_type,
        salary_min=hit.salary_min,
        salary_max=hit.salary_max,
        salary_currency=hit.salary_currency,
        snippet=hit.snippet,
        description_available=hit.description_available,
        posted_at=hit.posted_at,
        posted_at_basis=hit.posted_at_basis,
        posted_at_estimated=hit.posted_at_estimated,
        first_seen_at=hit.first_seen_at,
        last_seen_at=hit.last_seen_at,
        active=hit.active,
        inactive_since=hit.inactive_since,
        repost_count=hit.repost_count,
        rank=hit.rank,
        explain=ScoreExplainRead(**hit.explain.as_dict()) if hit.explain else None,
    )
