from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from job_os.settings import get_settings

log = structlog.get_logger(__name__)


@dataclass(slots=True)
class FetchedPage:
    url: str
    markdown: str
    raw: str
    title: str | None
    company_hint: str | None


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8), reraise=True)
async def fetch_url_markdown(url: str) -> FetchedPage:
    """Fetch a job posting URL and return cleaned markdown.

    Uses Firecrawl when an API key is configured; otherwise falls back to a plain
    httpx + BeautifulSoup fetch. Both paths return the same FetchedPage shape.
    """
    settings = get_settings()
    if settings.firecrawl_api_key:
        return await _fetch_firecrawl(url, settings.firecrawl_api_key)
    log.warning("firecrawl.fallback.no_key", url=url)
    return await _fetch_plain(url)


async def _fetch_firecrawl(url: str, api_key: str) -> FetchedPage:
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            "https://api.firecrawl.dev/v1/scrape",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "url": url,
                "formats": ["markdown", "html"],
                "onlyMainContent": True,
                "waitFor": 1500,
            },
        )
        resp.raise_for_status()
        data = resp.json().get("data", {})

    md = data.get("markdown") or ""
    html = data.get("html") or ""
    meta = data.get("metadata") or {}
    title = meta.get("title")
    company_hint = _company_from_domain(url)
    return FetchedPage(url=url, markdown=md, raw=html or md, title=title, company_hint=company_hint)


async def _fetch_plain(url: str) -> FetchedPage:
    from bs4 import BeautifulSoup

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        resp = await client.get(url, headers={"User-Agent": "job-os/0.1"})
        resp.raise_for_status()
        html = resp.text

    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "nav", "header", "footer"]):
        tag.decompose()
    title = soup.title.string.strip() if soup.title and soup.title.string else None
    text = "\n".join(line.strip() for line in soup.get_text("\n").splitlines() if line.strip())
    return FetchedPage(
        url=url, markdown=text, raw=html, title=title, company_hint=_company_from_domain(url)
    )


def _company_from_domain(url: str) -> str | None:
    host = (urlparse(url).hostname or "").lower()
    # job board hosts → useless as company hint
    skip = {
        "boards.greenhouse.io", "jobs.lever.co", "jobs.ashbyhq.com",
        "myworkdayjobs.com", "linkedin.com", "indeed.com", "glassdoor.com",
        "wellfound.com", "ycombinator.com",
    }
    if any(host == d or host.endswith("." + d) for d in skip):
        return None
    if host.startswith("www."):
        host = host[4:]
    return host.split(".")[0].title() if host else None
