"""Turn a raw ATS payload field into something the index can store and rank.

The rules here are ported from the web app's `lib/discover/no-key-sources.ts`
rather than shared with it, because that module runs in a Next.js route and this
one runs in a worker. Where the two disagree the boards are the arbiter, and the
notes below record what the boards actually send:

  * Greenhouse `content` is entity-encoded HTML (`&lt;p&gt;`), so it needs one
    unescape pass before tag stripping. Lever and Ashby send real HTML.
  * Lever `createdAt` is epoch MILLISECONDS. Treating it as seconds dates every
    Lever posting to 1970, which then reads as "55 years old" and gets dropped
    by any age filter.
  * Ashby writes `addressCountry` as a human name ("USA", "United States",
    "Canada"), not an ISO code.
  * SmartRecruiters writes a structured location with a lowercase ISO country
    ("us") plus explicit `remote` / `hybrid` booleans.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import UTC, datetime

# Descriptions are bounded so one pathological posting cannot dominate a row.
# Postgres moves any text over ~2KB into TOAST storage and compresses it, so a
# wide `jd_clean` does not sit in the main heap and does not slow the filter and
# ranking scans, which never read the column.
MAX_DESCRIPTION_CHARS = 12_000
# What the exact-match dedupe key and the content hash see. Short enough that a
# reposted requisition with a lightly edited intro still collapses.
HASH_DESCRIPTION_CHARS = 4_000

_NAMED_ENTITIES = {
    "amp": "&",
    "lt": "<",
    "gt": ">",
    "quot": '"',
    "apos": "'",
    "nbsp": " ",
    "ndash": "-",
    "mdash": "-",
    "hellip": "...",
    "rsquo": "'",
    "lsquo": "'",
    "rdquo": '"',
    "ldquo": '"',
    "bull": "*",
    "middot": "*",
    "trade": "(tm)",
    "reg": "(r)",
    "copy": "(c)",
}

_ENTITY_RE = re.compile(r"&(#x[0-9a-fA-F]+|#\d+|[a-zA-Z]+);")
_SCRIPT_RE = re.compile(r"<(script|style)[\s\S]*?</\1>", re.I)
_BR_RE = re.compile(r"<br\s*/?>", re.I)
_BLOCK_END_RE = re.compile(r"</(p|div|li|ul|ol|h[1-6]|tr|table|section)>", re.I)
_LI_RE = re.compile(r"<li[^>]*>", re.I)
_TAG_RE = re.compile(r"<[^>]+>")


def decode_entities(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        body = match.group(1)
        if body[:2].lower() == "#x":
            try:
                return chr(int(body[2:], 16))
            except ValueError:
                return match.group(0)
        if body.startswith("#"):
            try:
                return chr(int(body[1:], 10))
            except ValueError:
                return match.group(0)
        return _NAMED_ENTITIES.get(body.lower(), match.group(0))

    return _ENTITY_RE.sub(replace, text)


def collapse_whitespace(text: str) -> str:
    text = text.replace("\r", "").replace(" ", " ")
    text = re.sub(r"[ \t  ]+", " ", text)
    text = re.sub(r" ?\n ?", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def truncate(text: str, limit: int) -> str:
    """Cut at a word boundary when one is close to the limit, else hard cut."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    last_space = cut.rfind(" ")
    return (cut[:last_space] if last_space > limit * 0.8 else cut).rstrip()


def html_to_text(html: str | None, limit: int = MAX_DESCRIPTION_CHARS) -> str:
    """Flatten a posting body to plain text.

    The `&lt;` check is what handles Greenhouse: its payload is HTML that has
    been entity-encoded once, so it contains no real `<` at all. Unescaping it
    first turns it into ordinary HTML that the tag stripping below can read.
    Doing that unconditionally would corrupt a real-HTML body whose prose
    happens to contain a literal `&lt;`.
    """
    if not html:
        return ""
    text = html
    if "&lt;" in text and "<" not in text:
        text = decode_entities(text)
    text = _SCRIPT_RE.sub(" ", text)
    text = _BR_RE.sub("\n", text)
    text = _BLOCK_END_RE.sub("\n", text)
    text = _LI_RE.sub("- ", text)
    text = _TAG_RE.sub(" ", text)
    return truncate(collapse_whitespace(decode_entities(text)), limit)


def plain_text(text: str | None, limit: int = MAX_DESCRIPTION_CHARS) -> str:
    """For providers that already ship a plain-text body."""
    if not text:
        return ""
    return truncate(collapse_whitespace(text), limit)


# ---------------------------------------------------------------------------
# dates
# ---------------------------------------------------------------------------

_EPOCH_SECONDS_RE = re.compile(r"^\d{10}$")
_EPOCH_MILLIS_RE = re.compile(r"^\d{13}$")
_NAIVE_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ][\d:.]+$")
# A plausible window for a job posting. Anything outside it is a parse artifact
# (a millisecond stamp read as seconds lands in 1970) rather than a real date,
# and storing it would make the row look ancient to every freshness filter.
_MIN_PLAUSIBLE_YEAR = 1995
_MAX_PLAUSIBLE_YEAR = 2100


def to_datetime(value: object) -> datetime | None:
    """Coerce an ATS timestamp to an aware UTC datetime, or None.

    Handles ISO 8601 with or without a zone, epoch seconds, and epoch
    milliseconds. The 1e11 threshold is the seconds/millis discriminator: any
    epoch-seconds value past 1e11 would be the year 5138, so a number that big
    is milliseconds. Lever is the provider this matters for.
    """
    if value is None:
        return None

    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, bool):
        return None
    elif isinstance(value, int | float):
        seconds = float(value) / 1000.0 if abs(value) >= 1e11 else float(value)
        try:
            parsed = datetime.fromtimestamp(seconds, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    elif isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        if _EPOCH_MILLIS_RE.match(raw):
            return to_datetime(int(raw))
        if _EPOCH_SECONDS_RE.match(raw):
            return to_datetime(int(raw) * 1000)
        # A stamp with no zone: the boards that do this publish UTC, so pin it
        # rather than letting the worker's local zone shift the calendar day.
        if _NAIVE_ISO_RE.match(raw):
            raw = raw.replace(" ", "T") + "Z"
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        # A list, a dict, or whatever else a board decides to put in a date field.
        # Falling through would raise UnboundLocalError and take the whole crawl
        # down over one malformed posting.
        return None

    parsed = parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    if not (_MIN_PLAUSIBLE_YEAR <= parsed.year <= _MAX_PLAUSIBLE_YEAR):
        return None
    return parsed


# ---------------------------------------------------------------------------
# location
# ---------------------------------------------------------------------------

def _word_set(words: str) -> frozenset[str]:
    """A frozenset from a whitespace-delimited block.

    The lists below are long and read far better as prose blocks than as one
    quoted string per element, and they are only ever membership-tested.
    """
    return frozenset(words.split())


_US_STATE_CODES = _word_set(
    "AL AK AZ AR CA CO CT DC DE FL GA HI IA ID IL IN KS KY LA MA MD ME MI MN MO "
    "MS MT NC ND NE NH NJ NM NV NY OH OK OR PA RI SC SD TN TX UT VA VT WA WI WV "
    "WY PR"
)
_CA_PROVINCE_CODES = _word_set("AB BC MB NB NL NS NT NU ON PE QC SK YT")
_SUBDIVISION_RE = re.compile(r",\s*([A-Z]{2})\b")

_COUNTRY_BY_NAME = {
    "united states of america": "US", "united states": "US", "usa": "US",
    "u.s.": "US", "u.s.a.": "US", "america": "US", "us": "US",
    "canada": "CA", "united kingdom": "GB", "uk": "GB", "england": "GB",
    "scotland": "GB", "wales": "GB", "great britain": "GB",
    "northern ireland": "GB", "ireland": "IE", "germany": "DE",
    "deutschland": "DE", "france": "FR", "spain": "ES", "portugal": "PT",
    "netherlands": "NL", "the netherlands": "NL", "belgium": "BE",
    "switzerland": "CH", "sweden": "SE", "norway": "NO", "denmark": "DK",
    "finland": "FI", "poland": "PL", "italy": "IT", "austria": "AT",
    "czechia": "CZ", "czech republic": "CZ", "romania": "RO", "greece": "GR",
    "hungary": "HU", "bulgaria": "BG", "serbia": "RS", "croatia": "HR",
    "india": "IN", "singapore": "SG", "japan": "JP", "australia": "AU",
    "new zealand": "NZ", "brazil": "BR", "mexico": "MX", "argentina": "AR",
    "chile": "CL", "colombia": "CO", "peru": "PE", "israel": "IL",
    "united arab emirates": "AE", "uae": "AE", "south africa": "ZA",
    "china": "CN", "hong kong": "HK", "taiwan": "TW", "south korea": "KR",
    "korea": "KR", "philippines": "PH", "indonesia": "ID", "vietnam": "VN",
    "thailand": "TH", "malaysia": "MY", "nigeria": "NG", "kenya": "KE",
    "egypt": "EG", "turkey": "TR", "ukraine": "UA", "lithuania": "LT",
    "latvia": "LV", "estonia": "EE", "slovakia": "SK", "slovenia": "SI",
}

# Boards routinely give a bare city with no country, so a hint table lifts
# country coverage a long way.
_COUNTRY_BY_CITY = {
    "san francisco": "US", "bay area": "US", "new york": "US", "nyc": "US",
    "brooklyn": "US", "seattle": "US", "austin": "US", "boston": "US",
    "cambridge": "US", "chicago": "US", "los angeles": "US", "denver": "US",
    "atlanta": "US", "washington dc": "US", "miami": "US", "dallas": "US",
    "houston": "US", "phoenix": "US", "philadelphia": "US", "detroit": "US",
    "minneapolis": "US", "salt lake city": "US", "san diego": "US",
    "san jose": "US", "palo alto": "US", "mountain view": "US",
    "sunnyvale": "US", "portland": "US", "pittsburgh": "US", "raleigh": "US",
    "nashville": "US", "columbus": "US",
    "london": "GB", "manchester": "GB", "edinburgh": "GB", "cambridge uk": "GB",
    "bristol": "GB", "leeds": "GB", "glasgow": "GB",
    "toronto": "CA", "vancouver": "CA", "montreal": "CA", "ottawa": "CA",
    "calgary": "CA", "waterloo": "CA",
    "bengaluru": "IN", "bangalore": "IN", "mumbai": "IN", "hyderabad": "IN",
    "pune": "IN", "chennai": "IN", "gurgaon": "IN", "gurugram": "IN",
    "noida": "IN", "new delhi": "IN", "delhi": "IN", "kolkata": "IN",
    "ahmedabad": "IN", "jaipur": "IN",
    "berlin": "DE", "munich": "DE", "münchen": "DE", "hamburg": "DE",
    "frankfurt": "DE", "cologne": "DE", "stuttgart": "DE",
    "paris": "FR", "lyon": "FR", "toulouse": "FR",
    "amsterdam": "NL", "rotterdam": "NL", "utrecht": "NL", "eindhoven": "NL",
    "dublin": "IE", "cork": "IE",
    "madrid": "ES", "barcelona": "ES", "valencia": "ES",
    "lisbon": "PT", "porto": "PT",
    "zurich": "CH", "zürich": "CH", "geneva": "CH", "lausanne": "CH",
    "stockholm": "SE", "gothenburg": "SE", "copenhagen": "DK", "oslo": "NO",
    "helsinki": "FI", "warsaw": "PL", "krakow": "PL", "kraków": "PL",
    "wroclaw": "PL", "bucharest": "RO", "milan": "IT", "rome": "IT",
    "turin": "IT", "vienna": "AT", "prague": "CZ", "budapest": "HU",
    "sydney": "AU", "melbourne": "AU", "brisbane": "AU", "perth": "AU",
    "auckland": "NZ", "wellington": "NZ",
    "tokyo": "JP", "osaka": "JP", "kyoto": "JP",
    "tel aviv": "IL", "jerusalem": "IL", "haifa": "IL",
    "sao paulo": "BR", "são paulo": "BR", "rio de janeiro": "BR",
    "mexico city": "MX", "guadalajara": "MX", "bogota": "CO", "bogotá": "CO",
    "buenos aires": "AR", "santiago": "CL", "lima": "PE",
    "singapore": "SG", "seoul": "KR", "taipei": "TW", "shanghai": "CN",
    "beijing": "CN", "shenzhen": "CN", "bangkok": "TH", "manila": "PH",
    "jakarta": "ID", "kuala lumpur": "MY", "ho chi minh": "VN", "hanoi": "VN",
    "dubai": "AE", "abu dhabi": "AE", "cape town": "ZA", "johannesburg": "ZA",
    "lagos": "NG", "nairobi": "KE", "cairo": "EG", "istanbul": "TR",
    "kyiv": "UA", "kiev": "UA", "vilnius": "LT", "riga": "LV", "tallinn": "EE",
}

_REMOTE_RE = re.compile(r"\b(remote|anywhere|worldwide|distributed|work from home|wfh)\b", re.I)
_ANYWHERE_RE = re.compile(r"\b(anywhere|worldwide|global(?:ly)?)\b", re.I)


def _word_match(haystack: str, needle: str) -> bool:
    return re.search(rf"(^|[^a-z]){re.escape(needle)}([^a-z]|$)", haystack, re.I) is not None


def infer_country_code(location: str | None) -> str | None:
    """Best-effort ISO-3166 alpha-2 from a free-text location label.

    Returns None rather than guessing. An unknown country is a different fact
    from a wrong one, and the read path treats the two differently.
    """
    if not location:
        return None
    text = location.strip()
    if not text:
        return None

    # An explicit two-letter code is already the answer.
    if len(text) == 2 and text.upper() in _US_STATE_CODES | _CA_PROVINCE_CODES:
        return "US" if text.upper() in _US_STATE_CODES else "CA"

    for code in _SUBDIVISION_RE.findall(text):
        if code in _US_STATE_CODES:
            return "US"
        if code in _CA_PROVINCE_CODES:
            return "CA"

    lowered = text.lower()
    for name, code in _COUNTRY_BY_NAME.items():
        if _word_match(lowered, name):
            return code
    for city, code in _COUNTRY_BY_CITY.items():
        if _word_match(lowered, city):
            return code
    return None


def is_remote(location: str | None, *, explicit: bool | None = None) -> bool:
    """Whether a posting is remote-friendly.

    `explicit` is for providers that say so in a field (SmartRecruiters
    `location.remote`, Ashby `workplaceType`). It wins when present, because a
    board that states the fact beats a regex over a label.
    """
    if explicit is not None:
        return explicit
    return bool(location and _REMOTE_RE.search(location))


def is_anywhere(location: str | None) -> bool:
    """Hire-from-anywhere, as opposed to remote-within-one-country."""
    return bool(location and _ANYWHERE_RE.search(location))


# ---------------------------------------------------------------------------
# identity: hashes and dedupe keys
# ---------------------------------------------------------------------------

_PUNCT_RE = re.compile(r"[^a-z0-9+#./ ]+")
_WS_RE = re.compile(r"\s+")
# Seniority and req-number noise that makes two spellings of one job look like
# two jobs. Stripped only from the dedupe key, never from the stored title.
_TITLE_NOISE_RE = re.compile(
    r"\b(?:req(?:uisition)?\s*#?\d+|job\s*id\s*\d+|\(\s*remote\s*\)|"
    r"\d{4,}|[ivx]{1,4}\b(?=\s*$))",
    re.I,
)


def fold(text: str | None) -> str:
    """Lowercase, strip accents and punctuation, squeeze whitespace.

    Used for every comparison key so "Sao Paulo" and "São Paulo", or
    "Senior Engineer" and "senior  engineer", stop being different strings.
    """
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    lowered = _PUNCT_RE.sub(" ", stripped.lower())
    return _WS_RE.sub(" ", lowered).strip()


def normalize_title(title: str | None) -> str:
    return _WS_RE.sub(" ", _TITLE_NOISE_RE.sub(" ", fold(title))).strip()


def normalize_location(location: str | None) -> str:
    return fold(location)


def normalize_company(name: str | None, domain: str | None = None) -> str:
    """Prefer the domain: two boards spell one employer's name three ways."""
    if domain:
        host = domain.strip().lower().removeprefix("http://").removeprefix("https://")
        host = host.split("/")[0].removeprefix("www.")
        if host:
            return host
    return fold(name)


def dedupe_key(
    company: str | None, title: str | None, location: str | None, *, domain: str | None = None
) -> str:
    """Stage-one exact-match key: one requisition per company/title/location.

    This is the cheap half of the two-stage dedupe. Companies file the same
    opening once per office as separate requisitions, and the same role shows up
    on both a company board and an aggregator, so an exact key over the three
    fields that identify a job catches most of it for the cost of a hash.
    """
    return "|".join(
        (
            normalize_company(company, domain),
            normalize_title(title),
            normalize_location(location),
        )
    )


def content_hash(
    company: str | None,
    title: str | None,
    location: str | None,
    description: str | None,
    *,
    domain: str | None = None,
) -> str:
    """sha256 collapse key over the identity fields plus the description head.

    Distinct from `dedupe_key`: this one changes when the body is edited, so it
    is how the upsert decides whether a re-crawled posting is unchanged (skip
    the write, bump last_seen_at) or genuinely revised (rewrite the row). Only
    the first HASH_DESCRIPTION_CHARS take part, so a boilerplate footer edit
    does not read as a new posting.
    """
    payload = "␟".join(
        (
            normalize_company(company, domain),
            normalize_title(title),
            normalize_location(location),
            fold((description or "")[:HASH_DESCRIPTION_CHARS]),
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


_STOPWORDS = _word_set(
    """a an and are as at be by for from has have in into is it its of on or that
    the to was were will with you your we our us this those these they their he
    she his her but not no if then than so such can could would should may might
    must do does did done being been about above after again all also am any
    because before below between both during each few further here how i more
    most other others out over own same some there through too under until up
    very what when where which while who whom why role team work working join
    company job position opportunity candidate candidates apply application
    experience years year including etc"""
)


def tokens_for_similarity(text: str | None) -> list[str]:
    """Content words for the TF-IDF stage of dedupe.

    Stopwords and one-character tokens are dropped because job descriptions
    share so much boilerplate that leaving them in pushes every pair's cosine
    similarity up toward the threshold and the filter stops discriminating.
    """
    if not text:
        return []
    return [w for w in fold(text).split() if len(w) > 1 and w not in _STOPWORDS]
