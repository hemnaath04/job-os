from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from job_os.settings import get_settings

log = structlog.get_logger(__name__)

_BLOCKED_HOSTNAMES = {"localhost", "metadata.google.internal"}


def _assert_fetchable_url(url: str) -> None:
    """Keep a user-supplied job posting URL from probing the private network
    this runs in: https only, and no loopback, private, link-local or
    cloud-metadata target.

    Mirrors the guard in apps/web/src/lib/discover/custom-fetch.ts. Best-effort
    by design, same as that guard: it resolves the hostname once, up front, so
    DNS rebinding (the name resolving to something else by the time the
    request actually connects) and a redirect to a private address (the
    caller still fetches with follow_redirects=True) are both out of scope
    here.
    """
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError("must be an https URL")

    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError("must be an https URL")
    if host in _BLOCKED_HOSTNAMES or host.endswith(".local") or host.endswith(".internal"):
        raise ValueError("blocked host")

    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        addr = None
    if addr is not None and (
        addr.is_loopback
        or addr.is_private
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_unspecified
    ):
        raise ValueError("blocked host")

    # A bare hostname (not a literal IP) still resolves to something. Reject it
    # if every address it resolves to is private, so a name like
    # "internal.example.com" pointed at 10.0.0.5 cannot be used as a detour
    # around the literal-IP check above.
    if addr is None:
        try:
            infos = socket.getaddrinfo(host, None)
        except socket.gaierror as e:
            raise ValueError("could not resolve host") from e
        resolved = [ipaddress.ip_address(info[4][0]) for info in infos]
        if resolved and all(
            a.is_loopback or a.is_private or a.is_link_local or a.is_multicast or a.is_unspecified
            for a in resolved
        ):
            raise ValueError("blocked host")


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
    # A user is waiting on this synchronously (see routers/jobs.py's
    # create_from_url and discovery.py's import_result), so 60s was too
    # generous a ceiling for a single page fetch. Firecrawl's own waitFor
    # below is 1.5s; a scrape that has not returned well inside 20s is not
    # going to feel fast even if it eventually succeeds.
    async with httpx.AsyncClient(timeout=20.0) as client:
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

    # This server does the fetching itself here (unlike _fetch_firecrawl, where
    # Firecrawl's servers fetch on our behalf), so a user-supplied URL reaches
    # our own network directly. Reachable whenever FIRECRAWL_API_KEY is unset.
    _assert_fetchable_url(url)

    # Same reasoning as _fetch_firecrawl: a raw GET with no rendering wait
    # should be faster than the Firecrawl path, not slower, so it gets a
    # tighter ceiling.
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
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
