"""Read an Eightfold-hosted posting from its own API instead of its shell.

Eightfold career sites are client-rendered. Fetching one returns the app's
bootstrap payload rather than the job: Millennium's `campusjobs.mlp.com` stored
15KB of theme colours, navigation HTML and CSS as `jd_clean`, and Microsoft's
`apply.careers.microsoft.com` stored roughly 498,000 characters of the same
thing. Both parsed to zero requirements, correctly, because there were none in
the text. No amount of re-tailoring fixes a posting the reader never read.

Every tenant exposes the same unauthenticated JSON endpoint, on the tenant's
own host:

    GET https://<tenant-host>/api/apply/v2/jobs/<job_id>

Verified 2026-08-29 against both tenants. Millennium returns 2,649 characters of
job description and Microsoft 5,680, against the 15KB and 498KB of shell the
HTML path was collecting. The `?domain=` parameter every tenant's own front end
sends turns out to be optional, and is omitted here rather than guessed at.

Deliberately not a host allowlist. Eightfold has many tenants on their own
vanity domains and enumerating them would mean this only ever works for the two
that were reported. The URL shape is the trigger and the API's own answer is the
test: anything that is not a job comes back 404 or without a description, and
the caller falls through to the ordinary fetch having lost one request.
"""
from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
import structlog

log = structlog.get_logger(__name__)

# Both known tenants use this path. The id is Eightfold's own numeric posting
# id, which is what the API is keyed on.
_JOB_URL_RE = re.compile(r"/careers/job/(\d{6,})")

# Short: this runs ahead of the ordinary fetch, so its whole cost is added to
# every posting that turns out not to be Eightfold. A tenant that cannot answer
# in this long is one worth giving up on in favour of the path that works.
_TIMEOUT_SECONDS = 8.0

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")


@dataclass(frozen=True, slots=True)
class EightfoldJob:
    title: str | None
    company_hint: str | None
    text: str
    raw: str


def job_api_url(url: str) -> str | None:
    """The API URL for a posting, or None when this is not that shape."""
    try:
        parts = urlsplit(url if "://" in url else "https://" + url)
    except ValueError:
        return None
    host = (parts.hostname or "").strip()
    if not host:
        return None
    match = _JOB_URL_RE.search(parts.path or "")
    if not match:
        return None
    return f"https://{host}/api/apply/v2/jobs/{match.group(1)}"


def _to_text(markup: str) -> str:
    """The description as readable text.

    `job_description` is a fragment of HTML, mostly `<p>` and `<b>`. Block tags
    become newlines first so that headings and list items do not run into the
    sentence after them, which is what a requirement extractor reads as one long
    line and mines nothing out of.
    """
    text = re.sub(r"(?i)<br\s*/?>", "\n", markup)
    # `</li>` is deliberately absent: the opening tag below already starts the
    # line, and closing it too put a blank line between every bullet, which
    # turns a five-item requirements list into ten lines of mostly nothing.
    text = re.sub(r"(?i)</(p|div|h[1-6]|tr|ul|ol)>", "\n", text)
    text = re.sub(r"(?i)<li[^>]*>", "\n- ", text)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    return _BLANK_LINES_RE.sub("\n\n", text).strip()


async def fetch_job(url: str) -> EightfoldJob | None:
    """The posting behind an Eightfold URL, or None to fall through.

    Never raises. This sits in front of the ordinary fetch path, and a posting
    that fails here has to still be fetchable the usual way: turning a
    recoverable page into an error would be a worse outcome than the shell text
    this exists to replace.
    """
    api = job_api_url(url)
    if api is None:
        return None
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            response = await client.get(
                api,
                headers={"Accept": "application/json", "User-Agent": "job-os/0.1"},
            )
        if response.status_code != 200:
            return None
        payload = response.json()
    except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
        log.info("eightfold.miss", url=url, error=repr(exc)[:160])
        return None

    if not isinstance(payload, dict):
        return None
    description = payload.get("job_description")
    if not isinstance(description, str) or not description.strip():
        # A 200 without a description is some other API answering on this path.
        # Falling through is the honest response to that.
        return None

    text = _to_text(description)
    if not text:
        return None

    title = payload.get("name") or payload.get("posting_name")
    location = payload.get("location")
    # The title is not always inside `job_description`, and a parser that never
    # sees the role's name has to infer it from the body. Prepended rather than
    # relied upon: `jd_parse` also takes a `title_hint`.
    header = "\n".join(part for part in (title, location) if isinstance(part, str) and part)
    if header and not text.startswith(str(title or "")):
        text = f"{header}\n\n{text}"

    log.info("eightfold.hit", url=url, chars=len(text))
    return EightfoldJob(
        title=title if isinstance(title, str) else None,
        company_hint=_company_hint(payload, url),
        text=text,
        raw=json.dumps(payload)[:200_000],
    )


def _company_hint(payload: dict[str, object], url: str) -> str | None:
    """The employer, from the tenant's own domain rather than the posting.

    Eightfold does not name the company on the job record: the tenant IS the
    company. `campusjobs.mlp.com` is Millennium and `apply.careers.microsoft.com`
    is Microsoft, and neither string appears in a field of its own.
    """
    for key in ("company", "company_name"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    host = (urlsplit(url).hostname or "").lower()
    labels = [label for label in host.split(".") if label]
    if len(labels) >= 2:
        return labels[-2].replace("-", " ").title()
    return None
