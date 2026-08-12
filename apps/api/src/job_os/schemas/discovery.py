"""Schemas for the discovery feed (M4) — multi-source search + one-click import.

Search results are NOT persisted to `jobs` — they're just rendered to the user.
Import is an explicit follow-up call that creates a `Job` row, dedup'd by the
(source, source_id) pair.

Sources this API queries itself:
- `theirstack` — TheirStack /v1/jobs/search (aggregates LinkedIn / Lever /
  Greenhouse / Ashby / Workday; charges 1 credit per result).
- `github` — SimplifyJobs intern + new-grad README tables (free, cached 5min).
  Honors title_keywords + max_age_days; ignores country/tech filters.

`sources` accepts more than those two. The rest of discovery is fetched by the
web app's own /api/discover route, and while this API never runs those sources it
does have to *store* them: a saved search holds the user's whole selection, and a
selection is not split by which backend serves it. Accepting only the two it runs
is what forced the web app to strip the others out of a saved search and keep them
in localStorage instead, where they are invisible to any other device.
"""
import re
from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import AfterValidator, Field

from job_os.schemas.common import ORMModel

BACKEND_SOURCES = frozenset({"theirstack", "github"})
"""The ones with a runner in routers.discovery. Everything else validates but is
not fetched here."""

WEB_SOURCES = frozenset(
    {
        # ATS boards, one company list each.
        "greenhouse",
        "lever",
        "ashby",
        # Remote boards.
        "remotive",
        "remoteok",
        # Board-wide feeds, prefixed so they route by shape rather than by name.
        "feed:himalayas",
        "feed:jobicy",
        "feed:arbeitnow",
        # Aggregators on a key the user pastes into their own browser.
        "jsearch",
        "adzuna",
    }
)
"""Served by the web app's /api/discover. Kept in step with
apps/web/src/lib/discover/sources.ts by hand, since the two runtimes share no
code; adding one here without adding it there is harmless, the reverse is a 422."""

KNOWN_SOURCES = BACKEND_SOURCES | WEB_SOURCES

_CUSTOM_SOURCE = re.compile(r"^custom:[A-Za-z0-9._~-]{1,64}$")
"""A user's own endpoint, registered in the browser. The id half cannot be
enumerated by any union, which is the other reason the old Literal could not hold
a real selection."""


def _known_source(value: str) -> str:
    if value in KNOWN_SOURCES or _CUSTOM_SOURCE.match(value):
        return value
    raise ValueError(
        f"unknown source {value!r}; expected one of "
        f"{', '.join(sorted(KNOWN_SOURCES))}, or custom:<id>"
    )


DiscoverySource = Annotated[str, AfterValidator(_known_source)]
"""A checked string rather than a Literal. Still rejects a typo with a 422; no
longer rejects a source this API simply does not happen to run."""


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
