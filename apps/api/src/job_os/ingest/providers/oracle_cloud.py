"""Oracle Fusion Cloud Recruiting, via the REST resource its career sites call.

The gap this closes: Workday brought the large-employer half of the index in
reach without a browser, and Oracle Recruiting Cloud is the next vendor of that
size. TheirStack counts 5,444 companies on it worldwide and 1,153 in North
America; AppsRunTheWorld's breakdown puts 48% of them in the 1,001-10,000
employee band and names Goldman Sachs, UnitedHealth Group, JPMorganChase,
Albertsons and StoneX. Goldman Sachs, Albertsons, Marriott, AutoZone, Cummins,
Sherwin-Williams, Citizens Financial and Oracle itself were confirmed by
calling their boards and are in `seeds/oracle_cloud.txt`; none of them was
reachable from this index before, and all of them hire engineers.

    GET https://{tenant}.fa.{dc}.oraclecloud.com/hcmRestApi/resources/latest
        /recruitingCEJobRequisitions
        ?onlyData=true&expand=requisitionList.secondaryLocations
        &finder=findReqs;siteNumber={site},limit=200,offset=0
    GET .../recruitingCEJobRequisitionDetails
        ?onlyData=true&expand=all&finder=ById;Id="{id}",siteNumber={site}

Everything below was measured live on 2026-08-30 against Citizens Financial
(hcgn, us2, 461 jobs), Oracle itself (eeho, us2, 2,173), Goldman Sachs (hdpc,
us2, 1,012), Marriott (ejwl, us2, 13,182) and AutoZone (egud, us2, 11,178):

* **`expand=requisitionList...` is not optional.** Without it the response is
  still 200 and still has every facet and `TotalJobsCount`, but
  `requisitionList` is `null`. A crawler that dropped the parameter would read
  every board on earth as empty while getting 200s the whole way.
* **`limit` is capped at 200.** `limit=500` returns 200 rows, silently, with no
  error. Unlike Workday's `limit=50` -> HTTP 400 there is nothing to notice.
* **A short page is NOT the end of the list**, which is the trap that cost this
  provider 174 of Oracle's own 2,173 postings on its first live run. `limit=200`
  at `offset=1800` returned 199 rows while `offset=2000` still returned 171.
  Every other provider here stops on a short page; this one must stop only on
  an empty one.
* **`TotalJobsCount` is a real count** on the first page -- hcgn claimed 461 and
  `offset=450` returned exactly 11 -- so it is used to stop early once that many
  postings are in hand. It is read from the FIRST page only: at `offset=10000`
  Marriott answered 200 with `TotalJobsCount: 0` while reporting 13,184 on page
  one, so a later page's count is not evidence of anything.
* **There is no ETag.** The response carries
  `cache-control: no-cache, no-store, must-revalidate` and no validator, so
  this provider gives up conditional-GET savings entirely, as Workday does.
* **The list date is real.** `PostedDate` on the list ("2026-08-29") matched the
  date part of the detail's `ExternalPostedStartDate`
  ("2026-08-29T13:00:57+00:00") on 4 of 4 requisitions checked on hcgn. So
  unlike Workday, whose list `postedOn` is the prose "Posted Today", a
  list-only row here honestly carries `published`. Hydration only sharpens the
  date from a day to a timestamp; it does not change what the date means.
* **The list carries no description.** `ShortDescriptionStr` was empty on all
  200 rows of the hcgn page and populated on Oracle's own board, so it is a
  per-board authoring choice, not a field to rely on. Hence `jd_hydrated=False`.
* **Nobody is named.** `LegalEmployer`, `Organization` and `BusinessUnit` were
  null on every requisition and every detail checked across three tenants. See
  `_company_from` for what this provider does instead, and why.

Stability risk is the same shape as Workday's and worth stating plainly: Oracle
documents `recruitingCEJobRequisitions` as being "only for Oracle internal
use", which is a stronger disclaimer than Workday's CXS endpoint carries. It is
the endpoint Oracle's own candidate front end calls on every career site, so it
is not going to move quietly, but there is no contract behind it.

## Finding a token

The hard half of an Oracle token is the site, not the tenant. Two recipes,
both used to build `seeds/oracle_cloud.txt`:

1. **Tenant, from the employer's own careers page.** Fetch it, follow
   redirects and regex the body for
   `{tenant}.fa.{dc}.oraclecloud.com/hcmUI/CandidateExperience/.../sites/{site}`.
   This resolved 6 of 20 large US employers on the first try, including
   Albertsons (`eofd:us6:CX_1001`) and Cummins
   (`fa-espx-saasfaprod1:ocs:CX_1`).
2. **Site, from the tenant.** `GET /hcmRestApi/resources/latest/recruitingCESites`
   is unauthenticated too and lists every site on a tenant with its
   `SiteNumber`. That is how `egud`'s four sites and `hdpc`'s eight were
   enumerated, and it is the only way to tell a real site from a typo, since a
   typo answers 200 (see `parse_token`). Its `SiteName` is a site label rather
   than an employer name -- measured across 11 tenants it was the real employer
   5 times ("AutoZone", "Sherwin-Williams"), a decorated variant twice
   ("Careers at Marriott", "Cummins Talent Acquisition") and Oracle's stock
   "Candidate Experience site" 4 times -- which is why it is a discovery aid
   here and not something the crawl parses.

Both recipes are manual on purpose. Nothing in this module enumerates
anything: `recruitingCESites` is one more request per board for a name the
crawl does not need, and the tenant regex belongs in whatever builds the seed
list, not in the hot path of a sweep.
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
    as_list,
)

NAME = "oracle_cloud"

#: Oracle's own ceiling. `limit=500` returns 200 rows rather than an error, so
#: asking for more than this buys nothing and hides the truncation.
PAGE_SIZE = 200

#: 100 pages x 200 = 20,000 postings. The largest board found while building
#: the seed list was Marriott at 13,180, so this is headroom rather than a
#: limit anyone hits; a board genuinely past it loses its tail rather than the
#: sweep spending an unbounded number of requests on one employer.
MAX_PAGES = 100

#: Three parts, for the same reason Workday's token has three: the host is
#: per-tenant and the site cannot be derived from it.
#:     hcgn:us2:CX_1
#: The tenant is usually a four-character code but not always -- Cummins is
#: `fa-espx-saasfaprod1` -- so the tenant pattern is deliberately loose while
#: the datacenter is not.
_TOKEN_RE = re.compile(
    r"^(?P<tenant>[a-z0-9][a-z0-9-]{1,62}):(?P<dc>[a-z]{2}[a-z0-9]{0,4}):"
    r"(?P<site>[A-Za-z0-9_.-]{1,64})$",
    re.I,
)

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")

#: `PrimaryLocationCountry` is an ISO alpha-2 on every row seen, but it shares
#: a payload with free-text location fields, so it is checked rather than
#: trusted.
_ALPHA2_RE = re.compile(r"^[A-Za-z]{2}$")

#: Oracle's workplace codes. Anything outside this set is passed through as-is
#: rather than dropped, because the list of codes is Oracle's to extend.
_WORKPLACE = {
    "ORA_REMOTE": "remote",
    "ORA_HYBRID": "hybrid",
    "ORA_ONSITE": "onsite",
}


class OracleCloudTokenError(ValueError):
    """A token that cannot address a board. Raised at parse time, not fetch."""


def parse_token(token: str) -> tuple[str, str, str]:
    """`tenant:dc:site` -> its three parts.

    Strict, and for a sharper reason than Workday's. A wrong *tenant* here
    fails loudly (see `fetch_board`), but a wrong *site* on a real tenant does
    not fail at all, and it does not return nothing either: Oracle serves the
    tenant's whole unfiltered requisition pool, which is a SUPERSET of any real
    site. Measured, and the arithmetic is exact:

        Goldman Sachs (hdpc)  CX_3002 1012 + CX_3001 317 + CX 21 = 1350
                              siteNumber=ZZ_NOT_A_SITE           = 1350
        Marriott (ejwl)       CX 13182 + CX_1001 80 + CX_1 8     = 13270
                              siteNumber=ZZ_NOT_A_SITE           = 13270

    So a typo does not look like an error, it looks like a bigger board: it
    silently merges sites the employer deliberately separated (Goldman's campus
    pipeline into its lateral one) and files the lot under a token naming
    neither. That is why site numbers in `seeds/oracle_cloud.txt` were each
    checked against `recruitingCESites` rather than guessed.
    """
    match = _TOKEN_RE.match(token.strip())
    if not match:
        raise OracleCloudTokenError(
            f"oracle_cloud token {token!r} must be 'tenant:datacenter:site', "
            "e.g. 'hcgn:us2:CX_1'"
        )
    return match["tenant"], match["dc"].lower(), match["site"]


def _clean_html(raw: str) -> str:
    """Oracle's description fields are HTML fragments. Flatten them to text.

    Same shape as the Workday provider's, and deliberately not shared with it:
    the two vendors emit different tag soup (Oracle's comes out of a Word-style
    editor, full of `<span style>` and `MsoNormal`), and a single helper would
    have to grow a flag the first time one of them changed.
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
    text = "\n".join(line.strip() for line in text.splitlines())
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _posted_date(value: Any) -> datetime | None:
    """`PostedDate` / `ExternalPostedStartDate` as a datetime, or None.

    Handles both shapes the two endpoints use: the list's date-only
    "2026-08-29" and the detail's "2026-08-29T13:00:57+00:00". A date-only
    value becomes midnight UTC, which is the same convention the Workday
    provider uses for `startDate`.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class OracleCloudProvider:
    name = NAME
    #: As with Workday there is no single API host: every tenant is its own.
    #: `PoliteFetcher` keys its per-host semaphore on the string passed per
    #: call, so `host_for` is what actually gates politeness and this value is
    #: the fallback for accounting only.
    host = "oraclecloud.com"

    def host_for(self, token: str) -> str:
        tenant, dc, _site = parse_token(token)
        return f"{tenant}.fa.{dc}.oraclecloud.com"

    def board_url(self, token: str) -> str:
        _tenant, _dc, site = parse_token(token)
        return (
            f"https://{self.host_for(token)}/hcmUI/CandidateExperience/en/sites/{site}"
            "/requisitions"
        )

    def api_url(self, token: str, offset: int) -> str:
        _tenant, _dc, site = parse_token(token)
        return (
            f"https://{self.host_for(token)}/hcmRestApi/resources/latest"
            "/recruitingCEJobRequisitions?onlyData=true"
            "&expand=requisitionList.secondaryLocations"
            f"&finder=findReqs;siteNumber={site},limit={PAGE_SIZE},offset={offset}"
            ",sortBy=POSTING_DATES_DESC"
        )

    def detail_url(self, token: str, external_id: str) -> str:
        _tenant, _dc, site = parse_token(token)
        # The id is quoted inside the finder expression, and the quotes have to
        # be percent-encoded: Oracle's finder syntax is a query-string value
        # that happens to contain its own punctuation.
        return (
            f"https://{self.host_for(token)}/hcmRestApi/resources/latest"
            "/recruitingCEJobRequisitionDetails?onlyData=true&expand=all"
            f"&finder=ById;Id=%22{external_id}%22,siteNumber={site}"
        )

    def job_url(self, token: str, external_id: str) -> str:
        """The page a human would land on. Verified 200 for a live requisition."""
        _tenant, _dc, site = parse_token(token)
        return (
            f"https://{self.host_for(token)}/hcmUI/CandidateExperience/en/sites/{site}"
            f"/job/{external_id}"
        )

    async def fetch_board(
        self,
        fetcher: Any,
        token: str,
        etag: str | None = None,
        expect_bytes: int = 0,
    ) -> BoardResult:
        """Page the list endpoint. Descriptions are left for hydration.

        `etag`/`expect_bytes` are accepted to satisfy the Protocol and ignored.
        Oracle sends `no-cache, no-store, must-revalidate` and no validator, so
        there is nothing to make the GET conditional on and no saving to
        report. Saying so beats a caller wondering why this provider's
        `bytes_saved_estimate` is always zero.
        """
        try:
            tenant, _dc, _site = parse_token(token)
        except OracleCloudTokenError as exc:
            return BoardResult(
                provider=NAME, token=token, status=BoardStatus.MISSING, error=str(exc)
            )

        host = self.host_for(token)
        postings: list[RawPosting] = []
        seen: set[str] = set()
        bytes_fetched = 0
        requests_made = 0
        #: `TotalJobsCount` off the FIRST page. Later pages contradict it -- at
        #: offset=10000 Marriott reported 0 while page one said 13,184 -- so it
        #: is captured once and never revised.
        expected: int | None = None

        for page in range(MAX_PAGES):
            response = await fetcher.get_json(self.api_url(token, page * PAGE_SIZE), host=host)
            bytes_fetched += response.bytes_read
            requests_made += response.requests_made

            # The one status treated as a dead board, and the reason it is the
            # only one. Measured against a real tenant, a path that does not
            # name a real REST resource answers 404 with an empty body -- so a
            # 404 here means the recruiting resource is not on this host.
            #
            # What is deliberately NOT missing: a tenant that does not exist at
            # all answers **504** from Oracle's Akamai edge, reproducibly
            # (zzzz/qqqq/xxyy across us2 and us6). 504 is in the fetcher's
            # retry set, so a dead tenant costs three requests and lands here
            # as an ERROR. That is the honest answer -- ERROR retries a board,
            # MISSING deletes it -- but it does mean Oracle gives this crawler
            # no cheap way to prune a decommissioned tenant, and a token nobody
            # verified will sit in the corpus burning three requests a sweep.
            if response.status_code == 404:
                return BoardResult(
                    provider=NAME,
                    token=token,
                    status=BoardStatus.MISSING,
                    http_status=404,
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

            # The payload is `{"items": [ {one search result object} ]}`. The
            # postings are two levels down, inside that object.
            items = as_list(as_dict(response.payload).get("items"))
            if not items:
                return BoardResult(
                    provider=NAME,
                    token=token,
                    status=BoardStatus.ERROR,
                    postings=postings,
                    http_status=response.status_code,
                    bytes_fetched=bytes_fetched,
                    requests_made=requests_made,
                    error="items missing from payload",
                )
            result_set = as_dict(items[0])
            if expected is None:
                count = result_set.get("TotalJobsCount")
                expected = count if isinstance(count, int) and count > 0 else None
            batch = result_set.get("requisitionList")
            if not isinstance(batch, list):
                # `null` here is what dropping `expand=requisitionList...`
                # looks like, and it arrives as a 200. Calling it an error
                # rather than an empty board is the difference between
                # noticing that mistake and silently emptying the index.
                return BoardResult(
                    provider=NAME,
                    token=token,
                    status=BoardStatus.ERROR,
                    postings=postings,
                    http_status=response.status_code,
                    bytes_fetched=bytes_fetched,
                    requests_made=requests_made,
                    error="requisitionList missing from payload (expand dropped?)",
                )
            if not batch:
                # The only end-of-list signal Oracle gives that can be
                # trusted. Its cost, stated rather than hidden: if a page came
                # back empty in the MIDDLE of a list, this would return LIVE
                # with a truncated list and the caller would deactivate the
                # tail. No such page was observed while building the seed
                # list, and the alternative -- treating a legitimately
                # exhausted board as an error every sweep -- is worse.
                break

            added = 0
            for entry in batch:
                posting = self._to_posting(token, tenant, as_dict(entry))
                if posting is not None and posting.external_id not in seen:
                    seen.add(posting.external_id)
                    postings.append(posting)
                    added += 1

            # Deliberately NOT `len(batch) < PAGE_SIZE`, which is how every
            # other provider here ends its loop. Oracle serves short pages in
            # the MIDDLE of a list: `offset=1800` on Oracle's own board
            # returned 199 rows with 371 postings still to come after it. Only
            # an empty page (handled above) is the end.
            if expected is not None and len(postings) >= expected:
                # Page one's own count, reached. Saves the request that would
                # otherwise be spent proving the next page is empty.
                break
            if added == 0:
                # A full page that contributed nothing new means `offset` is
                # not advancing the window. MAX_PAGES already bounds the
                # damage, but without this a board that ignored `offset` would
                # cost 100 requests to learn nothing.
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

    def _company_from(self, tenant: str) -> str:
        """The employer, as far as the payload allows: the tenant code.

        This is the provider's weakest field and it is worth being blunt about.
        Oracle names nobody -- `LegalEmployer`, `Organization` and
        `BusinessUnit` were null on every requisition and detail checked across
        three tenants -- and unlike Workday, whose tenant IS the company
        ("nvidia", "adobe"), an Oracle tenant is an opaque four-character code
        like "hcgn". So this returns "hcgn", which is useless to a reader.

        The fix is the one `upsert.to_row` already implements for Lever and
        Ashby, which have the same problem: a curated entry's `company_name`
        overrides this. Every token in `seeds/oracle_cloud.txt` has a matching
        row in `curated.json` for exactly that reason, and a token added
        without one will show its tenant code until it gets one.
        """
        return tenant

    def _to_posting(self, token: str, tenant: str, entry: dict[str, Any]) -> RawPosting | None:
        external_id = str(entry.get("Id") or "").strip()
        title = str(entry.get("Title") or "").strip()
        if not external_id or not title:
            return None

        location = str(entry.get("PrimaryLocation") or "").strip() or None
        country = str(entry.get("PrimaryLocationCountry") or "").strip()
        country_code = country.upper() if _ALPHA2_RE.match(country) else None

        code = str(entry.get("WorkplaceTypeCode") or "").strip()
        label = str(entry.get("WorkplaceType") or "").strip()
        workplace_type = _WORKPLACE.get(code) or (code or None) or (label.lower() or None)

        # `ShortDescriptionStr` is present on some boards and empty on others,
        # so it is a bonus rather than the description. The row is still
        # `jd_hydrated=False`: the read path can rank it on the title and
        # metadata, and the hydration pass is what makes it scorable.
        summary = _clean_html(str(entry.get("ShortDescriptionStr") or ""))

        return RawPosting(
            source=NAME,
            board_token=token,
            external_id=external_id,
            title=title,
            company_name=self._company_from(tenant),
            source_url=self.job_url(token, external_id),
            jd_clean=f"{title}\n\n{summary}".strip() if summary else title,
            location=location,
            country_code=country_code,
            remote=workplace_type == "remote"
            or bool(location and "remote" in location.lower()),
            workplace_type=workplace_type,
            # A date the board states, on the list, with no second request:
            # `PostedDate` matched the detail's `ExternalPostedStartDate` on
            # 4 of 4 requisitions checked. Not `updated` -- Oracle exposes no
            # modification stamp here at all -- and not `first_crawl`, which
            # would throw away a real date the employer published.
            posted_at=_posted_date(entry.get("PostedDate")),
            posted_at_basis="published",
            closes_at=_posted_date(entry.get("PostingEndDate")),
            jd_hydrated=False,
        )

    async def hydrate(self, fetcher: Any, token: str, posting: RawPosting) -> RawPosting:
        """Fill in one posting's description from the detail resource.

        Separate from `fetch_board` for the reason SmartRecruiters and Workday
        are: AutoZone's board is 11,178 postings, so hydrating it is 11,178
        extra requests. That is a decision for the caller's budget, not
        something a list crawl should take on its own.
        """
        response = await fetcher.get_json(
            self.detail_url(token, posting.external_id), host=self.host_for(token)
        )
        if not response.ok:
            return posting

        items = as_list(as_dict(response.payload).get("items"))
        if not items:
            return posting
        info = as_dict(items[0])

        # Oracle splits the posting across three authored fields, and which
        # ones are filled in is a per-employer habit: Citizens puts everything
        # in `ExternalDescriptionStr` and leaves the other two empty strings.
        #
        # `CorporateDescriptionStr` is deliberately excluded. It is the
        # boilerplate EEO and about-us block, byte-identical on every posting
        # on a board, and folding it into every `jd_clean` would pad each row
        # with the same few thousand characters the fit scorer then has to
        # read past to reach the job.
        raw = "\n\n".join(
            part
            for part in (
                str(info.get("ExternalDescriptionStr") or ""),
                str(info.get("ExternalResponsibilitiesStr") or ""),
                str(info.get("ExternalQualificationsStr") or ""),
            )
            if part.strip()
        )
        description = _clean_html(raw)
        if not description:
            return posting

        posting.jd_raw = raw
        posting.jd_clean = f"{posting.title}\n\n{description}"
        posting.jd_hydrated = True

        # A timestamp where the list had a date. Same basis either way: this
        # is a sharper `published`, not a different kind of claim. Unverified:
        # whether an unposted-and-reposted requisition moves this date. If it
        # does it is still a publish date the board states, which is what
        # `published` means, so the basis would stay right either way.
        posted = _posted_date(info.get("ExternalPostedStartDate"))
        if posted is not None:
            posting.posted_at = posted

        # The list leaves `Department` null on every row seen; the detail's
        # `Category` ("Corporate Functions", "Technology Operations") is the
        # nearest thing Oracle offers and is populated where the list is not.
        category = str(info.get("Category") or "").strip()
        if category:
            posting.department = category
        return posting
