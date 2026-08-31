"""Request and response models for the indexed search.

`IndexHitRead` is deliberately close to `DiscoveryResult` in shape, because the
web app already renders that and the swap-over should not need a UI rewrite. It
differs in exactly one respect, and that difference is the point: freshness is
reported as evidence rather than as a single date. `posted_at` comes with
`posted_at_basis` and `posted_at_estimated`, and `first_seen_at` / `last_seen_at`
travel alongside, so the UI can write "first seen 3 weeks ago, still listed 1 hour
ago" instead of implying an employer posted something today when what actually
happened is that a crawler saw it today.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from job_os.ingest.providers import POSTED_AT_BASES
from job_os.services.job_index import DEFAULT_LIMIT, MAX_LIMIT


class IndexSearchRequest(BaseModel):
    title_keywords: list[str] = Field(
        default_factory=list,
        description=(
            "Phrases matched against the posting. Every word of a phrase must "
            "appear; the phrases themselves are alternatives."
        ),
    )
    query: str | None = Field(
        default=None, description="Free text matched against title, company, location and body."
    )
    location: str | None = None
    country_codes: list[str] = Field(default_factory=list)
    company: str | None = None
    sources: list[str] = Field(default_factory=list)
    remote: bool | None = None
    max_age_days: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Age measured on the effective date, which is posted_at when the board "
            "gave one and first_seen_at otherwise."
        ),
    )
    posted_within_days: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Stricter than max_age_days: only postings carrying a real published "
            "date inside the window, excluding any date we estimated."
        ),
    )
    include_inactive: bool = Field(
        default=False,
        description="Include postings the board has stopped listing, so a closure can be shown.",
    )
    include_duplicates: bool = False
    require_description: bool = Field(
        default=False,
        description="Exclude postings whose description has not been fetched yet.",
    )
    salary_min: int | None = Field(default=None, ge=0)
    limit: int = Field(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT)
    offset: int = Field(default=0, ge=0)
    explain: bool = Field(
        default=False, description="Return the score components for each hit."
    )


class ScoreLineRead(BaseModel):
    axis: str
    points: int
    reason: str
    detail: str
    subject: str | None = None
    evidence: str | None = None


class AxisScoreRead(BaseModel):
    axis: str
    weight: int
    points: int
    percent: int


class MatchScoreRead(BaseModel):
    """The AI-authoritative fit score, present once a posting has been
    enriched -- see `docs/job-enrichment.md`. Absent means the frontend's own
    lexicon estimate renders instead; the two never both render for one job.
    """

    overall: int
    raw_overall: int
    axes: list[AxisScoreRead]
    top_reasons: list[ScoreLineRead] = Field(
        description="The lines that moved the score most, not the full breakdown."
    )
    confidence: str
    confidence_reasons: list[str]
    blockers: list[ScoreLineRead] = Field(
        description="Not points. A hard mismatch (e.g. no visa sponsorship) travels here."
    )
    matched_skills: list[str]
    missing_skills: list[str]


class ScoreExplainRead(BaseModel):
    rank: float
    retrieve_score: float
    freshness_weight: float
    mix_weight: float
    text_rank_raw: float
    age_days: float
    effective_date: datetime
    company_rank: int
    matched_keywords: bool
    formula: str


class IndexHitRead(BaseModel):
    id: UUID
    source: str
    source_id: str
    source_url: str
    title: str
    company_name: str
    company_domain: str | None
    location: str | None
    country_code: str | None
    remote: bool
    department: str | None
    employment_type: str | None
    salary_min: int | None
    salary_max: int | None
    salary_currency: str | None
    snippet: str
    description_available: bool = Field(
        description=(
            "False when the provider's list endpoint carries no body and the extra "
            "per-posting fetch has not run. The snippet is then provider metadata, "
            "not the job description."
        )
    )

    posted_at: datetime | None
    posted_at_basis: str = Field(
        description=(
            "Where the date came from: "
            + " | ".join(POSTED_AT_BASES)
            + ". 'updated' and 'first_crawl' are upper bounds, not posting dates."
        )
    )
    posted_at_estimated: bool = Field(
        description="True when posted_at was inferred rather than published by the board."
    )
    first_seen_at: datetime = Field(description="First time this crawl ever saw the posting.")
    last_seen_at: datetime = Field(description="Most recent crawl that still found it listed.")
    active: bool
    inactive_since: datetime | None
    repost_count: int = Field(
        description="Times the posting disappeared from its board and came back."
    )
    rank: float
    explain: ScoreExplainRead | None = None
    match: MatchScoreRead | None = None


class IndexSearchResponse(BaseModel):
    results: list[IndexHitRead]
    total_matched: int = Field(description="Rows matching the filters, before the page limit.")
    total_matched_capped: bool = Field(
        default=False,
        description=(
            "True when counting stopped at the cap, so total_matched is a floor. "
            "Render it as '1000+' rather than as an exact total."
        ),
    )
    candidates_considered: int
    took_ms: float
    keyword_query: str | None = Field(
        default=None, description="The tsquery actually run, for debugging a surprising result set."
    )


class IndexStatsResponse(BaseModel):
    postings_total: int
    postings_active: int
    #: Both back after the return to Postgres. They need server-side
    #: aggregation, which is a `GROUP BY` here and was a full table scan per
    #: call on Appwrite, so they were dropped rather than served slowly.
    companies_active: int
    by_source: dict[str, int]
    duplicates_marked: int
    posted_at_estimated: int
    descriptions_missing: int
    last_crawl_seen_at: datetime | None
    #: Whether the counters above are true counts. Always True now: every one
    #: is a `COUNT(*)`. Kept because for two weeks it was not -- Appwrite's
    #: `total` saturated at 5,000 on a 359,416-row table, and a reader could
    #: not otherwise tell 5,000 rows from "at least 5,000".
    counts_exact: bool = True
    tokens: dict[str, dict[str, int]] = Field(
        default_factory=dict, description="Corpus liveness per provider and status."
    )
    ranking: dict[str, float] = Field(default_factory=dict)
