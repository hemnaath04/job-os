"""iCIMS, via the sitemap every tenant publishes for search engines.

The gap this closes: iCIMS is the second-largest ATS in the corpus after
Workday and, like Workday, was only reachable through a scraper on a personal
VPS. Unlike every other provider here it has no JSON API at all, so this one
reads the two artifacts iCIMS publishes *for crawlers*:

    GET https://{sub}.icims.com/robots.txt
    GET https://{sub}.icims.com/sitemap.xml          -> flat <urlset> of job URLs
    GET https://{sub}.icims.com/jobs/{id}/{slug}/job -> schema.org JSON-LD

Everything below was measured live on 2026-08-30 across 65 candidate
subdomains, 60 of which answered with a usable sitemap. The awkward parts are
awkward because iCIMS is, not because of a guess.

**`<lastmod>` is not a publish date, and the gap is enormous.** The sitemap
gives a URL and a modification stamp, nothing else. Hydrating 93 postings
across all 31 seeded tenants and comparing each `<lastmod>` against the
`datePosted` on that same posting's detail page:

    lastmod later than datePosted     65
    same calendar day                 17
    lastmod earlier than datePosted    8
    largest gap                      728 days

So the honest basis is `"updated"`. Recording it as `"published"` would date a
two-year-old requisition to this week and present that as the employer's own
figure; only the detail page's `datePosted` earns `"published"`, which is what
`hydrate` is for. Note the third row, because `POSTED_AT_BASES` describes
`"updated"` as an upper bound and on this vendor it is not quite one: 8 of 93
postings reported a `datePosted` *after* their own `<lastmod>`. iCIMS's two
stamps do not agree with each other, so this is an approximation in both
directions rather than a bound, and it is still the only label of the four that
describes what the value actually is.

**The User-Agent decides which page you get, and looking like a browser breaks
this provider.** Requesting a job URL with Chrome's UA, curl's UA, `foo/1.0`,
or no UA at all returns a 421,738-byte marketing wrapper carrying *zero*
`application/ld+json`. Requesting the same URL with `PoliteFetcher`'s
`discovery-bot` UA returns 50,548 bytes *with* the JSON-LD. `somecrawler` also
gets the small page and `curl/8.7.1` does not, so the trigger looks like a
crawler token in the UA rather than anything about the request. The exact rule
is iCIMS's and undocumented; the operational fact is that identifying honestly
as a bot is both 8x cheaper and the only way to get the structured data.
`test_icims_user_agent_must_still_look_like_a_crawler` is the guard on that.

**Some tenants opt out, and the opt-out is a robots.txt, not an error.**
`careers-cissecureco` and `careers-acentra` both serve `User-agent: * /
Disallow: /` and then 403 the sitemap with "Your IP address is not on a trusted
network". Reading only the 403 and falling back to scraping HTML would be
crawling a board whose operator said no in the one place we are obliged to
look. So robots.txt is fetched first, every sweep, and a disallowed board costs
exactly one request and returns MISSING.

**No conditional GET.** No iCIMS sitemap observed returned an `ETag` or a
`Last-Modified` header, so the bandwidth saving the Greenhouse/Lever/Ashby
providers get from `If-None-Match` is simply not available. The `etag` argument
is still threaded through in case that changes, and a 304 is handled, but do
not expect the saving to show up in the sweep report.

**No HTML fallback, deliberately.** `GET /jobs/search?ss=1&in_iframe=1&pr=N` does
work and pages 20 job links at a time (not 50). It is not wired up, for two
measured reasons: every sitemap 403 observed came from a tenant that had already
said `Disallow: /`, so the fallback would only ever fire against a board we are
not allowed to crawl; and it costs 59,029 bytes per 20 postings against the
sitemap's 4,776 bytes per 32, roughly 80x per posting. Adding it later needs a
tenant that 403s a sitemap while robots.txt allows, and no such tenant was found.
"""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import unquote

from job_os.ingest import normalize
from job_os.ingest.fetcher import USER_AGENT
from job_os.ingest.providers.base import (
    BoardResult,
    BoardStatus,
    RawPosting,
    as_dict,
    as_list,
)

NAME = "icims"
HOST = "icims.com"

#: Largest board seen is `securitycareers-aus` at 8,804 job URLs in a single
#: 1.4 MB flat sitemap (no `<sitemapindex>` was observed on any tenant). The cap
#: is well clear of that and only bounds memory, not requests: the sitemap is
#: one request whatever its size.
MAX_POSTINGS = 25_000

#: The token is the subdomain label and nothing else. Dots and slashes are
#: rejected rather than escaped because a token like `x/../y` or `evil.com#`
#: would build a URL pointing somewhere other than the tenant it names, and
#: this provider then fetches that URL and stores what it says as job postings.
_TOKEN_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")

#: iCIMS job URLs are `/jobs/{numeric id}/{slug}/job`. The sitemap also carries
#: `/jobs/intro` on every tenant and `/jobs/search` on some, so matching on
#: "contains /jobs/" would index the board's own landing pages as vacancies.
_JOB_URL_RE = re.compile(
    r"^https?://[^/]+/jobs/(?P<id>\d+)/(?P<slug>[^/?#]*)/job(?:[?#]|$)", re.I
)

_URL_BLOCK_RE = re.compile(r"<url\b[^>]*>(.*?)</url>", re.I | re.S)
_LOC_RE = re.compile(r"<loc>\s*(.*?)\s*</loc>", re.I | re.S)
_LASTMOD_RE = re.compile(r"<lastmod>\s*(.*?)\s*</lastmod>", re.I | re.S)
_URLSET_RE = re.compile(r"<urlset\b", re.I)

#: Deliberately not an HTML parser. The only thing needed out of a 50 KB page is
#: one script block, and `json.loads` does the actual validation, so a regex
#: here costs nothing that lxml would buy back.
_LD_RE = re.compile(
    r"<script[^>]*\btype\s*=\s*[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
    re.I | re.S,
)

#: iCIMS writes this literal string into any field the tenant left blank. It
#: turns up in `hiringOrganization.name` (all 211 Liberty Mutual postings),
#: `addressRegion`, `streetAddress`, `postalCode` and `postOfficeBoxNumber`.
#: Storing it would put a company called UNAVAILABLE in the index.
_PLACEHOLDER = "UNAVAILABLE"

#: An unsubstituted iCIMS template variable, seen leaking into real payloads:
#:     "addressCountry": "Hidden (${12781}", "addressLocality": "${34271}-Remote)"
_TEMPLATE_LEAK_RE = re.compile(r"\$\{")

#: schema.org's enum, mapped to the labels the other providers already emit.
#: `OTHER` is dropped rather than stored: it is iCIMS's default for a tenant
#: that did not classify the job (9 of 29 sampled postings), so it says nothing.
_EMPLOYMENT_TYPES = {
    "FULL_TIME": "Full-time",
    "PART_TIME": "Part-time",
    "CONTRACTOR": "Contract",
    "TEMPORARY": "Temporary",
    "INTERN": "Internship",
    "VOLUNTEER": "Volunteer",
    "PER_DIEM": "Per diem",
}

_XML_ENTITIES = {
    "&amp;": "&",
    "&lt;": "<",
    "&gt;": ">",
    "&quot;": '"',
    "&apos;": "'",
}


class ICIMSTokenError(ValueError):
    """A token that cannot address a board. Raised at parse time, not fetch."""


def parse_token(token: str) -> str:
    """`careers-here` -> `careers-here`, or raise.

    Strict for a security reason rather than a tidiness one: the token is
    interpolated straight into a hostname and this provider stores whatever
    that host returns as job postings for the named company.
    """
    candidate = token.strip().lower()
    if not _TOKEN_RE.match(candidate):
        raise ICIMSTokenError(
            f"icims token {token!r} must be a bare subdomain label, "
            "e.g. 'careers-here' from careers-here.icims.com"
        )
    return candidate


# ---------------------------------------------------------------------------
# robots.txt
# ---------------------------------------------------------------------------


def _robots_pattern(rule: str) -> re.Pattern[str]:
    """One `Allow:`/`Disallow:` value as a prefix matcher.

    `*` and `$` are the two wildcards RFC 9309 defines, and real iCIMS tenants
    use them: the default board robots.txt carries `Disallow: /jobs/*referral`
    alongside a dozen siblings. Everything else is escaped, so a rule
    containing a regex metacharacter matches literally.
    """
    parts = [re.escape(chunk).replace(r"\*", ".*") for chunk in rule.split("$")]
    body = r"\$".join(parts)
    if rule.endswith("$"):
        body = body[: -len(r"\$")] + "$"
    return re.compile(body)


def robots_forbids(robots_txt: str, path: str, user_agent: str = USER_AGENT) -> bool:
    """Whether this robots.txt tells *us* to stay off `path`.

    A deliberately small RFC 9309 subset: group the rules by `User-agent`,
    prefer a group that names us over the `*` group, then let the longest
    matching rule win with `Allow` breaking ties. That is enough to read both
    shapes iCIMS actually serves, which are a bare `Disallow: /` and a list of
    `/jobs/*something` exclusions that our path does not match.

    Ambiguity resolves toward *not* crawling. A robots.txt we cannot parse into
    any group yields no rules and therefore allows, which is the RFC's answer
    for a missing file, but the caller treats an unreachable robots.txt as an
    error rather than as permission.
    """
    ua = user_agent.lower()
    groups: dict[str, list[tuple[str, bool]]] = {}
    current: list[str] = []
    # A run of consecutive `User-agent` lines shares one rule block, so the
    # agent names stay open until the first rule line closes them.
    starting_group = True

    for raw_line in robots_txt.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        field, _, value = line.partition(":")
        field = field.strip().lower()
        value = value.strip()
        if field in {"user-agent", "useragent"}:
            if not starting_group:
                current = []
                starting_group = True
            current.append(value.lower())
            groups.setdefault(value.lower(), [])
        elif field in {"allow", "disallow"} and current:
            starting_group = False
            for agent in current:
                groups[agent].append((value, field == "allow"))

    named = [agent for agent in groups if agent != "*" and agent and agent in ua]
    # Most specific wins, and a group naming us outranks the wildcard group.
    rules = groups.get(max(named, key=len)) if named else groups.get("*", [])
    if not rules:
        return False

    verdict = False
    best = -1
    for value, allow in rules:
        # An empty `Disallow:` is the documented way to say "allow everything",
        # so it must not be read as the zero-length prefix that matches all.
        if not value:
            continue
        if _robots_pattern(value).match(path) and len(value) > best:
            best = len(value)
            verdict = not allow
    return verdict


# ---------------------------------------------------------------------------
# sitemap
# ---------------------------------------------------------------------------


def _unescape_xml(text: str) -> str:
    for entity, char in _XML_ENTITIES.items():
        text = text.replace(entity, char)
    return text


def _title_from_slug(slug: str) -> str:
    """A provisional title from the URL slug, because the sitemap has no title.

    Lossy on purpose and replaced wholesale by `hydrate`, which reads the real
    `title` out of the JSON-LD. The slug is a percent-encoded, lowercased,
    hyphenated title (`director%2c-enterprise-architecture`), so unquoting and
    de-hyphenating recovers something a keyword search can hit.

    Measured against the real title on 90 hydrated postings across 31 tenants:
    exact on 44, and on 62 ignoring case. What it loses is capitalisation
    (`principal-ai-automation-engineer` -> "Principal Ai Automation Engineer",
    not "AI") and any punctuation the slug dropped ("Mechanical Designer Drafter
    Iii Solidworks" for "Mechanical Designer/Drafter III - SolidWorks"). That is
    the price of a row being searchable at all before its detail page has been
    fetched, and it is why the row also carries `jd_hydrated=False`.
    """
    text = unquote(slug).replace("+", " ")
    # A run of two or more hyphens is where the original title had punctuation
    # around a space, so it collapses to a dash rather than to a wall of spaces.
    text = re.sub(r"-{2,}", " - ", text).replace("-", " ")
    text = re.sub(r"\s+", " ", text).strip(" -")
    return " ".join(word[:1].upper() + word[1:] for word in text.split())


def parse_sitemap(token: str, xml: str) -> list[RawPosting]:
    """Every job URL in a `<urlset>`, as unhydrated postings.

    Non-job entries are dropped rather than counted: every tenant lists
    `/jobs/intro` and some list `/jobs/search`, and both would otherwise become
    a posting titled "Intro" that never closes.
    """
    postings: list[RawPosting] = []
    seen: set[str] = set()

    for block in _URL_BLOCK_RE.findall(xml):
        loc_match = _LOC_RE.search(block)
        if not loc_match:
            continue
        url = _unescape_xml(loc_match.group(1).strip())
        job = _JOB_URL_RE.match(url)
        if not job:
            continue
        external_id = job["id"]
        if external_id in seen:
            continue
        seen.add(external_id)

        lastmod = _LASTMOD_RE.search(block)
        title = _title_from_slug(job["slug"]) or f"Job {external_id}"
        postings.append(
            RawPosting(
                source=NAME,
                board_token=token,
                external_id=external_id,
                title=title,
                # The subdomain, until hydration reads the employer's own name
                # out of `hiringOrganization`. The sitemap names nobody, and
                # `careers-here` -> "HERE Technologies" is not derivable.
                company_name=token,
                # The `<loc>` verbatim, never rebuilt from the token: a handful
                # of tenants 301 their whole board to a vanity domain
                # (careers-seminolehardrock -> careers.hardrock.com), and the
                # sitemap is then already speaking in the destination's URLs.
                source_url=url,
                # A title is all the sitemap gives. `jd_hydrated=False` is what
                # tells the read path this row can be ranked on its title but
                # not scored on its body.
                jd_clean=title,
                jd_hydrated=False,
                posted_at=normalize.to_datetime(
                    lastmod.group(1).strip() if lastmod else None
                ),
                # `<lastmod>` is when the requisition was last edited, which on
                # 65 of 93 sampled postings was later than the date the board
                # itself gives, by up to 728 days. `"updated"` is what marks
                # this an estimate; `"published"` would be a claim the sitemap
                # never made.
                posted_at_basis="updated",
                extra={"slug": job["slug"]},
            )
        )
        if len(postings) >= MAX_POSTINGS:
            break

    return postings


# ---------------------------------------------------------------------------
# JSON-LD
# ---------------------------------------------------------------------------


def find_job_posting_ld(html: str) -> dict[str, Any]:
    """The schema.org `JobPosting` out of a detail page, or an empty dict.

    Every page carrying structured data carried exactly one block, and 90 of 93
    sampled job pages carried one, so hydration has to tolerate the other 3
    rather than assume the block is there. `@graph` and top-level-array forms
    are handled anyway because they are legal JSON-LD and cost three lines to
    accept.
    """
    for raw in _LD_RE.findall(html):
        try:
            data = json.loads(raw.strip())
        except ValueError:
            # A page whose script block is not JSON is a page we cannot read,
            # not a reason to abandon the other blocks on it.
            continue
        for node in _ld_nodes(data):
            if node.get("@type") == "JobPosting":
                return node
    return {}


def _ld_nodes(data: object) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [n for item in data for n in _ld_nodes(item)]
    if isinstance(data, dict):
        if "@graph" in data:
            return _ld_nodes(data["@graph"])
        return [data]
    return []


def _real(value: object) -> str | None:
    """A string field, or None if iCIMS filled it with one of its placeholders.

    Two shapes, both seen in live payloads: the literal `UNAVAILABLE`, and an
    unsubstituted template variable such as `Hidden (${12781}`.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or text == _PLACEHOLDER or _TEMPLATE_LEAK_RE.search(text):
        return None
    return text


def _first_address(posting: dict[str, Any]) -> dict[str, Any]:
    """`jobLocation` is a list on every tenant, even when it holds one Place.

    Multi-site postings list every site (Liberty Mutual runs to five), and there
    is no field saying which is primary, so the first is taken and the count is
    recorded in `extra` rather than the row pretending to be in one place.
    """
    for place in as_list(posting.get("jobLocation")):
        address = as_dict(as_dict(place).get("address"))
        if address:
            return address
    return as_dict(as_dict(posting.get("jobLocation")).get("address"))


def _location_label(address: dict[str, Any]) -> str | None:
    parts = [
        _real(address.get("addressLocality")),
        _real(address.get("addressRegion")),
    ]
    country = _real(address.get("addressCountry"))
    # Only a bare ISO-2 belongs in a human-readable label. Anything longer is
    # either a country name worth showing or the "Hidden (${12781}" leak, which
    # `_real` has already removed.
    if country and len(country) != 2:
        parts.append(country)
    label = ", ".join(part for part in parts if part)
    return label or None


def _country_code(address: dict[str, Any], label: str | None) -> str | None:
    country = _real(address.get("addressCountry"))
    if country and len(country) == 2 and country.isalpha():
        return country.upper()
    return normalize.infer_country_code(label)


def _money(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        amount = int(float(value))
    except (TypeError, ValueError):
        return None
    return amount if amount > 0 else None


def _salary(posting: dict[str, Any]) -> tuple[int | None, int | None, str | None, str | None]:
    """`baseSalary` as (min, max, currency, interval).

    iCIMS writes a flattened `MonetaryAmount` -- `{"minValue", "maxValue",
    "currency"}` directly on the amount, with no nested `value` -- which is not
    the canonical schema.org shape, so both are read. No sampled posting carried
    a `unitText`, so the interval stays None: 50,000-78,000 USD is obviously
    annual, but guessing that here would put a number in the index that no
    board ever stated. `incentiveCompensation` sits alongside as prose
    ("$57,000 - $71,000") and is left in `extra` for the same reason.
    """
    base = as_dict(posting.get("baseSalary"))
    if not base:
        return None, None, None, None
    nested = as_dict(base.get("value"))
    source = nested or base
    low = _money(source.get("minValue"))
    high = _money(source.get("maxValue"))
    if low is None and high is None:
        single = _money(source.get("value"))
        low = high = single
    currency = _real(base.get("currency")) or _real(posting.get("salaryCurrency"))
    interval = _real(source.get("unitText"))
    if low is None and high is None:
        return None, None, None, None
    return low, high, (currency.upper() if currency else None), interval


# ---------------------------------------------------------------------------
# provider
# ---------------------------------------------------------------------------


class ICIMSProvider:
    name = NAME
    #: Like Workday, there is no single API host: every tenant is its own
    #: subdomain. `PoliteFetcher` keys its per-host semaphore on the string
    #: passed to `host=`, so `host_for` gives each tenant its own concurrency
    #: ceiling. This value is the fallback for accounting only.
    host = HOST

    def host_for(self, token: str) -> str:
        return f"{parse_token(token)}.icims.com"

    def board_url(self, token: str) -> str:
        return f"https://{parse_token(token)}.icims.com/jobs/search"

    def robots_url(self, token: str) -> str:
        return f"https://{parse_token(token)}.icims.com/robots.txt"

    def sitemap_url(self, token: str) -> str:
        return f"https://{parse_token(token)}.icims.com/sitemap.xml"

    async def fetch_board(
        self,
        fetcher: Any,
        token: str,
        etag: str | None = None,
        expect_bytes: int = 0,
    ) -> BoardResult:
        """Read one tenant's sitemap, after asking its robots.txt for consent.

        Two requests per board, and the first one is the point: a tenant that
        has opted out costs one small request and never has its sitemap touched.
        """
        try:
            subdomain = parse_token(token)
        except ICIMSTokenError as exc:
            return BoardResult(
                provider=NAME, token=token, status=BoardStatus.MISSING, error=str(exc)
            )

        host = f"{subdomain}.icims.com"
        bytes_fetched = 0
        requests_made = 0

        robots = await fetcher.get_text(self.robots_url(token), host=host)
        bytes_fetched += robots.bytes_read
        requests_made += robots.requests_made

        if robots.status_code == 404:
            # No robots.txt is the RFC's "everything is allowed", and it is a
            # real iCIMS shape: careers-seminolehardrock 404s robots.txt and
            # serves a working sitemap. Note this does NOT prove the tenant is
            # gone -- the sitemap fetch below is what decides that.
            pass
        elif robots.ok:
            body = robots.payload if isinstance(robots.payload, str) else ""
            if robots_forbids(body, "/jobs/"):
                # MISSING, not EMPTY or ERROR, and the distinction is the whole
                # point: EMPTY would re-ask every three days and ERROR every
                # hour, both of which are pestering someone who said no.
                # MISSING backs off to 30 days and retires the token after
                # three sweeps, while `liveness.py` still keeps the row so the
                # board comes back if the tenant ever changes its mind.
                return BoardResult(
                    provider=NAME,
                    token=token,
                    status=BoardStatus.MISSING,
                    http_status=robots.status_code,
                    bytes_fetched=bytes_fetched,
                    requests_made=requests_made,
                    error="robots.txt disallows /jobs/ (opted out, not a dead board)",
                )
        else:
            # An unreachable robots.txt is not permission. ERROR retries later
            # rather than either crawling without consent or pruning a board
            # over one bad response from someone else's CDN.
            return BoardResult(
                provider=NAME,
                token=token,
                status=BoardStatus.ERROR,
                http_status=robots.status_code,
                bytes_fetched=bytes_fetched,
                requests_made=requests_made,
                error=robots.error or f"robots.txt unreadable: HTTP {robots.status_code}",
            )

        response = await fetcher.get_text(
            self.sitemap_url(token), host=host, etag=etag, expect_bytes=expect_bytes
        )
        bytes_fetched += response.bytes_read
        requests_made += response.requests_made

        if response.not_modified:
            return BoardResult(
                provider=NAME,
                token=token,
                status=BoardStatus.NOT_MODIFIED,
                http_status=304,
                etag=etag,
                bytes_fetched=bytes_fetched,
                requests_made=requests_made,
            )
        if response.status_code == 404:
            # Verified unambiguous: a subdomain with no tenant behind it answers
            # 404 with `<title>gone: {sub}.icims.com : dc409</title>`. There is
            # no other 404 shape on this vendor, so this one prunes.
            return BoardResult(
                provider=NAME,
                token=token,
                status=BoardStatus.MISSING,
                http_status=404,
                bytes_fetched=bytes_fetched,
                requests_made=requests_made,
            )
        if not response.ok:
            # Includes 403. Every 403 observed came from a tenant that had
            # already said `Disallow: /`, and that case returned above, so a 403
            # reaching here means an IP block or a WAF on a board that did not
            # opt out. That is transient by nature and must not prune.
            return BoardResult(
                provider=NAME,
                token=token,
                status=BoardStatus.ERROR,
                http_status=response.status_code,
                bytes_fetched=bytes_fetched,
                requests_made=requests_made,
                error=response.error or f"HTTP {response.status_code}",
            )

        xml = response.payload if isinstance(response.payload, str) else ""
        if not _URLSET_RE.search(xml):
            # A 200 that is not a sitemap. `jobs-dollargeneral` does exactly
            # this: it 301s off iCIMS to careers.dollargeneral.com and answers
            # 200 with 450 KB of HTML. Parsing that yields zero postings, and
            # calling zero postings EMPTY would let the caller deactivate a
            # board that simply moved.
            return BoardResult(
                provider=NAME,
                token=token,
                status=BoardStatus.ERROR,
                http_status=200,
                bytes_fetched=bytes_fetched,
                requests_made=requests_made,
                error="200 response is not a sitemap <urlset>",
            )

        postings = parse_sitemap(token, xml)
        return BoardResult(
            provider=NAME,
            token=token,
            # EMPTY covers both a real board between hiring rounds and a
            # sitemap that lists only marketing pages. Neither can be told from
            # the other in one response, so `liveness.py` resolves it over
            # repeated observations the way it does for SmartRecruiters.
            status=BoardStatus.LIVE if postings else BoardStatus.EMPTY,
            postings=postings,
            http_status=200,
            etag=response.etag,
            bytes_fetched=bytes_fetched,
            requests_made=requests_made,
        )

    async def hydrate(self, fetcher: Any, token: str, posting: RawPosting) -> RawPosting:
        """Fill in one posting's description, real title and real posted date.

        Separate from `fetch_board` for the reason SmartRecruiters and Workday
        are: a 3,158-posting board like `careers-primehealthcare` is 3,158 extra
        requests at ~50 KB each, which is the caller's budget to spend.

        Consent was established by `fetch_board`, which is the only thing that
        produces these rows. A stored row hydrated long after its board added a
        `Disallow: /` would slip through that check; the next sweep marks the
        board MISSING and stops producing rows, so the window is one cycle.
        """
        response = await fetcher.get_text(posting.source_url, host=self.host_for(token))
        if not response.ok:
            return posting

        html = response.payload if isinstance(response.payload, str) else ""
        info = find_job_posting_ld(html)
        if not info:
            # 3 of 93 sampled pages carried no JSON-LD at all. Returning the row
            # untouched leaves `jd_hydrated=False`, so it stays a candidate for
            # a later pass instead of being recorded as a job with no body.
            return posting

        description = normalize.html_to_text(info.get("description"))
        if not description:
            return posting

        posting.jd_raw = str(info.get("description") or "")
        posting.jd_clean = description
        posting.jd_hydrated = True

        title = _real(info.get("title"))
        if title:
            # Replaces the guess `_title_from_slug` made, acronyms and all.
            posting.title = title

        posted = normalize.to_datetime(info.get("datePosted"))
        if posted is not None:
            posting.posted_at = posted
            # Now it is the board's own publish date rather than the day it was
            # last edited. Worth knowing before trusting it too far: four
            # tenants reported `datePosted` of exactly two years before the
            # crawl date, to the second, across every posting on the board,
            # which looks like an iCIMS floor rather than 211 requisitions
            # opened on one morning. It is still what the board states, so the
            # basis is `published`; the freshness of an old one is not.
            posting.posted_at_basis = "published"

        closes = normalize.to_datetime(info.get("validThrough"))
        if closes is not None:
            posting.closes_at = closes

        organization = _real(as_dict(info.get("hiringOrganization")).get("name"))
        if organization:
            posting.company_name = organization

        address = _first_address(info)
        label = _location_label(address)
        if label:
            posting.location = label
            posting.country_code = _country_code(address, label)

        remote = str(info.get("jobLocationType") or "").upper() == "TELECOMMUTE"
        posting.remote = normalize.is_remote(label, explicit=True if remote else None)
        posting.anywhere = normalize.is_anywhere(label)
        if remote:
            posting.workplace_type = "Remote"

        employment = str(info.get("employmentType") or "").upper()
        if employment in _EMPLOYMENT_TYPES:
            posting.employment_type = _EMPLOYMENT_TYPES[employment]

        low, high, currency, interval = _salary(info)
        if low is not None or high is not None:
            posting.salary_min = low
            posting.salary_max = high
            posting.salary_currency = currency
            posting.salary_interval = interval

        posting.extra = {
            **posting.extra,
            "occupational_category": _real(info.get("occupationalCategory")),
            "incentive_compensation": _real(info.get("incentiveCompensation")),
            "job_locations": len(as_list(info.get("jobLocation"))),
        }
        return posting
