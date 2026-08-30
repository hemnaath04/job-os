"""Workday, via the CXS endpoint its own career sites are built on.

The gap this closes: the four providers beside it cover Greenhouse, Lever,
Ashby and SmartRecruiters, and everything else was reaching this index through
a scraper on a personal VPS. Workday is the largest single vendor missing from
that set, and it needs no browser, so it can run wherever the sweep runs.

    POST https://{tenant}.wd{N}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs
         {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""}
    GET  https://{tenant}.wd{N}.myworkdayjobs.com/wday/cxs/{tenant}/{site}{externalPath}

Everything below was verified live against NVIDIA (wd5), Workiva (wd503) and
Salesforce (wd12) on 2026-08-29, and the awkward parts are awkward because
Workday is, not because of a guess:

* **It is a POST**, and `Content-Type: application/json` is required. Omitting
  the header returns 500, not 415, which is why `PoliteFetcher.post_json`
  exists rather than this reusing the conditional GET the others share. There
  is no ETag on a POST response, so this provider gives up conditional-GET
  savings entirely.
* **`limit` is hard-capped at 20.** 50 returns HTTP 400. A 2,000-posting tenant
  is therefore 100 list requests, which is what `MAX_PAGES` is really bounding.
* **`total` saturates at 2000.** NVIDIA reports exactly 2000 while offset=2000
  still returns rows, so it is a floor and is never treated as a count.
* **The list's `postedOn` is prose** -- "Posted Today", "Posted 30+ Days Ago" --
  with no real date in it. The DETAIL payload's `startDate` is a true ISO date
  (checked: "Posted Today" alongside startDate 2026-08-29). So a list-only row
  is honestly `first_crawl`, and hydration is what upgrades it to `published`.

This is an internal endpoint powering Workday's own front end, not a documented
API, and carries no stability contract. `BoardStatus` is the defence: the two
failure signatures are distinct and both mean "prune this token" rather than
"the board is empty today".
"""
from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from job_os.ingest.providers.base import (
    BoardResult,
    BoardStatus,
    RawPosting,
    as_dict,
)

NAME = "workday"

#: Workday's own cap. 50 returns HTTP 400, so this is the vendor's number.
PAGE_SIZE = 20

#: 100 pages x 20 = 2000 postings, which is where `total` saturates anyway.
#: A tenant genuinely larger than this loses its tail rather than the sweep
#: spending an unbounded number of requests on one board.
MAX_PAGES = 100

#: A token carries three parts, because unlike every other provider here the
#: host is per-tenant and the site is not derivable from it:
#:     nvidia:wd5:NVIDIAExternalCareerSite
_TOKEN_RE = re.compile(r"^(?P<tenant>[a-z0-9][a-z0-9._-]*):(?P<dc>wd\d+):(?P<site>[\w.-]+)$", re.I)

#: `S21` is Workday's own code for "that site id does not exist on this
#: tenant". A bare 404 without it is not necessarily a missing board.
_MISSING_SITE_CODE = "S21"

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")


class WorkdayTokenError(ValueError):
    """A token that cannot address a board. Raised at parse time, not fetch."""


def parse_token(token: str) -> tuple[str, str, str]:
    """`tenant:wdN:site` -> its three parts.

    Deliberately strict. A malformed token that fell through to a request would
    produce a URL pointing at some other tenant's board, and the wildcard DNS on
    `*.myworkdayjobs.com` means that URL would resolve rather than fail.
    """
    match = _TOKEN_RE.match(token.strip())
    if not match:
        raise WorkdayTokenError(
            f"workday token {token!r} must be 'tenant:wdN:site', "
            "e.g. 'nvidia:wd5:NVIDIAExternalCareerSite'"
        )
    return match["tenant"], match["dc"].lower(), match["site"]


def _clean_html(raw: str) -> str:
    """Workday's `jobDescription` is an HTML fragment. Flatten it to text.

    No parser dependency: this is block-level tags to newlines and everything
    else stripped, which is all the read path needs from a description it will
    keyword-match and hand to a model.
    """
    if not raw:
        return ""
    text = re.sub(r"(?i)<br\s*/?>", "\n", raw)
    text = re.sub(r"(?i)</(p|div|li|ul|ol|h[1-6]|tr)>", "\n", text)
    text = re.sub(r"(?i)<li[^>]*>", "\n- ", text)
    text = _TAG_RE.sub("", text)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
    )
    text = _WS_RE.sub(" ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _iso_date(value: Any) -> datetime | None:
    """`startDate` as a datetime, or None. Date-only, so midnight UTC."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class WorkdayProvider:
    name = NAME
    #: Unlike the other providers there is no single API host: every tenant is
    #: its own. `PoliteFetcher.get_json`/`post_json` take `host` per call and
    #: `_host_gate` keys its semaphore on that string, so each tenant gets its
    #: own concurrency ceiling automatically, which is the right politeness
    #: behaviour here. This value is the fallback for accounting only.
    host = "myworkdayjobs.com"

    def host_for(self, token: str) -> str:
        tenant, dc, _site = parse_token(token)
        return f"{tenant}.{dc}.myworkdayjobs.com"

    def board_url(self, token: str) -> str:
        tenant, dc, site = parse_token(token)
        return f"https://{tenant}.{dc}.myworkdayjobs.com/{site}"

    def api_url(self, token: str) -> str:
        tenant, dc, site = parse_token(token)
        return f"https://{tenant}.{dc}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"

    def detail_url(self, token: str, external_path: str) -> str:
        tenant, dc, site = parse_token(token)
        return (
            f"https://{tenant}.{dc}.myworkdayjobs.com/wday/cxs/{tenant}/{site}"
            f"{external_path}"
        )

    async def fetch_board(
        self,
        fetcher: Any,
        token: str,
        etag: str | None = None,
        expect_bytes: int = 0,
    ) -> BoardResult:
        """Page the list endpoint. Descriptions are left for hydration.

        `etag`/`expect_bytes` are accepted to satisfy the Protocol and ignored:
        a POST carries no ETag to send back, so there is nothing to be
        conditional about. Saying so here beats a caller wondering why the
        conditional-GET savings never show up for this provider.
        """
        try:
            tenant, _dc, _site = parse_token(token)
        except WorkdayTokenError as exc:
            return BoardResult(
                provider=NAME, token=token, status=BoardStatus.MISSING, error=str(exc)
            )

        host = self.host_for(token)
        url = self.api_url(token)
        postings: list[RawPosting] = []
        seen: set[str] = set()
        bytes_fetched = 0
        requests_made = 0

        for page in range(MAX_PAGES):
            response = await fetcher.post_json(
                url,
                host=host,
                body={
                    "appliedFacets": {},
                    "limit": PAGE_SIZE,
                    "offset": page * PAGE_SIZE,
                    "searchText": "",
                },
            )
            bytes_fetched += response.bytes_read
            requests_made += response.requests_made

            # Both missing-board shapes, and they are different questions.
            # 422: no such tenant. `*.myworkdayjobs.com` is wildcard DNS, so a
            # wrong tenant resolves and answers rather than failing to connect.
            # 404 + S21: the tenant is real, the site id is not.
            if response.status_code == 422 or (
                response.status_code == 404
                and as_dict(response.payload).get("errorCode") == _MISSING_SITE_CODE
            ):
                return BoardResult(
                    provider=NAME,
                    token=token,
                    status=BoardStatus.MISSING,
                    http_status=response.status_code,
                    bytes_fetched=bytes_fetched,
                    requests_made=requests_made,
                )
            if not response.ok:
                # Pages already collected stay, but the list is incomplete, so
                # ERROR keeps the caller from deactivating what it did not see.
                return BoardResult(
                    provider=NAME,
                    token=token,
                    status=BoardStatus.ERROR,
                    postings=postings,
                    http_status=response.status_code,
                    bytes_fetched=bytes_fetched,
                    requests_made=requests_made,
                    error=response.error or f"HTTP {response.status_code}",
                )

            payload = as_dict(response.payload)
            batch = payload.get("jobPostings")
            if not isinstance(batch, list):
                return BoardResult(
                    provider=NAME,
                    token=token,
                    status=BoardStatus.ERROR,
                    postings=postings,
                    http_status=response.status_code,
                    bytes_fetched=bytes_fetched,
                    requests_made=requests_made,
                    error="jobPostings missing from payload",
                )
            if not batch:
                break

            for entry in batch:
                posting = self._to_posting(token, tenant, as_dict(entry))
                # Multi-site tenants serve the same requisition under sibling
                # sites, so one board can repeat a path across pages.
                if posting is not None and posting.external_id not in seen:
                    seen.add(posting.external_id)
                    postings.append(posting)

            if len(batch) < PAGE_SIZE:
                break

        return BoardResult(
            provider=NAME,
            token=token,
            status=BoardStatus.LIVE if postings else BoardStatus.EMPTY,
            postings=postings,
            http_status=200,
            bytes_fetched=bytes_fetched,
            requests_made=requests_made,
        )

    def _to_posting(self, token: str, tenant: str, entry: dict[str, Any]) -> RawPosting | None:
        external_path = str(entry.get("externalPath") or "").strip()
        title = str(entry.get("title") or "").strip()
        if not external_path or not title:
            return None

        # The requisition id is the trailing `_JR2022858` on the path. It is
        # the same value the detail payload calls `jobReqId`, so a hydrated row
        # and a list-only row agree on identity, which is what stops hydration
        # from creating a second copy of every posting.
        tail = external_path.rsplit("/", 1)[-1]
        external_id = tail.rsplit("_", 1)[-1] if "_" in tail else tail

        location = str(entry.get("locationsText") or "").strip() or None
        # "4 Locations" is a count, not a place. Better to say nothing than to
        # index a job as being in a city called "4 Locations".
        if location and re.fullmatch(r"\d+\s+Locations?", location, re.I):
            location = None

        bullets = [str(b).strip() for b in (entry.get("bulletFields") or []) if str(b).strip()]

        return RawPosting(
            source=NAME,
            board_token=token,
            external_id=external_id,
            title=title,
            company_name=tenant,
            source_url=self.board_url(token) + external_path,
            # The list carries no description. `jd_hydrated=False` is what tells
            # the read path this row can be ranked on its title but not scored
            # on its body, and what the hydration pass looks for.
            jd_clean=" | ".join([title, *bullets]),
            location=location,
            remote=bool(location and "remote" in location.lower()),
            jd_hydrated=False,
            # `postedOn` is prose with no date in it. Claiming `published` here
            # would mean recording "Posted 30+ Days Ago" as today.
            posted_at_basis="first_crawl",
            extra={"external_path": external_path},
        )

    async def hydrate(
        self, fetcher: Any, token: str, posting: RawPosting
    ) -> RawPosting:
        """Fill in one posting's description and real posted date.

        Separate from `fetch_board` for the reason SmartRecruiters is: a board
        of 2,000 postings is 2,000 extra requests, which is a decision for the
        caller's budget rather than something a list crawl should do on its own.
        """
        path = str(posting.extra.get("external_path") or "")
        if not path:
            return posting

        response = await fetcher.get_json(self.detail_url(token, path), host=self.host_for(token))
        if not response.ok:
            return posting

        info = as_dict(as_dict(response.payload).get("jobPostingInfo"))
        description = _clean_html(str(info.get("jobDescription") or ""))
        if not description:
            return posting

        posted = _iso_date(info.get("startDate"))
        posting.jd_raw = str(info.get("jobDescription") or "")
        posting.jd_clean = description
        posting.jd_hydrated = True
        if posted is not None:
            posting.posted_at = posted
            # Now it IS the employer's own date rather than the day we looked.
            posting.posted_at_basis = "published"
        if info.get("externalUrl"):
            posting.source_url = str(info["externalUrl"])
        if info.get("jobReqId"):
            posting.external_id = str(info["jobReqId"])
        return posting
