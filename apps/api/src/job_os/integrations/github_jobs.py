"""GitHub-hosted intern/new-grad lists (SimplifyJobs / PittCSC heritage).

These repos maintain a Markdown table of open roles; we parse the README and
treat each row as a discovery hit. We do NOT scrape companies directly here —
the repo maintainers already aggregate them; we're just reading their list.

Parsing notes (current as of 2026-06-20):
- The README embeds HTML `<table>` elements per category (Software Engineering,
  Data Science, etc.). Each row has 5 cells: Company, Role, Location,
  Application, Age. We use BeautifulSoup to extract them.
- Company cell: `<strong><a>Name</a></strong>` for the lead row, or just `↳`
  for continuation rows that share the previous company.
- Application cell: two `<a>` tags inside a `<div align="center">`; the first
  is the real apply URL, the second is the Simplify redirect.
- Age cell: `Nd` days-since-posted; we convert to a UTC datetime.

The repos update many times a day; the user wants fresh data on every search,
so there's no result cache — `list_repo_jobs` re-fetches the README each call.
GitHub doesn't rate-limit anonymous reads of raw.githubusercontent for this
volume; if that ever becomes an issue we'll add a 30-60s cache here.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

import httpx
import structlog
from bs4 import BeautifulSoup, Tag
from tenacity import retry, stop_after_attempt, wait_exponential

log = structlog.get_logger(__name__)

REPOS: Final[dict[str, str]] = {
    "simplify_summer_internships": (
        "https://raw.githubusercontent.com/SimplifyJobs/"
        "Summer2026-Internships/dev/README.md"
    ),
    "simplify_new_grad": (
        "https://raw.githubusercontent.com/SimplifyJobs/"
        "New-Grad-Positions/dev/README.md"
    ),
}

REPO_LABELS: Final[dict[str, str]] = {
    "simplify_summer_internships": "SimplifyJobs · Internships",
    "simplify_new_grad": "SimplifyJobs · New Grad",
}

_AGE_RE = re.compile(r"(\d+)\s*d")
_EMOJI_PREFIXES = ("🔥", "🛂", "🇺🇸", "✨", "🆕", "🌎", "🇨🇦", "🇬🇧")


@dataclass(slots=True)
class GithubJob:
    repo: str
    repo_label: str
    company: str
    role: str
    location: str | None
    apply_url: str
    posted_at: datetime | None
    source_id: str


def _clean_text(s: str) -> str:
    out = s.strip()
    for e in _EMOJI_PREFIXES:
        out = out.replace(e, "")
    return " ".join(out.split())  # collapse internal whitespace


def _parse_age(cell: str) -> datetime | None:
    m = _AGE_RE.search(cell.strip())
    if not m:
        return None
    return datetime.now(UTC) - timedelta(days=int(m.group(1)))


def _first_apply_url(cell: Tag) -> str | None:
    """First <a href> inside the Application cell — the real apply URL.

    Skip simplify.jobs/* redirects (those are the second link in every row)."""
    fallback: str | None = None
    for a in cell.find_all("a"):
        raw_href = a.get("href")
        if raw_href is None:
            continue
        href_str = str(raw_href)
        if not href_str:
            continue
        if fallback is None:
            fallback = href_str
        if "simplify.jobs/" in href_str and "/p/" in href_str:
            # The Simplify redirect — keep looking for the upstream URL.
            continue
        return href_str
    return fallback


def _parse_table(repo: str, md: str) -> list[GithubJob]:
    repo_label = REPO_LABELS.get(repo, repo)
    soup = BeautifulSoup(md, "html.parser")
    jobs: list[GithubJob] = []
    for table in soup.find_all("table"):
        last_company: str | None = None
        # Iterate body rows only — header rows live in <thead>.
        for tr in table.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 5:
                continue
            c_company, c_role, c_loc, c_link, c_age = tds[:5]
            company_text = _clean_text(c_company.get_text(" "))
            if not company_text or company_text == "↳":
                company = last_company or ""
            else:
                company = company_text
                last_company = company
            if not company:
                continue
            role = _clean_text(c_role.get_text(" "))
            if not role:
                continue
            apply_url = _first_apply_url(c_link)
            if not apply_url:
                continue  # closed rows or missing links
            location = _clean_text(c_loc.get_text(" ")) or None
            source_id = hashlib.sha1(
                f"{repo}|{apply_url}".encode(), usedforsecurity=False
            ).hexdigest()[:16]
            jobs.append(
                GithubJob(
                    repo=repo,
                    repo_label=repo_label,
                    company=company,
                    role=role,
                    location=location,
                    apply_url=apply_url,
                    posted_at=_parse_age(c_age.get_text(" ")),
                    source_id=source_id,
                )
            )
    return jobs


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8), reraise=True)
async def _fetch_md(url: str) -> str:
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        resp = await client.get(url, headers={"User-Agent": "job-os/0.1"})
        resp.raise_for_status()
        return resp.text


async def list_repo_jobs(repo: str) -> list[GithubJob]:
    if repo not in REPOS:
        raise ValueError(f"unknown repo: {repo}")
    md = await _fetch_md(REPOS[repo])
    jobs = _parse_table(repo, md)
    log.info("github.parsed", repo=repo, count=len(jobs))
    return jobs


async def list_all_jobs() -> list[GithubJob]:
    results = await asyncio.gather(
        *(list_repo_jobs(r) for r in REPOS),
        return_exceptions=True,
    )
    out: list[GithubJob] = []
    for repo, res in zip(REPOS, results, strict=True):
        if isinstance(res, BaseException):
            log.warning("github.repo_failed", repo=repo, error=str(res))
            continue
        out.extend(res)
    return out


def _matches_titles(role: str, title_keywords: list[str]) -> bool:
    if not title_keywords:
        return True
    haystack = role.lower()
    return any(k.strip().lower() in haystack for k in title_keywords if k.strip())


async def search_jobs(
    *,
    title_keywords: list[str] | None = None,
    max_age_days: int = 30,
    limit: int = 20,
) -> list[GithubJob]:
    all_jobs = await list_all_jobs()
    cutoff = datetime.now(UTC) - timedelta(days=max_age_days)
    out = [
        j
        for j in all_jobs
        if (j.posted_at is None or j.posted_at >= cutoff)
        and _matches_titles(j.role, title_keywords or [])
    ]
    out.sort(key=lambda j: (j.posted_at or datetime.min.replace(tzinfo=UTC)), reverse=True)
    return out[: max(1, min(limit, 100))]
