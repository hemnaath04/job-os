"""Lever job boards.

`GET api.lever.co/v0/postings/{co}?mode=json` returns a bare JSON array, with no
envelope object. Measured on postings/palantir: 309 postings, 5,970,962 bytes,
which makes Lever the heaviest per-board payload of the four and the provider
that gains most from conditional GET.

Two traps:

  * `createdAt` is epoch MILLISECONDS (observed 1711403416463). Read as seconds
    that is 1970, so every Lever posting would look 55 years old and be dropped
    by any freshness filter. `normalize.to_datetime` handles the discriminator.
  * A bad slug answers 404 with `{"ok": false, "error": "Document not found"}`,
    an object rather than an array. Guarding only on the status code and then
    iterating the payload would treat the error object's keys as postings, so
    the shape is checked too.

Lever is also the one provider that hands over a real ISO country code, so its
`country` field is trusted ahead of inference from the location label.
"""
from __future__ import annotations

from typing import Any

from job_os.ingest import normalize
from job_os.ingest.providers.base import (
    BoardResult,
    BoardStatus,
    RawPosting,
    as_dict,
    as_list,
)

NAME = "lever"
HOST = "api.lever.co"


class LeverProvider:
    name = NAME
    host = HOST

    def board_url(self, token: str) -> str:
        return f"https://jobs.lever.co/{token}"

    def api_url(self, token: str) -> str:
        return f"https://api.lever.co/v0/postings/{token}?mode=json"

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
        if not isinstance(payload, list):
            # A 200 that is not an array is Lever's soft-error shape. An empty
            # board and a soft error must not look the same.
            message = "unexpected payload shape"
            if isinstance(payload, dict) and isinstance(payload.get("error"), str):
                message = payload["error"]
            return BoardResult(
                provider=NAME,
                token=token,
                status=BoardStatus.ERROR,
                http_status=200,
                bytes_fetched=response.bytes_read,
                requests_made=response.requests_made,
                error=message,
            )

        postings = [p for raw in payload if (p := parse_posting(token, raw)) is not None]
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
    title = (raw.get("text") or "").strip()
    url = raw.get("hostedUrl") or raw.get("applyUrl") or ""
    if not external_id or not title or not url:
        return None

    categories = as_dict(raw.get("categories"))
    salary_range = as_dict(raw.get("salaryRange"))
    location = _text(categories.get("location"))
    workplace = _text(raw.get("workplaceType"))

    body = raw.get("descriptionPlain")
    jd_clean = (
        normalize.plain_text(body) if body else normalize.html_to_text(raw.get("description"))
    )

    country = _text(raw.get("country"))
    return RawPosting(
        source=NAME,
        board_token=token,
        external_id=str(external_id),
        title=title,
        company_name=token,
        source_url=url,
        jd_clean=jd_clean,
        jd_raw=raw.get("description") or "",
        location=location,
        # Lever states the country outright. Trust it over inference, but only
        # when it looks like an alpha-2 code rather than free text.
        country_code=(
            country.upper()
            if country and len(country) == 2
            else normalize.infer_country_code(location)
        ),
        remote=normalize.is_remote(
            location, explicit=True if workplace and workplace.lower() == "remote" else None
        ),
        anywhere=normalize.is_anywhere(location),
        workplace_type=workplace,
        employment_type=_text(categories.get("commitment")),
        department=_text(categories.get("team")),
        posted_at=normalize.to_datetime(raw.get("createdAt")),
        posted_at_basis="created",
        salary_min=_money(salary_range, "min"),
        salary_max=_money(salary_range, "max"),
        salary_currency=_currency(salary_range),
        salary_interval=_text(salary_range.get("interval")),
        extra={
            "all_locations": [
                loc for loc in as_list(categories.get("allLocations")) if isinstance(loc, str)
            ][:8],
            "department_group": _text(categories.get("department")),
        },
    )


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _money(raw: dict[str, Any], key: str) -> int | None:
    value = raw.get(key)
    if isinstance(value, int | float) and value > 0:
        return int(value)
    return None


def _currency(raw: dict[str, Any]) -> str | None:
    code = raw.get("currency")
    return code.upper()[:3] if isinstance(code, str) and len(code) >= 3 else None
