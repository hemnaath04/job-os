"""The job description an ATS will hand over for free, if you ask it properly.

Importing a SimplifyJobs row produced an application with no description on it.
Those rows carry a title and a link and nothing else -- the card even says so
("score on import") -- and `/discovery/import` was written to close that gap by
running the link through Firecrawl. In practice it did not, and the reason is
specific rather than flaky: the links are overwhelmingly Greenhouse and Lever
postings, and `job-boards.greenhouse.io` serves a JavaScript shell whose HTML
contains none of the posting. The plain httpx + BeautifulSoup fallback in
`integrations/firecrawl` therefore recovers an empty page, and a Firecrawl
render costs a credit and several seconds to fetch what the board will simply
hand over as JSON.

So: ask the board. `boards-api.greenhouse.io` and `api.lever.co` both expose
one posting by id, key-free, in a couple of hundred milliseconds, and it is the
employer's own text rather than a scrape of a page about it. This runs before
Firecrawl and falls through to it for everything it cannot read.

Greenhouse and Lever only, deliberately. Ashby publishes a whole board and no
single posting, so reading one job means downloading every job -- occasionally
megabytes, on the path a user is waiting on. Workday needs a session. Those
keep the Firecrawl path they already had.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

import httpx
import structlog

log = structlog.get_logger(__name__)

#: One posting from a board, over the public API, on the import path a user is
#: waiting on. These endpoints answer in a few hundred milliseconds; anything
#: slower than this is not worth delaying the Firecrawl fallback for.
TIMEOUT_SECONDS = 8.0

_USER_AGENT = "job-os/1.0 (+https://github.com/hemnaath04/job-os) import-bot"

#: `hostname suffix -> (ats, path pattern)`. Greenhouse serves the same board
#: from three hosts and an aggregator will have linked whichever one the
#: employer published, so the host is matched by suffix rather than equality.
_ROUTES: list[tuple[str, str, re.Pattern[str]]] = [
    (
        "greenhouse.io",
        "greenhouse",
        re.compile(r"/(?P<board>[A-Za-z0-9_-]+)/jobs/(?P<job>\d+)"),
    ),
    (
        "lever.co",
        "lever",
        re.compile(r"/(?P<board>[A-Za-z0-9_.-]+)/(?P<job>[0-9a-fA-F-]{20,})"),
    ),
]


def parse_posting_url(url: str) -> tuple[str, str, str] | None:
    """`(ats, board, job_id)` for a posting URL we can read, else None."""
    if not url:
        return None
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host:
        return None
    for suffix, ats, pattern in _ROUTES:
        if host != suffix and not host.endswith("." + suffix):
            continue
        match = pattern.search(parsed.path)
        if match:
            return ats, match.group("board"), match.group("job")
    return None


_TAG = re.compile(r"<[^>]+>")
_SCRIPT = re.compile(r"<(script|style)[\s\S]*?</\1>", re.I)
_BLOCK_END = re.compile(r"</(p|div|li|ul|ol|h[1-6]|tr|table|section)>", re.I)
_ENTITIES = {
    "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&#39;": "'",
    "&apos;": "'", "&nbsp;": " ", "&ndash;": "-", "&mdash;": "-",
    "&hellip;": "...", "&rsquo;": "'", "&lsquo;": "'", "&rdquo;": '"',
    "&ldquo;": '"', "&bull;": "*",
}


def _decode(text: str) -> str:
    for entity, char in _ENTITIES.items():
        text = text.replace(entity, char)
    return text


def html_to_text(html: str) -> str:
    """Markup to readable text. Greenhouse ships `content` entity-encoded, so
    the encoded form is unwrapped once before tags are stripped."""
    if not html:
        return ""
    text = html
    if "&lt;" in text and "<" not in text:
        text = _decode(text)
    text = _SCRIPT.sub(" ", text)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = _BLOCK_END.sub("\n", text)
    text = re.sub(r"<li[^>]*>", "- ", text, flags=re.I)
    text = _TAG.sub(" ", text)
    text = _decode(text)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


async def _get_json(url: str) -> Any:
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS, follow_redirects=True) as client:
        response = await client.get(
            url, headers={"accept": "application/json", "user-agent": _USER_AGENT}
        )
    if response.status_code >= 400:
        raise RuntimeError(f"HTTP {response.status_code}")
    payload: Any = response.json()
    return payload


async def fetch_description(url: str) -> str | None:
    """The posting's own description text, or None if this URL is not one we
    can read or the board did not answer.

    Never raises. The caller's next move is Firecrawl either way, and a board
    being briefly unreachable is not a reason to fail an import that has a
    working fallback.
    """
    route = parse_posting_url(url)
    if route is None:
        return None
    ats, board, job_id = route
    try:
        if ats == "greenhouse":
            payload = await _get_json(
                f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job_id}"
            )
            content = payload.get("content") if isinstance(payload, dict) else None
            text = html_to_text(content or "")
        else:
            payload = await _get_json(f"https://api.lever.co/v0/postings/{board}/{job_id}")
            if not isinstance(payload, dict):
                return None
            # `descriptionPlain` is Lever's own text rendering and is the
            # opening section only; `description` is the full HTML body. Take
            # whichever is longer rather than assuming, since which one carries
            # the requirements varies by how the employer filled the posting in.
            plain = (payload.get("descriptionPlain") or "").strip()
            rich = html_to_text(payload.get("description") or "")
            text = rich if len(rich) > len(plain) else plain
    except Exception as exc:  # noqa: BLE001 -- Firecrawl is the fallback
        log.info("ats_jd.miss", ats=ats, board=board, error=str(exc)[:200])
        return None
    return text or None
