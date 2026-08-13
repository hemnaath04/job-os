"""Ashby job boards.

`GET api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true`
returns `{"jobs": [...], "apiVersion": "..."}`.

Compensation is the fiddly part. Measured on job-board/ramp, salary arrives as:

    "compensation": {
      "compensationTierSummary": "$211.4K - $290.6K - Offers Equity",
      "scrapeableCompensationSalarySummary": "$211.4K - $290.6K",
      "compensationTiers": [{"components": [
          {"compensationType": "Salary", "interval": "1 YEAR",
           "currencyCode": "USD", "minValue": 211400, "maxValue": 290600},
          {"compensationType": "EquityPercentage", ...}]}]
    }

so the numbers live two levels down in `components`, mixed in with equity rows
that have null values. `_salary_from_compensation` walks tiers and keeps only
the Salary component. It also accepts `compensationTiers` at the top level of
the job object, because the field has been documented both ways and the cost of
handling both is three lines.

Other quirks:
  * `isRemote` reads true on most postings including hybrid ones, so
    `workplaceType` is the trustworthy signal.
  * `address.postalAddress.addressCountry` is a human name ("USA", "Canada"),
    not an ISO code, so it goes through the same inference as a location label.
  * `isListed: false` means the posting is hidden on the board; those are skipped.
"""
from __future__ import annotations

from typing import Any

from job_os.ingest import normalize
from job_os.ingest.providers.base import BoardResult, BoardStatus, RawPosting

NAME = "ashby"
HOST = "api.ashbyhq.com"


class AshbyProvider:
    name = NAME
    host = HOST

    def board_url(self, token: str) -> str:
        return f"https://jobs.ashbyhq.com/{token}"

    def api_url(self, token: str) -> str:
        return (
            f"https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true"
        )

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
        if response.status_code in (404, 400):
            # Ashby answers an unknown board with a client error rather than an
            # empty list, so both codes mean "prune this token".
            return BoardResult(
                provider=NAME,
                token=token,
                status=BoardStatus.MISSING,
                http_status=response.status_code,
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
    if raw.get("isListed") is False:
        return None
    external_id = raw.get("id")
    title = (raw.get("title") or "").strip()
    url = raw.get("jobUrl") or raw.get("applyUrl") or ""
    if not external_id or not title or not url:
        return None

    workplace = _text(raw.get("workplaceType"))
    remote = bool(workplace and workplace.lower() == "remote")
    location = _text(raw.get("location"))
    # Surface remoteness in the label when it is not already there, otherwise a
    # remote-only search silently drops the row.
    if remote and (not location or "remote" not in location.lower()):
        location = f"Remote - {location}" if location else "Remote"

    country_hint = _country_from_address(raw.get("address"))
    body = raw.get("descriptionPlain")
    jd_clean = (
        normalize.plain_text(body)
        if body
        else normalize.html_to_text(raw.get("descriptionHtml"))
    )
    salary = _salary_from_compensation(raw)

    return RawPosting(
        source=NAME,
        board_token=token,
        external_id=str(external_id),
        title=title,
        company_name=token,
        source_url=url,
        jd_clean=jd_clean,
        jd_raw=raw.get("descriptionHtml") or "",
        location=location,
        country_code=(
            normalize.infer_country_code(country_hint)
            or normalize.infer_country_code(location)
        ),
        remote=normalize.is_remote(location, explicit=remote or None),
        anywhere=normalize.is_anywhere(location),
        workplace_type=workplace,
        employment_type=_text(raw.get("employmentType")),
        department=_text(raw.get("department")) or _text(raw.get("team")),
        posted_at=normalize.to_datetime(raw.get("publishedAt")),
        posted_at_basis="published",
        salary_min=salary[0],
        salary_max=salary[1],
        salary_currency=salary[2],
        salary_interval=salary[3],
        extra={
            "team": _text(raw.get("team")),
            "secondary_locations": _secondary_locations(raw.get("secondaryLocations")),
            "compensation_summary": _compensation_summary(raw),
        },
    )


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _country_from_address(address: object) -> str | None:
    if not isinstance(address, dict):
        return None
    postal = address.get("postalAddress")
    if not isinstance(postal, dict):
        return None
    return _text(postal.get("addressCountry"))


def _secondary_locations(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw[:12]:
        if isinstance(item, dict):
            label = _text(item.get("location"))
            if label:
                out.append(label)
    return out


def _compensation_summary(raw: dict[str, Any]) -> str | None:
    comp = raw.get("compensation")
    if isinstance(comp, dict):
        return _text(comp.get("compensationTierSummary")) or _text(
            comp.get("scrapeableCompensationSalarySummary")
        )
    return None


def _tiers(raw: dict[str, Any]) -> list[Any]:
    """Collect compensation tiers from either documented nesting."""
    found: list[Any] = []
    comp = raw.get("compensation")
    if isinstance(comp, dict) and isinstance(comp.get("compensationTiers"), list):
        found.extend(comp["compensationTiers"])
    if isinstance(raw.get("compensationTiers"), list):
        found.extend(raw["compensationTiers"])
    return found


def _salary_from_compensation(
    raw: dict[str, Any],
) -> tuple[int | None, int | None, str | None, str | None]:
    """Widest Salary band across all tiers.

    A posting with several tiers is one job paid differently by location, so the
    honest single-row answer is the full span rather than whichever tier happens
    to be listed first. Equity and bonus components are skipped: they share the
    tier but are not a salary, and their min/max are usually null.
    """
    low: int | None = None
    high: int | None = None
    currency: str | None = None
    interval: str | None = None

    for tier in _tiers(raw):
        if not isinstance(tier, dict):
            continue
        for component in tier.get("components") or []:
            if not isinstance(component, dict):
                continue
            if (component.get("compensationType") or "").lower() != "salary":
                continue
            min_value = component.get("minValue")
            max_value = component.get("maxValue")
            if isinstance(min_value, int | float) and min_value > 0:
                low = int(min_value) if low is None else min(low, int(min_value))
            if isinstance(max_value, int | float) and max_value > 0:
                high = int(max_value) if high is None else max(high, int(max_value))
            code = component.get("currencyCode")
            if currency is None and isinstance(code, str) and len(code) >= 3:
                currency = code.upper()[:3]
            span = component.get("interval")
            if interval is None and isinstance(span, str) and span.strip():
                interval = span.strip()

    return low, high, currency, interval
