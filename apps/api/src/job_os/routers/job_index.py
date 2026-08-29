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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from job_os.auth import get_current_user
from job_os.db.models import User
from job_os.db.models.profile import ProfileFact
from job_os.db.session import get_session
from job_os.routers.me import _to_settings
from job_os.schemas.job_index import (
    AxisScoreRead,
    IndexHitRead,
    IndexSearchRequest,
    IndexSearchResponse,
    IndexStatsResponse,
    MatchScoreRead,
    ScoreExplainRead,
    ScoreLineRead,
)
from job_os.services import job_index, job_match

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
    facts = (
        await session.execute(select(ProfileFact).where(ProfileFact.user_id == _user.id))
    ).scalars().all()
    # Settings, not facts. Whether an employer would have to file a petition
    # is a statement the user makes about themselves, and until it was read
    # here every eligibility blocker in the scorer was unreachable.
    candidate = job_match.build_candidate_profile(
        facts, eligibility=_to_settings(_user.settings).work_eligibility
    )
    # A profile with nothing usable scores every job the same uninformative
    # way and still costs an enrichment call per un-cached posting on the
    # page -- skip it and let the frontend's own lexicon fallback (which
    # already returns 0/not-confident for an empty profile too) render
    # instead, rather than paying that cost for a signal that isn't there yet.
    has_signal = bool(candidate.skills or candidate.highest_degree != "none" or candidate.years_experience)

    result = await job_index.search_index(
        session, query, candidate=candidate if has_signal else None
    )
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

    payload = await job_index.index_stats()
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
        match=_match_to_read(hit.match) if hit.match else None,
    )


def _match_to_read(score: job_match.MatchScore) -> MatchScoreRead:
    def line(l: job_match.ScoreLine) -> ScoreLineRead:
        return ScoreLineRead(
            axis=l.axis, points=l.points, reason=l.reason, detail=l.detail,
            subject=l.subject, evidence=l.evidence,
        )

    return MatchScoreRead(
        overall=score.overall,
        raw_overall=score.raw_overall,
        axes=[
            AxisScoreRead(axis=a.axis, weight=a.weight, points=a.points, percent=a.percent)
            for a in score.axes
        ],
        top_reasons=[line(l) for l in score.top_reasons()],
        confidence=score.confidence,
        confidence_reasons=list(score.confidence_reasons),
        blockers=[line(l) for l in score.blockers],
        matched_skills=list(score.matched_skills),
        missing_skills=list(score.missing_skills),
    )
