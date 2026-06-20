"""Schemas for the discovery feed (M4) — multi-source search + one-click import.

Search results are NOT persisted to `jobs` — they're just rendered to the user.
Import is an explicit follow-up call that creates a `Job` row, dedup'd by the
(source, source_id) pair.

Sources today:
- `theirstack` — TheirStack /v1/jobs/search (aggregates LinkedIn / Lever /
  Greenhouse / Ashby / Workday; charges 1 credit per result).
- `github` — SimplifyJobs intern + new-grad README tables (free, cached 5min).
  Honors title_keywords + max_age_days; ignores country/tech filters.
"""
from datetime import datetime
from typing import Literal

from pydantic import Field

from job_os.schemas.common import ORMModel

DiscoverySource = Literal["theirstack", "github"]


def _default_sources() -> list[DiscoverySource]:
    return ["theirstack"]


class DiscoverySearchRequest(ORMModel):
    sources: list[DiscoverySource] = Field(
        default_factory=_default_sources,
        description="Which sources to query. Multiple = merged + sorted by posted_at desc.",
    )
    title_keywords: list[str] = Field(default_factory=list)
    description_keywords: list[str] = Field(default_factory=list)
    country_codes: list[str] = Field(
        default_factory=list, description="ISO-3166 alpha-2 codes (TheirStack only)."
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
