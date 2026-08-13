"""Greenhouse job boards.

`GET boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true` returns the
entire board, descriptions included, in one request. No pagination, no key.

Two measured facts drive the code:

  * `content` is entity-encoded HTML. Observed on boards/vercel: the body starts
    `&lt;div class=&quot;content-intro&quot;&gt;` and contains no raw `<` at all,
    so it needs one unescape pass before tags can be stripped.
  * `first_published` was present on 84/84 postings on that board, so the common
    case is a real publish date rather than an estimate. `updated_at` is the
    fallback, and a row that had to use it is marked as an estimate.

A missing token answers 404 with a JSON error body, which is an unambiguous
"prune this from the corpus".
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from job_os.ingest import normalize
from job_os.ingest.providers.base import (
    BoardResult,
    BoardStatus,
    RawPosting,
    as_dict,
    as_list,
)

NAME = "greenhouse"
HOST = "boards-api.greenhouse.io"


class GreenhouseProvider:
    name = NAME
    host = HOST

    def board_url(self, token: str) -> str:
        return f"https://boards.greenhouse.io/{token}"

    def api_url(self, token: str) -> str:
        return f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"

    async def fetch_board(
        self,
        fetcher: Any,
        token: str,
        etag: str | None = None,
        expect_bytes: int = 0,
    ) -> BoardResult:
        response = await fetcher.get_json(
            self.api_url(token), host=HOST, etag=etag, expect_bytes=expect_bytes
        )
        if response.not_modified:
            return BoardResult(
                provider=NAME,
                token=token,
                status=BoardStatus.NOT_MODIFIED,
                http_status=304,
                etag=etag,
                bytes_fetched=response.bytes_read,
                requests_made=response.requests_made,
            )
        if response.status_code == 404:
            return BoardResult(
                provider=NAME,
                token=token,
                status=BoardStatus.MISSING,
                http_status=404,
                bytes_fetched=response.bytes_read,
                requests_made=response.requests_made,
            )
        if not response.ok:
            return BoardResult(
                provider=NAME,
                token=token,
                status=BoardStatus.ERROR,
                http_status=response.status_code,
                bytes_fetched=response.bytes_read,
                requests_made=response.requests_made,
                error=response.error or f"HTTP {response.status_code}",
            )

        payload = response.payload
        if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
            return BoardResult(
                provider=NAME,
                token=token,
                status=BoardStatus.ERROR,
                http_status=200,
                bytes_fetched=response.bytes_read,
                requests_made=response.requests_made,
                error="unexpected payload shape",
            )

        postings = [
            p for raw in payload["jobs"] if (p := parse_posting(token, raw)) is not None
        ]
        return BoardResult(
            provider=NAME,
            token=token,
            status=BoardStatus.LIVE if postings else BoardStatus.EMPTY,
            postings=postings,
            http_status=200,
            etag=response.etag,
            bytes_fetched=response.bytes_read,
            requests_made=response.requests_made,
        )


def parse_posting(token: str, raw: Any) -> RawPosting | None:
    if not isinstance(raw, dict):
        return None
    external_id = raw.get("id")
    title = (raw.get("title") or "").strip()
    url = raw.get("absolute_url") or ""
    if external_id is None or not title or not url:
        return None

    offices = [office for office in as_list(raw.get("offices")) if isinstance(office, dict)]
    first_office = as_dict(offices[0]) if offices else {}
    location = _first_text(
        as_dict(raw.get("location")).get("name"),
        first_office.get("location"),
        first_office.get("name"),
    )

    published = normalize.to_datetime(raw.get("first_published"))
    posted_at: datetime | None = published
    basis = "published"
    if published is None:
        # No publish date. `updated_at` is an upper bound on when it went up,
        # never the posting date itself, so the row says so.
        posted_at, basis = normalize.to_datetime(raw.get("updated_at")), "updated"

    body = normalize.html_to_text(raw.get("content"))
    departments = [d for d in as_list(raw.get("departments")) if isinstance(d, dict)]
    department = departments[0].get("name") if departments else None

    return RawPosting(
        source=NAME,
        board_token=token,
        external_id=str(external_id),
        title=title,
        company_name=(raw.get("company_name") or token).strip() or token,
        source_url=url,
        jd_clean=body,
        jd_raw=raw.get("content") or "",
        location=location,
        country_code=normalize.infer_country_code(location),
        remote=normalize.is_remote(location),
        anywhere=normalize.is_anywhere(location),
        department=department,
        posted_at=posted_at,
        posted_at_basis=basis,
        closes_at=normalize.to_datetime(raw.get("application_deadline")),
        extra={
            "requisition_id": raw.get("requisition_id"),
            "internal_job_id": raw.get("internal_job_id"),
            "all_offices": [
                o.get("location") or o.get("name")
                for o in offices
                if o.get("location") or o.get("name")
            ][:8],
            "metadata": _metadata_pairs(raw.get("metadata")),
        },
    )


def _first_text(*candidates: object) -> str | None:
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


def _metadata_pairs(raw: object) -> dict[str, str]:
    """Greenhouse custom fields, flattened to name -> value.

    Boards use these for the things a searcher actually filters on (Vercel files
    "Career Site Categories": "Sales" here), so they are worth keeping even
    though the vocabulary differs per company.
    """
    if not isinstance(raw, list):
        return {}
    out: dict[str, str] = {}
    for item in raw[:20]:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        value = item.get("value")
        if isinstance(name, str) and isinstance(value, str) and value.strip():
            out[name.strip()[:60]] = value.strip()[:120]
    return out
