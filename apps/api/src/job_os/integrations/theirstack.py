"""TheirStack jobs search client.

TheirStack auths with `Authorization: Bearer <api_key>` and charges 1 credit
per job returned, so default page sizes are kept small. Their `/v1/jobs/search`
requires at least one temporal or company filter — we always pass
`posted_at_max_age_days` to satisfy that, even when the caller didn't ask.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from job_os.settings import get_settings

log = structlog.get_logger(__name__)

API_URL = "https://api.theirstack.com/v1/jobs/search"
DEFAULT_MAX_AGE_DAYS = 30


@dataclass(slots=True)
class TheirStackJob:
    """One result row, normalised to job.os field names."""

    source_id: str
    source_url: str
    title: str
    company_name: str | None
    company_domain: str | None
    location: str | None
    country_code: str | None
    posted_at: datetime | None
    description: str
    technologies: list[str]

    @classmethod
    def from_api(cls, item: dict[str, Any]) -> TheirStackJob:
        return cls(
            source_id=str(item.get("job_id") or item.get("id") or ""),
            source_url=item.get("url") or "",
            title=item.get("job_title") or "Untitled",
            company_name=item.get("company_name") or None,
            company_domain=item.get("company_domain") or None,
            location=item.get("job_location") or None,
            country_code=item.get("job_country_code") or None,
            posted_at=_parse_dt(item.get("date_posted")),
            description=item.get("job_description") or "",
            technologies=list(item.get("job_technology_slug") or []),
        )


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


class TheirStackUnavailableError(RuntimeError):
    """Raised when the TheirStack key isn't configured. Surfaced as a clean 503."""


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8), reraise=True)
async def search_jobs(
    *,
    title_keywords: list[str] | None = None,
    description_keywords: list[str] | None = None,
    country_codes: list[str] | None = None,
    technology_slugs: list[str] | None = None,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    limit: int = 20,
    page: int = 0,
) -> list[TheirStackJob]:
    settings = get_settings()
    if not settings.theirstack_api_key:
        raise TheirStackUnavailableError("THEIRSTACK_API_KEY is not configured")

    body: dict[str, Any] = {
        "posted_at_max_age_days": max_age_days,  # required: temporal filter
        "limit": min(max(limit, 1), 50),         # cap to control credit burn
        "page": max(page, 0),
        "include_total_results": False,
        "order_by": [{"field": "date_posted", "desc": True}],
    }
    if title_keywords:
        body["job_title_or"] = title_keywords
    if description_keywords:
        body["job_description_pattern_or"] = description_keywords
    if country_codes:
        body["job_country_code_or"] = country_codes
    if technology_slugs:
        body["job_technology_slug_or"] = technology_slugs

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            API_URL,
            headers={
                "Authorization": f"Bearer {settings.theirstack_api_key}",
                "Content-Type": "application/json",
            },
            json=body,
        )
    if resp.status_code >= 400:
        log.warning("theirstack.error", status=resp.status_code, body=resp.text[:500])
        resp.raise_for_status()
    data = resp.json().get("data") or []
    return [TheirStackJob.from_api(item) for item in data]
