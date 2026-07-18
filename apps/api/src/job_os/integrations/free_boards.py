"""Free, keyless job-board sources (no Apify, no API key).

Each board exposes a public JSON endpoint. We fetch, keyword-filter (client-side
where the API has no search param), and normalise to a shared `FreeJob`. These
follow the `github_jobs` pattern: always available, free, best-effort. A board
that errors is caught by the discovery router and surfaced as a per-source note,
never a hard failure.

Boards:
- remotive  — remotive.com public API (server-side `search`).
- remoteok  — remoteok.com/api (array; item[0] is a legal notice we skip).
- themuse   — themuse.com public API (no keyword search; paged + client-filter).
- arbeitnow — arbeitnow.com board API (latest 100; client-filter).
- jobicy    — jobicy.com v2 remote-jobs (`tag` search + client-filter).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

log = structlog.get_logger(__name__)

_UA = "job-os/1.0 (+https://github.com/hemnaath04/job-os)"

LABELS: dict[str, str] = {
    "remotive": "Remotive",
    "remoteok": "RemoteOK",
    "themuse": "The Muse",
    "arbeitnow": "Arbeitnow",
    "jobicy": "Jobicy",
}


@dataclass(slots=True)
class FreeJob:
    """One listing, normalised to job.os field names. `source` is the board key."""

    source: str
    source_id: str
    source_url: str
    title: str
    company_name: str | None
    location: str | None
    posted_at: datetime | None
    description: str
    technologies: list[str] = field(default_factory=list)


# ---- helpers ----------------------------------------------------------------


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8), reraise=True)
async def _get_json(url: str, params: dict[str, Any] | None = None) -> Any:
    async with httpx.AsyncClient(
        timeout=30.0,
        headers={"User-Agent": _UA, "Accept": "application/json"},
        follow_redirects=True,
    ) as client:
        resp = await client.get(url, params=params)
    if resp.status_code >= 400:
        log.warning("free_boards.error", url=url, status=resp.status_code)
        resp.raise_for_status()
    return resp.json()


def _text(html: Any) -> str:
    """HTML/markup -> plain text, trimmed. Descriptions ship as HTML on most boards."""
    if not html:
        return ""
    return BeautifulSoup(str(html), "lxml").get_text(" ", strip=True)[:8000]


def _matches(text: str, keywords: list[str] | None) -> bool:
    if not keywords:
        return True
    low = text.lower()
    return any(k.lower() in low for k in keywords if k)


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_epoch(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value), tz=UTC)
    except (TypeError, ValueError):
        return None


# ---- boards -----------------------------------------------------------------


async def search_remotive(
    *, title_keywords: list[str] | None = None, location: str | None = None, limit: int = 20
) -> list[FreeJob]:
    params: dict[str, Any] = {"limit": max(1, min(limit, 50))}
    if title_keywords:
        params["search"] = " ".join(title_keywords)  # server-side keyword search
    data = await _get_json("https://remotive.com/api/remote-jobs", params)
    out: list[FreeJob] = []
    for j in (data.get("jobs") or [])[:limit]:
        out.append(
            FreeJob(
                source="remotive",
                source_id=str(j.get("id") or ""),
                source_url=j.get("url") or "",
                title=j.get("title") or "Untitled",
                company_name=j.get("company_name") or None,
                location=j.get("candidate_required_location") or None,
                posted_at=_parse_dt(j.get("publication_date")),
                description=_text(j.get("description")),
                technologies=[str(t) for t in (j.get("tags") or [])][:12],
            )
        )
    return out


async def search_remoteok(
    *, title_keywords: list[str] | None = None, location: str | None = None, limit: int = 20
) -> list[FreeJob]:
    data = await _get_json("https://remoteok.com/api")
    out: list[FreeJob] = []
    for j in data if isinstance(data, list) else []:
        # Skip the leading legal-notice object and any malformed rows.
        if not isinstance(j, dict) or not j.get("id") or not j.get("position"):
            continue
        title = j.get("position") or ""
        hay = f"{title} {' '.join(str(t) for t in (j.get('tags') or []))}"
        if not _matches(hay, title_keywords):
            continue
        out.append(
            FreeJob(
                source="remoteok",
                source_id=str(j.get("id")),
                source_url=j.get("url") or j.get("apply_url") or "",
                title=title or "Untitled",
                company_name=j.get("company") or None,
                location=(j.get("location") or "").strip(" ,") or None,
                posted_at=_parse_dt(j.get("date")),
                description=_text(j.get("description")),
                technologies=[str(t) for t in (j.get("tags") or [])][:12],
            )
        )
        if len(out) >= limit:
            break
    return out


async def search_themuse(
    *, title_keywords: list[str] | None = None, location: str | None = None, limit: int = 20
) -> list[FreeJob]:
    # No keyword search param — pull a few recent pages and filter client-side.
    out: list[FreeJob] = []
    for page in range(1, 4):
        data = await _get_json(
            "https://www.themuse.com/api/public/jobs", {"page": page, "descending": "true"}
        )
        results = data.get("results") or []
        for j in results:
            name = j.get("name") or ""
            desc = _text(j.get("contents"))
            if not _matches(f"{name} {desc[:400]}", title_keywords):
                continue
            locs = ", ".join(
                lc.get("name", "") for lc in (j.get("locations") or []) if isinstance(lc, dict)
            )
            if location and location.lower() not in locs.lower():
                continue
            out.append(
                FreeJob(
                    source="themuse",
                    source_id=str(j.get("id") or ""),
                    source_url=(j.get("refs") or {}).get("landing_page") or "",
                    title=name or "Untitled",
                    company_name=(j.get("company") or {}).get("name") or None,
                    location=locs or None,
                    posted_at=_parse_dt(j.get("publication_date")),
                    description=desc,
                    technologies=[
                        str(t["name"])
                        for t in (j.get("tags") or [])
                        if isinstance(t, dict) and t.get("name")
                    ][:12],
                )
            )
            if len(out) >= limit:
                break
        if len(out) >= limit or not results:
            break
    return out


async def search_arbeitnow(
    *, title_keywords: list[str] | None = None, location: str | None = None, limit: int = 20
) -> list[FreeJob]:
    data = await _get_json("https://www.arbeitnow.com/api/job-board-api")
    out: list[FreeJob] = []
    for j in data.get("data") or []:
        title = j.get("title") or ""
        tags_txt = " ".join(str(t) for t in (j.get("tags") or []))
        if not _matches(f"{title} {tags_txt}", title_keywords):
            continue
        loc = j.get("location") or None
        if location:
            wants_remote = "remote" in location.lower()
            if location.lower() not in (loc or "").lower() and not (
                wants_remote and j.get("remote")
            ):
                continue
        out.append(
            FreeJob(
                source="arbeitnow",
                source_id=str(j.get("slug") or ""),
                source_url=j.get("url") or "",
                title=title or "Untitled",
                company_name=j.get("company_name") or None,
                location=loc,
                posted_at=_parse_epoch(j.get("created_at")),
                description=_text(j.get("description")),
                technologies=[str(t) for t in (j.get("tags") or [])][:12],
            )
        )
        if len(out) >= limit:
            break
    return out


async def search_jobicy(
    *, title_keywords: list[str] | None = None, location: str | None = None, limit: int = 20
) -> list[FreeJob]:
    params: dict[str, Any] = {"count": max(1, min(limit, 50))}
    if title_keywords:
        params["tag"] = " ".join(title_keywords)  # loose search; filtered again below
    data = await _get_json("https://jobicy.com/api/v2/remote-jobs", params)
    out: list[FreeJob] = []
    for j in data.get("jobs") or []:
        title = j.get("jobTitle") or ""
        if not _matches(f"{title} {j.get('jobExcerpt', '')}", title_keywords):
            continue
        out.append(
            FreeJob(
                source="jobicy",
                source_id=str(j.get("id") or ""),
                source_url=j.get("url") or "",
                title=title or "Untitled",
                company_name=j.get("companyName") or None,
                location=j.get("jobGeo") or None,
                posted_at=_parse_dt(j.get("pubDate")),
                description=_text(j.get("jobDescription")) or _text(j.get("jobExcerpt")),
                technologies=[str(t) for t in (j.get("jobIndustry") or [])][:8],
            )
        )
        if len(out) >= limit:
            break
    return out


SEARCHERS = {
    "remotive": search_remotive,
    "remoteok": search_remoteok,
    "themuse": search_themuse,
    "arbeitnow": search_arbeitnow,
    "jobicy": search_jobicy,
}
FREE_BOARDS: frozenset[str] = frozenset(SEARCHERS)
