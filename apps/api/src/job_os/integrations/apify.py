"""Apify multi-board job scraper (optional, per-user token).

Runs the `openclawai/job-board-scraper` Actor, which scrapes LinkedIn, Indeed,
Glassdoor, Google Jobs, ZipRecruiter and Naukri in a single run and returns a
de-duplicated dataset. It is *opt-in*: the token comes from the current user's
settings (`User.settings.apify_api_token`), falling back to the `APIFY_API_TOKEN`
env var. When neither is set the source raises `ApifyUnavailableError`, which the
discovery router surfaces as a per-source error (never a hard crash).

Credits/usage come from Apify's account API (`/users/me/limits`) so the UI can
show "how much money / how many searches" are left.

Unlike TheirStack, we deliberately do NOT retry the Actor run: `run-sync` bills
per delivered result, so a blind retry would double-charge on a transient error.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from job_os.settings import get_settings

log = structlog.get_logger(__name__)

# Actor that aggregates the boards below. Overridable via APIFY_JOBS_ACTOR so the
# integration can be pointed at a different/self-hosted actor without a code change.
DEFAULT_ACTOR = "openclawai~job-board-scraper"
RUN_SYNC_URL = "https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"
LIMITS_URL = "https://api.apify.com/v2/users/me/limits"

# Nominal results assumed per search when estimating "searches left" from the
# remaining USD balance. Purely for the UI estimate; real spend is per-result.
EST_RESULTS_PER_SEARCH = 20

# DiscoverySource key -> the actor's `sites` token. These keys double as the
# per-board toggles in the UI and as `DiscoveryResult.source` / `Job.source`.
BOARD_TO_SITE: dict[str, str] = {
    "linkedin": "linkedin",
    "indeed": "indeed",
    "glassdoor": "glassdoor",
    "google": "google",
    "ziprecruiter": "zip_recruiter",
    "naukri": "naukri",
}
SITE_TO_BOARD: dict[str, str] = {v: k for k, v in BOARD_TO_SITE.items()}
APIFY_BOARDS: frozenset[str] = frozenset(BOARD_TO_SITE)

BOARD_LABELS: dict[str, str] = {
    "linkedin": "LinkedIn",
    "indeed": "Indeed",
    "glassdoor": "Glassdoor",
    "google": "Google Jobs",
    "ziprecruiter": "ZipRecruiter",
    "naukri": "Naukri",
}

# ISO-3166 alpha-2 -> the actor's `countryIndeed` value (Indeed/Glassdoor targeting).
_COUNTRY_INDEED: dict[str, str] = {
    "US": "usa",
    "GB": "uk",
    "UK": "uk",
    "CA": "canada",
    "AU": "australia",
    "DE": "germany",
    "FR": "france",
    "IN": "india",
    "SG": "singapore",
    "AE": "uae",
    "NL": "netherlands",
    "IE": "ireland",
    "NZ": "newzealand",
}


class ApifyUnavailableError(RuntimeError):
    """Raised when Apify can't run — no token, or no usable search term."""


@dataclass(slots=True)
class ApifyJob:
    """One scraped listing, normalised to job.os field names.

    `source` is the per-board key (e.g. 'linkedin') so results land in the right
    per-source bucket and dedup by (source, source_id) across imports.
    """

    source: str
    source_id: str
    source_url: str
    title: str
    company_name: str | None
    company_domain: str | None
    location: str | None
    country_code: str | None
    posted_at: datetime | None
    description: str
    technologies: list[str] = field(default_factory=list)

    @classmethod
    def from_api(cls, item: dict[str, Any]) -> ApifyJob:
        site = str(item.get("site") or "").strip().lower()
        board = SITE_TO_BOARD.get(site, site or "apify")
        # `job_url` is the board listing (best for Firecrawl JD fetch on import);
        # fall back to the direct apply URL. The URL doubles as the stable id.
        url = str(item.get("job_url") or item.get("job_url_direct") or "").strip()
        return cls(
            source=board,
            source_id=url or f"{board}:{item.get('title', '')}:{item.get('company', '')}",
            source_url=url,
            title=str(item.get("title") or "Untitled"),
            company_name=item.get("company") or None,
            company_domain=None,
            location=item.get("location") or None,
            country_code=None,
            posted_at=_parse_posted(item.get("date_posted")),
            description=str(item.get("description") or ""),
            technologies=[str(s) for s in (item.get("skills") or []) if s],
        )


@dataclass(slots=True)
class ApifyUsage:
    """Snapshot of the Apify account's monthly usage cycle."""

    max_usd: float
    used_usd: float
    remaining_usd: float
    cycle_start: datetime | None
    cycle_end: datetime | None


def resolve_apify_token(user_settings: dict[str, Any] | None) -> str | None:
    """Per-user token (from `User.settings`) with an env-var fallback."""
    token = (user_settings or {}).get("apify_api_token")
    if token:
        return str(token)
    return get_settings().apify_api_token


def _parse_posted(value: Any) -> datetime | None:
    """Parse the actor's `date_posted`, which may be ISO or a relative phrase."""
    if not value:
        return None
    text = str(value).strip().lower()
    # ISO first (e.g. "2026-03-28" or "2026-03-28T09:00:00Z").
    try:
        return datetime.fromisoformat(text.replace("z", "+00:00"))
    except ValueError:
        pass
    now = datetime.now(UTC)
    if text in {"today", "just posted", "just now"}:
        return now
    if text == "yesterday":
        return now - timedelta(days=1)
    # "3 days ago", "5 hours ago", "2 weeks ago", "1 month ago"
    parts = text.split()
    if len(parts) >= 2 and parts[0].isdigit():
        n = int(parts[0])
        unit = parts[1]
        if unit.startswith("hour"):
            return now - timedelta(hours=n)
        if unit.startswith("day"):
            return now - timedelta(days=n)
        if unit.startswith("week"):
            return now - timedelta(weeks=n)
        if unit.startswith("month"):
            return now - timedelta(days=30 * n)
    return None


async def search_jobs(
    *,
    token: str,
    search_term: str,
    boards: list[str],
    location: str | None = None,
    country_codes: list[str] | None = None,
    max_age_days: int | None = None,
    limit_per_board: int = 10,
    is_remote: bool = False,
) -> list[ApifyJob]:
    """Run the actor once for all selected boards and normalise the results."""
    if not token:
        raise ApifyUnavailableError("Apify API token is not configured")
    sites = [BOARD_TO_SITE[b] for b in boards if b in BOARD_TO_SITE]
    if not sites:
        return []
    if not search_term.strip():
        raise ApifyUnavailableError("Apify needs at least one title keyword to search")

    body: dict[str, Any] = {
        "searchTerm": search_term.strip(),
        "sites": sites,
        "maxResults": max(1, min(limit_per_board, 100)),
        "descriptionFormat": "markdown",
    }
    if location:
        body["location"] = location
    if country_codes:
        mapped = _COUNTRY_INDEED.get(country_codes[0].upper())
        if mapped:
            body["countryIndeed"] = mapped
    if max_age_days:
        body["hoursOld"] = min(max_age_days * 24, 8760)
    if is_remote:
        body["isRemote"] = True

    settings = get_settings()
    actor = settings.apify_jobs_actor or DEFAULT_ACTOR
    url = RUN_SYNC_URL.format(actor=actor)

    # Actor runs can take a couple of minutes; give it room but stay under the
    # frontend proxy's 300s ceiling.
    async with httpx.AsyncClient(timeout=180.0) as client:
        resp = await client.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=body,
        )
    if resp.status_code >= 400:
        log.warning("apify.error", status=resp.status_code, body=resp.text[:500])
        resp.raise_for_status()

    data = resp.json()
    items = data if isinstance(data, list) else (data.get("items") or [])
    return [ApifyJob.from_api(it) for it in items if isinstance(it, dict)]


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8), reraise=True)
async def get_usage(token: str) -> ApifyUsage:
    """Fetch the account's monthly limit + current spend from Apify."""
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(
            LIMITS_URL, headers={"Authorization": f"Bearer {token}"}
        )
    resp.raise_for_status()
    data = resp.json().get("data") or {}
    limits = data.get("limits") or {}
    current = data.get("current") or {}
    cycle = data.get("monthlyUsageCycle") or {}
    max_usd = float(limits.get("maxMonthlyUsageUsd") or 0.0)
    used_usd = float(current.get("monthlyUsageUsd") or 0.0)
    return ApifyUsage(
        max_usd=max_usd,
        used_usd=used_usd,
        remaining_usd=max(max_usd - used_usd, 0.0),
        cycle_start=_parse_posted(cycle.get("startAt")),
        cycle_end=_parse_posted(cycle.get("endAt")),
    )
