"""Answer a job search from the index instead of fanning out to 85 boards.

This is the read half of the ingest work. The live path in the web app's
`lib/discover/no-key-sources.ts` fetches every curated board on every search,
which makes search latency the sum of someone else's API latency and caps coverage
at the boards in that file. This module answers the same question with one indexed
query and does not touch the network.

**Storage.** `job_postings` moved off Neon Postgres to Appwrite (see
`search_index`'s own docstring for the honest list of what retrieval lost in
that move -- graded relevance chief among them). Everything below stayed put.

**Ranking.** `rank = retrieve_score * freshness_weight * mix_weight`, multiplicative
so freshness cannot be swamped by a marginally better keyword match. With an
additive score, a perfect title hit from eight months ago outranks a good hit from
this morning, which is the wrong answer for a job search: the old one is probably
filled. Multiplying means a stale posting has to be substantially more relevant to
beat a fresh one, rather than slightly.

  * `retrieve_score`  whether the searched words are in the TITLE or only in the
                      JD body (`_title_weight`). Appwrite's fulltext match is
                      pass/fail over `search_text`, which concatenates the title
                      with 8000 characters of `jd_clean`, so retrieval alone
                      cannot tell a Software Engineering Intern posting from a
                      Director of Litigation posting whose JD happens to mention
                      the internship programme -- and a live search for
                      "software engineer intern" duly opened with the Director.
                      This restores, in Python and coarsely, the one distinction
                      the weighted tsvector used to make for free.
  * `freshness_weight` exponential decay on the effective date with a 14-day half
                      life, floored so an old-but-perfect match is demoted rather
                      than deleted.
  * `mix_weight`      diversity: the nth posting from one company is progressively
                      discounted, so one employer mid-hiring-spree cannot own the
                      page. Positional, applied in Python after the candidate pool
                      comes back from Appwrite.

**Two-phase, and why.** Appwrite's filters/fulltext search pick the candidate
pool and a coarse sort order; Python then computes the real `freshness_weight`
and the positional `mix_weight` and re-sorts by the full multiplicative rank.
Diversity has to happen after retrieval regardless of storage engine: it is a
function of a row's position among its neighbors, not of the row alone.

Snippets are a second pass, over just the ~60 rows that survived ranking, so
that Appwrite's own row size cap and the cost of moving `jd_clean` around only
has to be paid for a page, not the whole candidate pool -- the same shape the
Postgres version used, back when it was TOAST doing the cost, not Appwrite.
That second pass is a second REQUEST now, not just a second read of a payload
already in hand: the pool query selects metadata only (`POOL_COLUMNS`). A pool
of 480 rows each carrying up to 8000 characters of `jd_clean` is around four
megabytes for MariaDB to sort, serialize and ship on every search, and that is
the shape of the failure the UI was reporting as "the saved index was
restarting" -- Appwrite answering its own slow query with a 408.

The result count is capped at `TOTAL_COUNT_CAP` for the same reason as before:
a searcher does not act on the difference between 1,000 and 7,019 matches, and
`total_matched_capped` says when the number is a floor rather than an exact
total.

**Freshness is reported, not asserted.** Every result carries `first_seen_at`,
`last_seen_at`, and `posted_at_estimated`, so the UI can say "first seen 3 weeks
ago, still listed 1 hour ago" rather than presenting a re-dated repost as new.
`posted_at_basis` says where the date came from. See `ingest/providers/base.py`.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from job_os.schemas.enrichment import JobEnrichment
from job_os.services import appwrite_tables, job_enrich, job_match
from job_os.services.job_match import CandidateProfile, MatchScore

log = structlog.get_logger(__name__)

#: The Appwrite `job_postings` column holding a job's enrichment document, as
#: the raw JSON `job_enrich.enrich_job` produced -- not the `Job.jd_parsed`
#: dict wrapper `store_enrichment`/`load_enrichment` were written against,
#: since this table has no such column. Wrapping/unwrapping in that shape
#: here reuses their validation and schema-version handling instead of a
#: second copy of it.
ENRICHMENT_COLUMN = "enrichment"

#: Enrichment runs inline, per row, the first time a posting reaches a page a
#: user actually sees -- bounded by the page size a search ever returns, not
#: by the corpus. Was 50, run sequentially; a real request with real profile
#: signal against a page of never-before-seen postings blew straight through
#: Heroku's 30s router timeout (H12) awaiting enrich_job one at a time. Now
#: run concurrently (see _attach_match_scores), which changes the cost from
#: "sum of every call" to "the slowest one" -- lowered anyway, since even
#: concurrent calls still each cost real gateway capacity and dyno memory.
MAX_ENRICH_PER_SEARCH = 12

#: Per-call ceiling when enriching concurrently, so one slow or hung gateway
#: call cannot spend the whole request's remaining time budget under
#: Heroku's 30s router limit. A skipped enrichment here just means that hit
#: falls back to the client lexicon this time -- not an error, and it will
#: very likely be cached and scored by the next search that reaches it.
ENRICH_DEADLINE_SECONDS = 18.0

#: Half life of the freshness decay. A fortnight is roughly the useful life of a
#: posting: applying on day 30 is materially worse than on day 2.
FRESHNESS_HALF_LIFE_DAYS = 14.0
#: Freshness never reaches zero. An old posting that is still listed is still a
#: real job, so it is ranked down rather than filtered out; deciding it is gone is
#: the crawl's job, via `active`, not the ranker's.
MIN_FRESHNESS_WEIGHT = 0.05
#: Inert since the move to Appwrite: this squashed `ts_rank_cd`'s unbounded
#: score into (0,1] via x/(x+k). Appwrite's fulltext match has no score to
#: squash -- `retrieve_score` is a flat 1.0 now (see this module's own
#: docstring) -- so this constant no longer affects any ranking and is not
#: reported by `ranking_constants()`. Left defined, unused, rather than
#: deleted: the Postgres-backed `ts_rank_cd` path this tuned is still real
#: code history, not a hypothetical one.
RANK_SATURATION_K = 1.0
#: `retrieve_score` for a row whose title carries the searched words, and for
#: one where they appear only in the JD body. The gap is deliberately wide:
#: `freshness_weight` spans a factor of 20 on its own, so a narrower one would
#: let a body-only mention from this morning outrank a real title match from
#: last week -- which is the ordering being fixed, not a variation of it. A
#: body-only hit is demoted rather than dropped because it is still a genuine
#: match: a posting that describes the internship in its body and titles itself
#: something else is a real thing, just not the first thing to show.
TITLE_MATCH_WEIGHT = 1.0
BODY_ONLY_MATCH_WEIGHT = 0.05
#: What every row scores when the search named no keywords at all. Browsing has
#: no title to match against, so this keeps `rank` exactly what it was: purely
#: freshness times diversity.
NO_KEYWORDS_WEIGHT = 1.0
#: Discount applied to the nth posting from the same company on one page.
COMPANY_DIVERSITY_DECAY = 0.65
#: The floor of that discount, so a company with genuinely more relevant postings
#: still keeps several slots.
MIN_MIX_WEIGHT = 0.15
#: Candidate pool multiplier. Diversity reorders, so the pool must be wider than
#: the page or reordering has nothing to promote.
CANDIDATE_MULTIPLIER = 8
MAX_CANDIDATES = 2_000
DEFAULT_LIMIT = 60
MAX_LIMIT = 200
SNIPPET_CHARS = 400
#: Counting is stopped here. A searcher does not act on the difference between
#: 1,000 and 7,019 matches, and the exact count costs an order of magnitude more
#: than the bounded one. `total_matched_capped` says when the number is a floor.
TOTAL_COUNT_CAP = 1_000


@dataclass(slots=True)
class IndexQuery:
    title_keywords: list[str] = field(default_factory=list)
    #: Free text matched against the whole vector rather than the title only.
    query: str | None = None
    location: str | None = None
    country_codes: list[str] = field(default_factory=list)
    company: str | None = None
    sources: list[str] = field(default_factory=list)
    remote: bool | None = None
    max_age_days: int | None = None
    posted_within_days: int | None = None
    #: Include postings the board has stopped listing. Off by default; on, it is
    #: how the UI can honestly show that a role closed rather than hiding it.
    include_inactive: bool = False
    #: Include rows merged into another by dedupe. Off by default.
    include_duplicates: bool = False
    #: Drop rows whose description was never hydrated (SmartRecruiters listings).
    require_description: bool = False
    salary_min: int | None = None
    limit: int = DEFAULT_LIMIT
    offset: int = 0
    explain: bool = False


@dataclass(slots=True)
class ScoreExplain:
    """Why this row is where it is. EXPLAIN-style, returned when asked for."""

    retrieve_score: float
    freshness_weight: float
    mix_weight: float
    rank: float
    text_rank_raw: float
    age_days: float
    effective_date: datetime
    company_rank: int
    matched_keywords: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "rank": round(self.rank, 6),
            "retrieve_score": round(self.retrieve_score, 6),
            "freshness_weight": round(self.freshness_weight, 6),
            "mix_weight": round(self.mix_weight, 6),
            "text_rank_raw": round(self.text_rank_raw, 6),
            "age_days": round(self.age_days, 2),
            "effective_date": self.effective_date.isoformat(),
            "company_rank": self.company_rank,
            "matched_keywords": self.matched_keywords,
            "formula": "rank = retrieve_score * freshness_weight * mix_weight",
        }


@dataclass(slots=True)
class IndexHit:
    id: uuid.UUID
    source: str
    source_id: str
    source_url: str
    title: str
    company_name: str
    company_domain: str | None
    location: str | None
    country_code: str | None
    remote: bool
    department: str | None
    employment_type: str | None
    salary_min: int | None
    salary_max: int | None
    salary_currency: str | None
    snippet: str
    description_available: bool
    # --- honest freshness ---
    posted_at: datetime | None
    posted_at_basis: str
    posted_at_estimated: bool
    first_seen_at: datetime
    last_seen_at: datetime
    active: bool
    inactive_since: datetime | None
    repost_count: int
    rank: float
    explain: ScoreExplain | None = None
    #: None when no `candidate` was passed to `search_index` (the caller has
    #: no signed-in profile to score against) -- the frontend's own lexicon
    #: fallback (`fit-score.ts`) is what renders then. Present and
    #: authoritative otherwise; see `_attach_match_scores`.
    match: MatchScore | None = None


@dataclass(slots=True)
class IndexSearchResult:
    hits: list[IndexHit]
    total_matched: int
    candidates_considered: int
    took_ms: float
    #: True when counting stopped at TOTAL_COUNT_CAP, so `total_matched` is a
    #: floor. The UI should render "1000+" rather than implying an exact total.
    total_matched_capped: bool = False
    #: The tsquery that ran, so a surprising result set can be explained.
    keyword_query: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "total_matched": self.total_matched,
            "total_matched_capped": self.total_matched_capped,
            "candidates_considered": self.candidates_considered,
            "took_ms": round(self.took_ms, 2),
            "returned": len(self.hits),
        }


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _quote_phrase(text: str) -> str:
    """A search term as a MariaDB fulltext phrase, not a bag of words.

    Unquoted, multiple words run in natural-language mode, satisfied by *any*
    of them appearing anywhere in `search_text` -- see `search_index`'s own
    comment on why that made a search for "software engineer intern" surface
    an Account Executive posting. A literal double quote in the term itself
    would otherwise end the phrase early (or, with a stray unmatched quote,
    make the whole query malformed), so any quotes the caller typed are
    stripped rather than escaped -- there is no legitimate reason a job title
    or free-text query needs one.
    """
    return f'"{text.strip().replace(chr(34), "")}"'


#: Columns the candidate pool query reads. Everything `_row_to_tuple` touches
#: and nothing else -- specifically not `jd_clean` or `enrichment`, the two
#: fat ones, which `_hydrate_page` fetches for the page that survived ranking.
#: `test_job_index_title_weight` asserts this list still covers `_row_to_tuple`,
#: because a field added there and forgotten here would not fail loudly; it
#: would quietly read as None and put every posting's date at the crawl time.
POOL_COLUMNS = [
    "source_posting_id",
    "source",
    "source_id",
    "source_url",
    "title",
    "company_name",
    "company_domain",
    "location",
    "country_code",
    "remote",
    "department",
    "employment_type",
    "salary_min",
    "salary_max",
    "salary_currency",
    "jd_hydrated",
    "posted_at",
    "posted_at_basis",
    "posted_at_estimated",
    "first_seen_at",
    "last_seen_at",
    "active",
    "inactive_since",
    "repost_count",
]

#: Columns only the page needs: the JD body for the snippet and the fit score,
#: and the cached enrichment so an already-scored posting costs no LLM call.
PAGE_COLUMNS = ["source_posting_id", "jd_clean", "enrichment", "title", "company_name"]

#: Words too common in a job title to carry intent on their own.
_TITLE_STOPWORDS = frozenset(
    {"a", "an", "and", "at", "for", "from", "in", "of", "on", "or", "the", "to", "with"}
)


#: Suffixes stripped before two title words are compared, longest first, never
#: below a four-character root. Deliberately crude, and deliberately looser than
#: the exact-word rule the live sources FILTER with (`matchesTitle` in
#: `no-key-sources.ts`, which the smart-search prompt is written against):
#: loosening that rule would widen what every search fetches, while this only
#: decides the order of what came back, and "engineer" scoring "Software
#: Engineering Intern" no higher than "Director of Litigation" is the failure
#: being fixed. The same three cases in the same order live in the web app's
#: `relevance.ts`; that is duplicated knowledge in two languages, and it is
#: called out here rather than left to be discovered.
_STEM_SUFFIXES = ("ships", "ship", "ings", "ing", "es", "s")


def _stem(word: str) -> str:
    for suffix in _STEM_SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


def _title_words(text: str | None) -> set[str]:
    """A title as a bag of comparable words. Punctuation, case and the handful
    of suffixes above do not count, so "AI/ML Engineer Intern" and "ai ml
    engineering, internship" are the same title."""
    if not text:
        return set()
    return {_stem(w) for w in re.split(r"[^a-z0-9+#]+", text.lower()) if w}


def _title_weight(title: str | None, phrases: list[str]) -> float:
    """Did the searched words land in the title, or only somewhere in the JD?

    `phrases` are alternatives: every word of a phrase must appear in the title,
    in any order, and matching any one phrase is enough.

    Returns `NO_KEYWORDS_WEIGHT` when there was nothing to match, which is what
    keeps browsing with no filters ranked purely on freshness and diversity.
    """
    usable = [p for p in phrases if p and p.strip()]
    if not usable:
        return NO_KEYWORDS_WEIGHT
    words = _title_words(title)
    for phrase in usable:
        needed = {w for w in _title_words(phrase) if w not in _TITLE_STOPWORDS}
        if needed and needed <= words:
            return TITLE_MATCH_WEIGHT
    return BODY_ONLY_MATCH_WEIGHT


def _row_to_tuple(
    row: dict[str, Any], now: datetime, title_phrases: list[str] | None = None
) -> tuple[Any, ...]:
    """One Appwrite `job_postings` row, reshaped into the tuple
    `_apply_mix_and_rank` has always consumed -- the same fields, in the same
    order, that the SQL `select(...)` in the Postgres version of this
    function used to produce. Keeping that contract is what let the
    Python-side ranking (`_freshness_weight`/`_mix_weight`/
    `_apply_mix_and_rank`) stay completely unchanged by this migration.

    `text_rank` is always `0.0`: Appwrite's fulltext match has nothing gradable
    to report, unlike `ts_rank_cd`. `retrieve` was flat `1.0` for the same
    reason until `_title_weight` gave it the one distinction that mattered --
    title hit or body-only hit.
    """
    posted_at = _parse_dt(row.get("posted_at"))
    first_seen_at = _parse_dt(row.get("first_seen_at")) or now
    effective_date = posted_at or first_seen_at
    age_days = (now - effective_date).total_seconds() / 86400.0
    freshness = _freshness_weight(age_days)
    return (
        uuid.UUID(row["source_posting_id"]),
        row.get("source"),
        row.get("source_id"),
        row.get("source_url"),
        row.get("title"),
        row.get("company_name"),
        row.get("company_domain"),
        row.get("location"),
        row.get("country_code"),
        bool(row.get("remote")),
        row.get("department"),
        row.get("employment_type"),
        row.get("salary_min"),
        row.get("salary_max"),
        row.get("salary_currency"),
        bool(row.get("jd_hydrated")),
        posted_at,
        row.get("posted_at_basis"),
        bool(row.get("posted_at_estimated")),
        first_seen_at,
        _parse_dt(row.get("last_seen_at")) or first_seen_at,
        bool(row.get("active", True)),
        _parse_dt(row.get("inactive_since")),
        int(row.get("repost_count") or 0),
        0.0,
        _title_weight(row.get("title"), title_phrases or []),
        freshness,
        age_days,
        effective_date,
    )


async def search_index(
    session: AsyncSession,
    query: IndexQuery,
    *,
    candidate: CandidateProfile | None = None,
) -> IndexSearchResult:
    """Retrieval against Appwrite's `job_postings` table (moved off Neon
    Postgres to the same Appwrite project the resume workspace already used,
    on the GitHub Student Pack's Education plan -- Pro-equivalent limits, no
    per-project storage-GB cap, unlike the 512MB Neon free tier this table
    alone kept exhausting). `session` is accepted and left unused so the
    `/discovery` routers calling this did not need a signature change; nothing
    in this function touches Postgres.

    Everything below the candidate pool -- `_freshness_weight`, `_mix_weight`,
    `_apply_mix_and_rank`'s multiplicative `rank = retrieve * freshness * mix`,
    the dataclasses returned -- is exactly what this module always did. Only
    retrieval changed, and it changed in ways worth being as honest about as
    this module's docstring always was about its Postgres cost measurements:

    * `retrieve_score` was `ts_rank_cd` over a weighted tsvector (title
      weighted above company above location above JD body), squashed to
      (0,1]. Appwrite's fulltext index on `search_text` (a plain
      concatenation of title + company_name + location + the first 8000
      chars of jd_clean, built at write time -- see `ingest/upsert.py`) is
      match-or-no-match, not graded. `retrieve` is `1.0` for every row here,
      keyword search or browse alike: a row that failed the fulltext filter
      never reaches this function at all, so there is nothing left to grade.
      A real title hit no longer outranks an incidental JD-body hit within
      one result set the way per-zone `ts_rank_cd` weighting did.
    * `title_keywords` was matched against a title-only tsvector, kept
      deliberately separate from `query`'s whole-document match so a title
      search could not surface a posting that only happened to mention the
      same words somewhere in its JD. Appwrite has one fulltext index, over
      the combined `search_text`; `title_keywords`, `query`, `location`, and
      `company` are folded into a single search string here. That
      separation is gone.
    * `location`/`company` were `ILIKE` substring filters. Appwrite has no
      substring filter on a plain string column; both fold into the same
      fulltext search instead, which matches whole words, not substrings.
    * `max_age_days`/`posted_within_days` filtered on
      `COALESCE(posted_at, first_seen_at)`. Appwrite can't express that
      COALESCE as a filter. `max_age_days` now filters on `last_seen_at`
      (still being re-confirmed by a crawl is, in practice, still within any
      reasonable recency window); `posted_within_days` is unchanged --
      it already filtered `posted_at` directly with `posted_at_estimated`
      false, both real columns here too.
    """
    del session
    started = time.perf_counter()
    now = datetime.now(UTC)
    limit = max(1, min(query.limit, MAX_LIMIT))
    pool_size = min(MAX_CANDIDATES, (limit + query.offset) * CANDIDATE_MULTIPLIER)

    filters: list[str] = []
    raw_queries: list[dict[str, Any]] = []

    if not query.include_inactive:
        filters.append("active=true")
    if not query.include_duplicates:
        raw_queries.append({"method": "isNull", "attribute": "canonical_id"})
    if query.require_description:
        filters.append("jd_hydrated=true")
    if query.sources:
        raw_queries.append({"method": "equal", "attribute": "source", "values": query.sources})
    if query.remote is True:
        filters.append("remote=true")
    if query.country_codes:
        codes = [c.strip().upper() for c in query.country_codes if c.strip()]
        if codes:
            # A hire-from-anywhere posting has no country to match but is
            # plausibly open to the one being filtered on, so it passes.
            # Mirrors the Postgres version's own `matchesCountry` parity note.
            raw_queries.append(
                {
                    "method": "or",
                    "values": [
                        {"method": "equal", "attribute": "country_code", "values": codes},
                        {"method": "equal", "attribute": "anywhere", "values": [True]},
                    ],
                }
            )
    if query.salary_min:
        filters.append(f"salary_max>={query.salary_min}")
    if query.max_age_days and query.max_age_days > 0:
        cutoff = now - timedelta(days=query.max_age_days)
        filters.append(f"last_seen_at>={cutoff.isoformat()}")
    if query.posted_within_days and query.posted_within_days > 0:
        cutoff = now - timedelta(days=query.posted_within_days)
        filters.append(f"posted_at>={cutoff.isoformat()}")
        filters.append("posted_at_estimated=false")

    # Quoted, not raw. A live test against the real table on this exact query
    # ("software engineer intern") is what caught this: an unquoted multi-word
    # search runs MariaDB's fulltext in natural-language mode, which is
    # satisfied by *any* of the words appearing anywhere in `search_text`'s up-
    # to-8000-char JD body -- "software", "engineer", and "intern" are common
    # enough that this matched almost the whole table (an Account Executive
    # posting ranked above real software-engineer-intern listings). Wrapping
    # the phrase in literal double quotes switches MariaDB to phrase mode,
    # requiring the words adjacent and in order; the same query then returned
    # exactly the relevant ~30 rows. A rare term ("MongoDB") had already
    # filtered correctly unquoted, which is what made the bug easy to miss
    # locally and only surface against real, common-vocabulary data.
    phrase_alternatives = [
        _quote_phrase(t) for t in [*query.title_keywords, query.query or ""] if t and t.strip()
    ]
    matched_keywords = bool(phrase_alternatives)
    if len(phrase_alternatives) == 1:
        raw_queries.append(
            {"method": "search", "attribute": "search_text", "values": [phrase_alternatives[0]]}
        )
    elif phrase_alternatives:
        raw_queries.append(
            {
                "method": "or",
                "values": [
                    {"method": "search", "attribute": "search_text", "values": [phrase]}
                    for phrase in phrase_alternatives
                ],
            }
        )
    # Required, not an alternative: unlike title_keywords/query above, these
    # narrow the result set rather than define what counts as a match, so
    # each is its own top-level entry (Appwrite ANDs the query list) rather
    # than joining the `or` group. The nearest thing to Postgres's `ILIKE`
    # substring filter Appwrite's fulltext index actually supports.
    for narrowing in (query.location, query.company):
        if narrowing and narrowing.strip():
            raw_queries.append(
                {"method": "search", "attribute": "search_text", "values": [_quote_phrase(narrowing)]}
            )

    rows = await appwrite_tables.list_rows(
        filters=filters,
        queries=raw_queries,
        select=POOL_COLUMNS,
        sort_desc="last_seen_at",
        limit=pool_size,
    )

    # Appwrite's own `total` on a filtered list is itself an estimate capped
    # well below a full COUNT(*) (observed capping around 5000 against this
    # table's 34,942 rows) -- so rather than trust it as a second, possibly
    # inconsistent number, `total_matched` here is just how many candidates
    # this same pool query actually returned, capped the same way the
    # Postgres version capped its own exact COUNT(*).
    total_matched = len(rows)
    total_capped = total_matched >= pool_size
    if total_matched > TOTAL_COUNT_CAP:
        total_matched = TOTAL_COUNT_CAP
        total_capped = True

    hits = _apply_mix_and_rank(
        [_row_to_tuple(r, now, [*query.title_keywords]) for r in rows],
        limit=limit,
        offset=query.offset,
        explain=query.explain,
        matched_keywords=matched_keywords,
    )
    by_id = await _hydrate_page(hits)
    _attach_snippets(by_id, hits)
    if candidate is not None:
        await _attach_match_scores(by_id, hits, candidate)

    took_ms = (time.perf_counter() - started) * 1000
    return IndexSearchResult(
        hits=hits,
        total_matched=total_matched,
        total_matched_capped=total_capped,
        candidates_considered=len(rows),
        took_ms=took_ms,
        keyword_query=" OR ".join(phrase_alternatives) or None,
    )


async def _hydrate_page(hits: list[IndexHit]) -> dict[Any, dict[str, Any]]:
    """The fat columns, for the page and only the page.

    One extra request, in exchange for not moving `jd_clean` for every one of
    the ~480 candidates the pool query considers. That trade used to run the
    other way: the pool selected every column, on the reasoning that Appwrite
    had already returned them so the snippet pass needed no second round trip.
    It was true and it was expensive -- the pool is eight times the page by
    construction (`CANDIDATE_MULTIPLIER`), `jd_clean` runs to 8000 characters,
    and MariaDB has to sort and serialize all of it before Appwrite can answer.
    Appwrite's reply to that was a 408 ("Database timed out"), which the whole
    retry ladder in `appwrite_tables` exists to paper over and which the web app
    reported to the user as the index "restarting".

    Failing here costs the page its snippets and its cached enrichments, not
    its results: the ranking is already decided, and a search that returns
    correctly ranked jobs without preview text beats one that returns nothing.
    """
    if not hits:
        return {}
    ids = [str(hit.id) for hit in hits]
    try:
        rows = await appwrite_tables.list_rows(
            queries=[{"method": "equal", "attribute": "source_posting_id", "values": ids}],
            select=PAGE_COLUMNS,
            limit=len(ids),
        )
    except appwrite_tables.AppwriteTablesError as exc:
        log.warning("job_index.page_hydrate_failed", error=str(exc)[:300], hits=len(hits))
        return {}
    return {r.get("source_posting_id"): r for r in rows}


def _attach_snippets(by_id: dict[Any, dict[str, Any]], hits: list[IndexHit]) -> None:
    """Fill in the snippet for the page that survived ranking.

    `by_id` comes from `_hydrate_page`, which fetched `jd_clean` for these rows
    alone. A hit missing from it (the hydrate call failed, or the row went away
    between the two queries) keeps the `description_available` flag the pool
    row's own `jd_hydrated` column already gave it, rather than being restated
    as having no description.
    """
    for hit in hits:
        row = by_id.get(str(hit.id))
        if row is None:
            continue
        jd_clean = row.get("jd_clean") or ""
        hit.snippet = jd_clean[:SNIPPET_CHARS]
        hit.description_available = bool(hit.snippet.strip())


def _load_enrichment(row: dict[str, Any]) -> JobEnrichment | None:
    """A row's stored enrichment, or None if absent/unreadable/stale.

    Reuses `job_enrich.load_enrichment` rather than duplicating its
    schema-version check and salvage-tolerant validation -- that function
    expects the `Job.jd_parsed` dict shape it was written against
    (`{"enrichment": {...}}`), which this table does not have, so the raw
    column value is wrapped in that shape here rather than storage being
    reshaped to match a different table's column.
    """
    raw = row.get(ENRICHMENT_COLUMN)
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return job_enrich.load_enrichment({ENRICHMENT_COLUMN: parsed})


async def _attach_match_scores(
    by_id: dict[Any, dict[str, Any]], hits: list[IndexHit], candidate: CandidateProfile
) -> None:
    """Score every hit on this page against `candidate`, enriching first if needed.

    Enrichment is the one LLM call per job the whole design rests on (see
    `docs/job-enrichment.md`) -- it runs here, lazily, the first time a
    posting reaches a page a real search actually returns, rather than eagerly
    over the full crawl. That bounds the cost of a backlog-heavy corpus to
    "at most one page's worth of new calls per search" instead of "the whole
    index, most of which nobody will ever look at". A posting enriched once
    stays enriched: the result is written back to Appwrite so every later
    search reads it for free, exactly like `job_match.score_job` itself.

    Enrichment calls run concurrently, not sequentially -- a first version
    awaited `enrich_job` one at a time in this loop and produced a real
    production incident the first time a user with real profile signal hit a
    page nobody had ever enriched before: up to MAX_ENRICH_PER_SEARCH real
    Sonnet calls summed past Heroku's 30s router timeout (H12), a 503 on the
    whole search rather than a slow one. `asyncio.gather` turns that sum into
    "as long as the slowest call", and `ENRICH_DEADLINE_SECONDS` bounds even
    that: a hit whose enrichment does not finish in time just falls back to
    the client lexicon this once, rather than failing the request.
    """
    already_scored: list[tuple[IndexHit, JobEnrichment]] = []
    to_enrich: list[tuple[IndexHit, dict[str, Any]]] = []
    for hit in hits:
        row = by_id.get(str(hit.id))
        if row is None:
            continue
        enrichment = _load_enrichment(row)
        if enrichment is not None:
            already_scored.append((hit, enrichment))
        elif len(to_enrich) < MAX_ENRICH_PER_SEARCH:
            to_enrich.append((hit, row))

    for hit, enrichment in already_scored:
        hit.match = job_match.score_job(enrichment, candidate)

    if not to_enrich:
        return

    async def _enrich(hit: IndexHit, row: dict[str, Any]) -> JobEnrichment:
        return await asyncio.wait_for(
            job_enrich.enrich_job(
                row.get("jd_clean") or "",
                title_hint=row.get("title"),
                company_hint=row.get("company_name"),
                posted_at=hit.posted_at,
            ),
            timeout=ENRICH_DEADLINE_SECONDS,
        )

    results = await asyncio.gather(
        *(_enrich(hit, row) for hit, row in to_enrich), return_exceptions=True
    )

    to_persist: list[dict[str, Any]] = []
    for (hit, row), result in zip(to_enrich, results, strict=True):
        if isinstance(result, BaseException):
            log.warning(
                "job_index.enrich_deadline_exceeded",
                source_posting_id=row.get("source_posting_id"),
                error=repr(result)[:200],
            )
            continue
        hit.match = job_match.score_job(result, candidate)
        row_id = row.get("$id")
        if row_id:
            to_persist.append(
                {"$id": row_id, ENRICHMENT_COLUMN: json.dumps(result.model_dump(mode="json"))}
            )

    if to_persist:
        try:
            await appwrite_tables.upsert_rows(to_persist)
        except appwrite_tables.AppwriteTablesError as exc:
            # A search that scored jobs correctly but failed to cache the
            # result is a slower future search, not a wrong one -- the next
            # request just enriches these rows again. Not worth failing the
            # search a user is waiting on over a write that only saves money.
            log.warning("job_index.enrichment_persist_failed", error=str(exc)[:300])


def _freshness_weight(age_days: float) -> float:
    """Python twin of the SQL freshness expression. Kept for the EXPLAIN field."""
    decayed: float = 0.5 ** (max(age_days, 0.0) / FRESHNESS_HALF_LIFE_DAYS)
    return max(decayed, MIN_FRESHNESS_WEIGHT)


def _mix_weight(company_rank: int) -> float:
    """Diversity discount for the nth posting from one company on this page.

    Positional rather than row-local, which is why it cannot be done in the same
    pass as the SQL scoring. `company_rank` is 0 for a company's first posting, so
    its best result is never penalized.
    """
    return max(COMPANY_DIVERSITY_DECAY**company_rank, MIN_MIX_WEIGHT)


def _apply_mix_and_rank(
    rows: list[tuple[Any, ...]],
    *,
    limit: int,
    offset: int,
    explain: bool,
    matched_keywords: bool,
) -> list[IndexHit]:
    seen_per_company: dict[str, int] = {}
    scored: list[IndexHit] = []

    for row in rows:
        (
            posting_id,
            source,
            source_id,
            source_url,
            title,
            company_name,
            company_domain,
            location,
            country_code,
            remote,
            department,
            employment_type,
            salary_min,
            salary_max,
            salary_currency,
            jd_hydrated,
            posted_at,
            posted_at_basis,
            posted_at_estimated,
            first_seen_at,
            last_seen_at,
            active,
            inactive_since,
            repost_count,
            text_rank,
            retrieve,
            freshness,
            age_days,
            effective_date,
        ) = row

        company_key = (company_domain or company_name or "").lower()
        company_rank = seen_per_company.get(company_key, 0)
        seen_per_company[company_key] = company_rank + 1

        mix = _mix_weight(company_rank)
        # `retrieve` carries `_title_weight` now, so this product is once again
        # relevance times freshness times diversity rather than freshness times
        # diversity with a constant stapled to the front.
        retrieve_score = float(retrieve)
        freshness_weight = float(freshness)
        rank = retrieve_score * freshness_weight * mix

        hit = IndexHit(
            id=posting_id,
            source=source,
            source_id=source_id,
            source_url=source_url,
            title=title,
            company_name=company_name,
            company_domain=company_domain,
            location=location,
            country_code=country_code,
            remote=remote,
            department=department,
            employment_type=employment_type,
            salary_min=salary_min,
            salary_max=salary_max,
            salary_currency=salary_currency,
            # Both filled by `_attach_snippets` once the page is decided. An
            # unhydrated body is provider metadata, not a job description, so the
            # flag travels with the row rather than letting the UI guess.
            snippet="",
            description_available=bool(jd_hydrated),
            posted_at=posted_at,
            posted_at_basis=posted_at_basis,
            posted_at_estimated=posted_at_estimated,
            first_seen_at=first_seen_at,
            last_seen_at=last_seen_at,
            active=active,
            inactive_since=inactive_since,
            repost_count=repost_count,
            rank=rank,
        )
        if explain:
            hit.explain = ScoreExplain(
                retrieve_score=retrieve_score,
                freshness_weight=freshness_weight,
                mix_weight=mix,
                rank=rank,
                text_rank_raw=float(text_rank or 0.0),
                age_days=float(age_days),
                effective_date=effective_date,
                company_rank=company_rank,
                matched_keywords=matched_keywords,
            )
        scored.append(hit)

    scored.sort(key=lambda h: (-h.rank, -h.last_seen_at.timestamp()))
    return scored[offset : offset + limit]


async def index_stats() -> dict[str, object]:
    """Counters for an ops view and for judging whether the index is worth reading.

    `job_postings` lives in Appwrite now (see `appwrite_tables.py`), which has
    no server-side aggregation -- `count_rows` reads Appwrite's own `total`
    off a `limit(1)` call rather than paging through 35k+ rows, but that only
    works for a plain filter count. A distinct-company count and a group-by-
    source breakdown would each need a full table scan on every call to this
    endpoint, which isn't worth it for an ops view; both are dropped rather
    than silently wrong or slow.

    Every counter degrades to None on its own rather than taking the report
    down with it. This is a diagnostic: five counters and a null is a useful
    answer, and an exception is not.
    """
    async def _count(**kwargs: Any) -> int | None:
        """One counter, or None if Appwrite would not answer in time.

        Six sequential reads, each able to draw a cold fulltext or sort cache
        and time out. All-or-nothing made any one of those lose the entire
        report, and because the workflow step exits non-zero on it, a sweep
        that had crawled 1,958 postings successfully was reported as failed.
        A missing counter is worth far less than the other five together.
        """
        try:
            return await appwrite_tables.count_rows(**kwargs)
        except appwrite_tables.AppwriteTablesError as exc:
            log.warning("index_stats.counter_unavailable", filters=kwargs, error=str(exc)[:200])
            return None

    total = await _count()
    active = await _count(filters=["active=true", "canonical_id=null"])
    duplicates = await _count(filters=["canonical_id!=null"])
    estimated = await _count(filters=["active=true", "posted_at_estimated=true"])
    unhydrated = await _count(filters=["active=true", "jd_hydrated=false"])
    try:
        newest_rows = await appwrite_tables.list_rows(
            select=["last_seen_at"], sort_desc="last_seen_at", limit=1
        )
        newest = newest_rows[0]["last_seen_at"] if newest_rows else None
    except appwrite_tables.AppwriteTablesError as exc:
        # The sorted read, which is the slowest of the six and the one that
        # actually failed in production: a cold `last_seen_at` sort measured
        # 24s against 1.75s warm.
        log.warning("index_stats.newest_unavailable", error=str(exc)[:200])
        newest = None

    return {
        "postings_total": total,
        "postings_active": active,
        "duplicates_marked": duplicates,
        # Reported rather than buried: a searcher deserves to know how much of the
        # index has a date we inferred instead of one the employer published.
        "posted_at_estimated": estimated,
        "descriptions_missing": unhydrated,
        "last_crawl_seen_at": newest,
    }


def promote_payload(hit: IndexHit) -> dict[str, object]:
    """Fields to copy when an index row becomes a tracked `jobs` row.

    The one-way door between the two tables. `job_postings` and `jobs` share a
    column vocabulary precisely so this stays a field copy rather than a
    re-derivation, and so `/discovery/import` keeps working unchanged.
    """
    return {
        "title": hit.title,
        "company_name": hit.company_name,
        "company_domain": hit.company_domain,
        "location": hit.location,
        "source": hit.source,
        "source_id": hit.source_id,
        "source_url": hit.source_url,
        "posted_at": hit.posted_at,
    }


def ranking_constants() -> dict[str, float]:
    """The tunables, so the web app can describe the ranking without copying it.

    `rank_saturation_k` is deliberately absent: it tuned `ts_rank_cd`'s
    squash curve, and Appwrite's fulltext match has no graded score for that
    curve to act on anymore (see `RANK_SATURATION_K`'s own comment).
    Reporting a number that no longer shapes any result would be exactly the
    kind of implied precision this module's docstrings have never allowed.
    """
    return {
        "freshness_half_life_days": FRESHNESS_HALF_LIFE_DAYS,
        "min_freshness_weight": MIN_FRESHNESS_WEIGHT,
        "company_diversity_decay": COMPANY_DIVERSITY_DECAY,
        "min_mix_weight": MIN_MIX_WEIGHT,
        "title_match_weight": TITLE_MATCH_WEIGHT,
        "body_only_match_weight": BODY_ONLY_MATCH_WEIGHT,
    }
