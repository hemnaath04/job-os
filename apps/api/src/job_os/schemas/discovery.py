"""Schemas for the discovery feed (M4) — multi-source search + one-click import.

Search results are NOT persisted to `jobs` — they're just rendered to the user.
Import is an explicit follow-up call that creates a `Job` row, dedup'd by the
(source, source_id) pair.

Sources today:
- `theirstack` — TheirStack /v1/jobs/search (aggregates LinkedIn / Lever /
  Greenhouse / Ashby / Workday; charges 1 credit per result).
- `github` — SimplifyJobs intern + new-grad README tables (free, cached 5min).
  Honors title_keywords + max_age_days; ignores country/tech filters.
- Apify boards (`linkedin`, `indeed`, `glassdoor`, `google`, `ziprecruiter`,
  `naukri`) — opt-in per-board scraping via the Apify actor (see
  `integrations/apify.py`); needs a per-user Apify token. All selected boards
  run in a single actor call to keep credit spend predictable.
"""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from job_os.schemas.common import ORMModel

DiscoverySource = Literal[
    "theirstack",
    "github",
    # Apify boards (opt-in; served by a single actor run):
    "linkedin",
    "indeed",
    "glassdoor",
    "google",
    "ziprecruiter",
    "naukri",
    # Free, keyless boards (no Apify, no key — see integrations/free_boards.py):
    "remotive",
    "remoteok",
    "themuse",
    "arbeitnow",
    "jobicy",
]


def _default_sources() -> list[DiscoverySource]:
    return ["theirstack"]


class DiscoverySearchRequest(ORMModel):
    sources: list[DiscoverySource] = Field(
        default_factory=_default_sources,
        description="Which sources to query. Multiple = merged + sorted by posted_at desc.",
    )
    title_keywords: list[str] = Field(default_factory=list)
    description_keywords: list[str] = Field(default_factory=list)
    location: str | None = Field(
        default=None,
        description="Free-text city/region for Apify boards (e.g. 'Boston', 'Remote').",
    )
    country_codes: list[str] = Field(
        default_factory=list, description="ISO-3166 alpha-2 codes (TheirStack + Apify)."
    )
    technology_slugs: list[str] = Field(
        default_factory=list, description="Tech slugs (TheirStack only)."
    )
    max_age_days: int = Field(default=30, ge=1, le=180)
    limit: int = Field(default=20, ge=1, le=50)
    page: int = Field(default=0, ge=0)


class DiscoveryResult(ORMModel):
    source: str = "theirstack"
    source_label: str | None = None
    """Human-friendly label for the source — e.g. 'SimplifyJobs · Internships'."""
    source_id: str
    source_url: str
    title: str
    company_name: str | None = None
    company_domain: str | None = None
    location: str | None = None
    country_code: str | None = None
    posted_at: datetime | None = None
    description: str
    """May be empty for github results — the FE/import path fetches the JD on demand."""
    technologies: list[str] = Field(default_factory=list)
    already_imported: bool = False
    """True if this user already has a Job row matching (source, source_id)."""


class DiscoverySourceError(ORMModel):
    """One source failed to return results (e.g. TheirStack key missing).

    Surfaced to the FE so the user sees WHY a source went dark instead of
    just silently getting an unbalanced result mix."""

    source: DiscoverySource
    message: str


class DiscoverySearchResponse(ORMModel):
    results: list[DiscoveryResult] = Field(default_factory=list)
    source_counts: dict[str, int] = Field(default_factory=dict)
    """Per-source result counts BEFORE the cross-source limit cap."""
    errors: list[DiscoverySourceError] = Field(default_factory=list)


class ApifyUsageResponse(ORMModel):
    """Apify account credit snapshot for the Settings integration panel.

    `configured=False` means no token is set (env or per-user) — the FE shows a
    'paste your key' prompt instead of an error. `valid=False` means the token
    was present but Apify rejected it.
    """

    configured: bool
    valid: bool = False
    error: str | None = None
    max_monthly_usd: float | None = None
    used_usd: float | None = None
    remaining_usd: float | None = None
    cycle_start: datetime | None = None
    cycle_end: datetime | None = None
    price_per_result_usd: float | None = None
    est_results_per_search: int | None = None
    est_searches_left: int | None = None


class DiscoveryImportRequest(ORMModel):
    source: str = "theirstack"
    source_id: str
    source_url: str
    title: str
    description: str
    company_name: str | None = None
    company_domain: str | None = None
    location: str | None = None
    posted_at: datetime | None = None


class SmartSearchRequest(ORMModel):
    """Natural-language search: a free-form sentence Claude parses into filters."""

    query: str = Field(min_length=1, max_length=500)


class SmartSearchResponse(ORMModel):
    """The structured filters Claude pulled out of the user's sentence."""

    filters: DiscoverySearchRequest
    explanation: str = ""
    """One-line plain-English summary of what got extracted, for the FE to show."""


class SavedSearchCreate(ORMModel):
    name: str = Field(min_length=1, max_length=100)
    query: DiscoverySearchRequest


class SavedSearchRead(ORMModel):
    id: UUID
    name: str
    query: DiscoverySearchRequest
    last_run_at: datetime | None = None
    last_run_count: int | None = None
    created_at: datetime
    updated_at: datetime
