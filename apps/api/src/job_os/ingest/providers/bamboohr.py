"""BambooHR careers sites, via the JSON endpoint their own careers page calls.

BambooHR sits where the other five providers do not. Greenhouse, Lever, Ashby and
Workday are where large companies post; BambooHR is where companies of 20 to 500
people post, and those roles are invisible to this index today.

    GET https://{slug}.bamboohr.com/careers/list
    GET https://{slug}.bamboohr.com/careers/{id}/detail

`robots.txt` on a careers host disallows exactly two paths, `/jobs/embed.php` and
`/jobs/embed2.php`, and neither is used here. `/careers/list` is permitted.
Checked on soundstripe.bamboohr.com, 2026-08-30.

Verified live on 2026-08-30 against soundstripe, canopy, atlas, pinnacle, urban,
cornerstone, trajectory, frequency, anvil and titan. What that measurement
changed:

* **A nonexistent slug answers HTTP 200.** It 302s to `www.bamboohr.com` and
  returns 43,785 bytes of marketing HTML, and `httpx` follows redirects, so the
  status code the provider sees is 200 and carries no information at all.
  `zzznotarealcompany9911`, `test`, `foo` and `xyzzy` all produce that exact
  response. A second shape exists: `acme` lands on
  `/settings/account/expired.php`, 67,122 bytes, also 200. Liveness here is
  therefore decided on whether the body parses into the documented shape, never
  on the status code. See `_board_shape`.
* **`isRemote` is always null.** Null on all 37 postings sampled across those
  ten boards, on both the list and the detail payload, including a posting whose
  own title ends in "(Remote)". The field that actually carries the answer is
  `locationType`. Reading `isRemote` would file every BambooHR role as onsite.
* **There is no ETag and no `Last-Modified`.** The response sets
  `cache-control: no-store, no-cache, must-revalidate`, so the conditional-GET
  saving the Greenhouse/Lever/Ashby providers rely on is not available. The
  `etag` argument is accepted for the Protocol and is dead weight here.
* **The list carries no description.** Bodies come from the per-posting detail
  call, so rows land `jd_hydrated=False`, exactly as SmartRecruiters and Workday
  do.
* **`location` and `atsLocation` are populated inconsistently, in both
  directions.** soundstripe and titan fill `atsLocation` (Nashville, Tennessee,
  United States) and leave `location` null; canopy, atlas, pinnacle and urban do
  the exact opposite (`location.city` = Kingston, `atsLocation` entirely null).
  Neither field is the reliable one, so `_location` reads both.

The endpoint is undocumented and powers BambooHR's own careers front end, so it
carries no stability contract. `BoardStatus` is the defence: a shape this
provider does not recognise is an `ERROR` that gets retried, never a `MISSING`
that prunes the token.

**On the seed list.** `seeds/bamboohr.txt` holds 4,992 tokens, every one of them
fetched and confirmed to be a real board with at least one posting open on
2026-08-30. None is an unverified guess. `corpus.py` records where the candidates
came from and why the discovery method, not the count, is the part worth reading.
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

NAME = "bamboohr"

#: A token is the careers subdomain and nothing else. Strict because it is
#: interpolated into a hostname: a token carrying a dot or a slash would build
#: a URL pointing somewhere other than `{slug}.bamboohr.com`, and unlike a bad
#: path that 404s, a bad host is a request to a stranger.
_TOKEN_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$", re.I)

#: BambooHR's own encoding of the workplace, and the only field that carries it.
#: All three values were seen live and two of the three were corroborated
#: against text the employer wrote rather than against a guess:
#:   "1" -> soundstripe/167 is titled "Head of Sales (Remote)".
#:   "2" -> all three anvil "(Forward Deployed)" roles say "hybrid" in their
#:          own descriptions.
#:   "0" -> the residual, and the overwhelming majority: 35 of 37 sampled
#:          postings, across boards of GPs, concrete finishers and pipe fitters.
#: A caveat that matters more than the enum does. This is a self-reported field
#: on boards run by very small employers, and they get it wrong: titan flags an
#: "HVAC Ductwork Installer" and a "Plumber" as "1". So `remote` here records
#: what the employer claimed, not a fact about the job, which is the same
#: standing every other provider's remote flag has.
_LOCATION_TYPES = {"0": "onsite", "1": "remote", "2": "hybrid"}
_REMOTE_LOCATION_TYPE = "1"

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")


class BambooHRTokenError(ValueError):
    """A token that cannot address a board. Raised at parse time, not fetch."""


def parse_token(token: str) -> str:
    """The careers subdomain, validated.

    Rejecting here rather than at the request is deliberate. `*.bamboohr.com`
    resolves for names that are not tenants, so a malformed token does not fail
    to connect; it succeeds against something else.
    """
    slug = token.strip().lower()
    if not _TOKEN_RE.match(slug):
        raise BambooHRTokenError(
            f"bamboohr token {token!r} must be a careers subdomain, e.g. 'soundstripe'"
        )
    return slug


def _clean_html(raw: str) -> str:
    """`description` is an HTML fragment. Flatten it to text.

    BambooHR's rich-text editor emits deeply nested `<span style=...>` around
    almost every run of words (measured: a 6,783-byte description whose visible
    text is under 3,000), so stripping tags matters more here than elsewhere.
    Same approach as the Workday provider, and for the same reason: the read
    path keyword-matches this and hands it to a model, neither of which needs a
    parser dependency to be correct.
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
    """`datePosted` as a datetime, or None. Date-only, so midnight UTC."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _location(entry: dict[str, Any]) -> str | None:
    """A display location out of whichever of the two location objects is filled.

    Both are always present as objects and either may be entirely null, in both
    directions, on real boards. Preferring `atsLocation` is not arbitrary: it is
    the richer of the two, carrying country as well as city and state, so when
    both are filled it is the better answer.
    """
    ats = as_dict(entry.get("atsLocation"))
    plain = as_dict(entry.get("location"))
    parts = [
        str(ats.get("city") or plain.get("city") or "").strip(),
        str(ats.get("state") or ats.get("province") or plain.get("state") or "").strip(),
        str(ats.get("country") or "").strip(),
    ]
    return ", ".join(p for p in parts if p) or None


def _board_shape(payload: Any) -> list[Any] | None:
    """The posting list, or None if this payload is not a BambooHR board.

    The single most important function in this provider. A nonexistent slug
    answers 200, so this is the only thing standing between the index and a row
    per marketing page. It insists on the documented shape -- an object with a
    `result` array -- rather than on anything the transport reported.
    """
    if not isinstance(payload, dict):
        return None
    result = payload.get("result")
    return result if isinstance(result, list) else None


class BambooHRProvider:
    name = NAME
    #: Every tenant is its own host, as with Workday, so `host_for` is what the
    #: fetcher's per-host semaphore keys on and one slug can never monopolise
    #: the sweep. This value is the fallback for accounting only.
    host = "bamboohr.com"

    def host_for(self, token: str) -> str:
        return f"{parse_token(token)}.bamboohr.com"

    def board_url(self, token: str) -> str:
        return f"https://{parse_token(token)}.bamboohr.com/careers"

    def api_url(self, token: str) -> str:
        return f"https://{parse_token(token)}.bamboohr.com/careers/list"

    def detail_url(self, token: str, job_id: str) -> str:
        return f"https://{parse_token(token)}.bamboohr.com/careers/{job_id}/detail"

    async def fetch_board(
        self,
        fetcher: Any,
        token: str,
        etag: str | None = None,
        expect_bytes: int = 0,
    ) -> BoardResult:
        """Fetch one board's whole posting list. Descriptions are left to hydration.

        One request, no pagination, and that is measured rather than assumed.
        `meta.totalCount` equalled `len(result)` on all 6,254 real boards found,
        up to and including the largest (lanesgroup, 197 postings; then upike at
        170 and theweitzcompany at 150), so there is no ceiling in the range that
        matters. The endpoint also ignores paging parameters outright:
        `?limit=200`, `?page=2`, `?offset=50` and `?per_page=200` each returned a
        response identical to the bare URL, same count and same first id. If a
        ceiling ever does appear, `totalCount` exceeding `len(result)` is the
        signal, and there is currently no parameter with which to answer it.

        `etag` is forwarded so a future BambooHR that grows one is used
        automatically, but no response measured carried one and the endpoint
        sends `cache-control: no-store`, so a 304 is not expected here.
        """
        try:
            slug = parse_token(token)
        except BambooHRTokenError as exc:
            return BoardResult(
                provider=NAME, token=token, status=BoardStatus.MISSING, error=str(exc)
            )

        response = await fetcher.get_json(
            self.api_url(token), host=self.host_for(token), etag=etag, expect_bytes=expect_bytes
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

        # THE TRAP. A slug with no tenant behind it 302s to www.bamboohr.com and
        # answers 200 with marketing HTML, so `response.ok` is not reachable
        # through the status code alone -- `get_json` sets `error` when the body
        # is not JSON, which is the actual signal. Both non-board shapes measured
        # (the marketing page, and `/settings/account/expired.php` for a lapsed
        # account) arrive here. Calling this MISSING rather than ERROR is the
        # point: it is BambooHR's definitive answer that no board exists, and an
        # ERROR would leave a dead slug being re-crawled forever.
        if response.status_code == 200 and response.error:
            return BoardResult(
                provider=NAME,
                token=token,
                status=BoardStatus.MISSING,
                http_status=200,
                bytes_fetched=response.bytes_read,
                requests_made=response.requests_made,
                error=f"200 but not a board: {response.error}",
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

        rows = _board_shape(response.payload)
        if rows is None:
            # JSON, but not the documented object. That is a vendor change we do
            # not understand, and it is NOT evidence the board is gone, so it
            # must not prune the token the way the branch above does.
            return BoardResult(
                provider=NAME,
                token=token,
                status=BoardStatus.ERROR,
                http_status=200,
                bytes_fetched=response.bytes_read,
                requests_made=response.requests_made,
                error="200 with json in an unrecognized shape",
            )

        postings: list[RawPosting] = []
        seen: set[str] = set()
        for entry in rows:
            posting = self._to_posting(token, slug, as_dict(entry))
            if posting is not None and posting.external_id not in seen:
                seen.add(posting.external_id)
                postings.append(posting)

        return BoardResult(
            provider=NAME,
            token=token,
            # `result: []` is a real tenant with nothing open, which is what
            # `hover` returns. EMPTY, not MISSING: same reasoning as
            # SmartRecruiters, and `liveness.py` resolves it over repeat visits
            # rather than this provider pruning a company between hiring rounds.
            status=BoardStatus.LIVE if postings else BoardStatus.EMPTY,
            postings=postings,
            http_status=200,
            etag=response.etag,
            bytes_fetched=response.bytes_read,
            requests_made=response.requests_made,
        )

    def _to_posting(self, token: str, slug: str, entry: dict[str, Any]) -> RawPosting | None:
        job_id = str(entry.get("id") or "").strip()
        title = str(entry.get("jobOpeningName") or "").strip()
        if not job_id or not title:
            return None

        location = _location(entry)
        department = str(entry.get("departmentLabel") or "").strip() or None
        employment = str(entry.get("employmentStatusLabel") or "").strip() or None

        # `isRemote` is deliberately not read. It was null on every posting
        # measured, including one whose title ends in "(Remote)", so trusting it
        # would file every BambooHR role as onsite.
        location_type = str(entry.get("locationType") or "").strip()

        return RawPosting(
            source=NAME,
            board_token=token,
            external_id=job_id,
            title=title,
            # The list payload never names the employer, so the slug is the best
            # identity available until `curated.json` supplies a real name.
            company_name=slug,
            source_url=f"https://{slug}.bamboohr.com/careers/{job_id}",
            # No description in the list. `jd_hydrated=False` tells the read path
            # this row can be ranked on its title but not scored on its body.
            jd_clean=" | ".join(
                p for p in (title, department, employment, location) if p
            ),
            location=location,
            remote=location_type == _REMOTE_LOCATION_TYPE,
            workplace_type=_LOCATION_TYPES.get(location_type),
            department=department,
            employment_type=employment,
            jd_hydrated=False,
            # The list has no date field of any kind, not even a vague one.
            # `datePosted` exists only on the detail payload, so hydration is
            # what earns "published".
            posted_at_basis="first_crawl",
            extra={"location_type": location_type or None},
        )

    async def hydrate(self, fetcher: Any, token: str, posting: RawPosting) -> RawPosting:
        """Fill in one posting's description and the employer's own posted date.

        Separate from `fetch_board` for the reason SmartRecruiters and Workday
        are: it is one extra request per posting, which is the caller's budget
        decision. The scale is friendlier here than elsewhere though. The median
        BambooHR board in the seed corpus holds around a dozen postings and the
        largest found holds 170, so hydrating a whole board is tens of requests
        rather than the ~4,800 a large SmartRecruiters tenant would cost.
        """
        response = await fetcher.get_json(
            self.detail_url(token, posting.external_id), host=self.host_for(token)
        )
        if not response.ok:
            # Includes the 404 a deleted posting gives:
            # {"type":"not_found","title":"Resource not found."}. The row keeps
            # its list-derived fields rather than being emptied out.
            return posting

        opening = as_dict(as_dict(as_dict(response.payload).get("result")).get("jobOpening"))
        description = _clean_html(str(opening.get("description") or ""))
        if not description:
            return posting

        posting.jd_raw = str(opening.get("description") or "")
        posting.jd_clean = description
        posting.jd_hydrated = True

        posted = _iso_date(opening.get("datePosted"))
        if posted is not None:
            posting.posted_at = posted
            # Now the employer's own date rather than the day we looked.
            posting.posted_at_basis = "published"

        share_url = str(opening.get("jobOpeningShareUrl") or "").strip()
        if share_url:
            posting.source_url = share_url

        # The detail payload repeats the location objects and fills them more
        # often than the list does, so a row that came back locationless can
        # still learn where the job is.
        if posting.location is None:
            posting.location = _location(opening)

        detail_type = str(opening.get("locationType") or "").strip()
        if detail_type:
            posting.remote = detail_type == _REMOTE_LOCATION_TYPE
            posting.workplace_type = _LOCATION_TYPES.get(detail_type)

        # `compensation` and `minimumExperience` are carried through unparsed,
        # and that is a decision rather than an oversight.
        #
        # `compensation` is set on about a quarter of postings (13 of 48 sampled
        # across 12 boards) and it is free text an employer typed, with no unit,
        # currency or period field beside it. The formats measured, all real:
        #     "$9.75/hour"                  "$70,000 - $100,000 per year"
        #     "$45,000-$50,000 per year"    "$18-$20 per hour"
        #     "$40 per hour"                "$28 - $35/DOE and shift"
        # Six shapes in one small sample, one of them with prose inside the
        # range. A regex over that would populate `salary_min`/`salary_max`
        # confidently and wrongly, and a salary shown wrong is worse than a
        # salary not shown: the read path has no way to tell that an hourly
        # figure was stored as an annual one. So the raw string is kept in
        # `extra` where a later, tested parser can find it, and the typed
        # salary fields stay None until such a parser exists.
        for field in ("compensation", "minimumExperience"):
            value = opening.get(field)
            if value:
                posting.extra[field] = value
        return posting
