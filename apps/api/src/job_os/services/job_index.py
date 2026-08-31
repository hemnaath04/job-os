"""Answer a job search from the index instead of fanning out to 85 boards.

This is the read half of the ingest work. The live path in the web app's
`lib/discover/no-key-sources.ts` fetches every curated board on every search,
which makes search latency the sum of someone else's API latency and caps coverage
at the boards in that file. This module answers the same question with one indexed
query and does not touch the network.

**Storage.** `job_postings` is a Neon Postgres table again. It spent 2026-08-18
to 2026-08-31 in Appwrite TablesDB, which bills reads PER ROW: one search reads a
candidate pool of up to 2,000 rows, the 6-hourly crawl alone measured ~1.19M
reads a month against a 1.75M allowance, and when the allowance ran out every
Appwrite read on the project answered 402 `limit_databases_reads_exceeded` --
taking resumes and tailoring down with it, since they share the quota. Postgres
charges for storage, which this workload can bound; see
`db/models/job_posting.py` for what was traded to make it fit.

**Ranking.** `rank = retrieve_score * freshness_weight * mix_weight`, multiplicative
so freshness cannot be swamped by a marginally better keyword match. With an
additive score, a perfect title hit from eight months ago outranks a good hit from
this morning, which is the wrong answer for a job search: the old one is probably
filled. Multiplying means a stale posting has to be substantially more relevant to
beat a fresh one, rather than slightly.

  * `retrieve_score`  `ts_rank_cd` over the weighted tsvector, normalized to (0,1].
                      1.0 when no keywords were given, so a browse is ranked purely
                      on freshness and mix. This is graded again: the Appwrite
                      version had only a pass/fail fulltext match, so every row
                      scored a flat 1.0 and a Director of Litigation whose JD
                      mentioned an internship programme opened a search for
                      "software engineer intern". The tsvector's own A/B/C/D
                      weighting (title above company above location above body)
                      is what makes that distinction for free, at retrieval
                      time, instead of in a Python re-scoring pass.
  * `freshness_weight` exponential decay on the effective date with a 14-day half
                      life, floored so an old-but-perfect match is demoted rather
                      than deleted.
  * `mix_weight`      diversity: the nth posting from one company is progressively
                      discounted, so one employer mid-hiring-spree cannot own the
                      page. Positional, so it is applied in Python after SQL has
                      produced a candidate pool.

**Two-phase, and why.** SQL filters and scores a candidate pool on
`retrieve_score * freshness_weight`, which are both row-local, then Python applies
the positional `mix_weight` and re-sorts. Doing diversity in SQL would need a
window function over the whole match set, which is the expensive part of the query
for a value that only affects the ordering of one page.

The pool query deliberately does not select `jd_clean`. Measured on 19,461 real
crawled postings, a 480-row pool cost 8.5ms selecting the narrow columns and 87.4ms
selecting the same rows plus `left(jd_clean, 400)`. `jd_clean` averages 5,569
characters and 17,204 of those 19,461 rows exceed 2KB, so Postgres keeps it out of
line in TOAST and has to fetch and decompress it once per row. The body is
therefore a second query over the ~60 rows that survived ranking rather than the
480 that were considered, and that query measures 0.9ms. The same shape survived
the Appwrite detour for a different reason (a 4MB payload for MariaDB to sort,
answered with a 408); it is back here for the original one.

The result count gets the same treatment: an exact `COUNT(*)` over a keyword match
measured 48.4ms against 4.7ms bounded at `TOTAL_COUNT_CAP`, so the count is capped
and `total_matched_capped` says so rather than implying a precise total.

**Freshness is reported, not asserted.** Every result carries `first_seen_at`,
`last_seen_at`, and `posted_at_estimated`, so the UI can say "first seen 3 weeks
ago, still listed 1 hour ago" rather than presenting a re-dated repost as new.
`posted_at_basis` says where the date came from. See `ingest/providers/base.py`.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import DateTime, Float, func, literal, or_, select, type_coerce, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from job_os.db.models.job_posting import JobPosting
from job_os.schemas.enrichment import JobEnrichment
from job_os.services import job_enrich, job_match
from job_os.services.job_match import CandidateProfile, MatchScore

log = structlog.get_logger(__name__)

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
#: `ts_rank_cd` is unbounded, so it is squashed into (0,1] by x/(x+k). k sets where
#: the curve bends; 1.0 keeps typical scores spread across the useful range instead
#: of saturating near 1.
RANK_SATURATION_K = 1.0
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


@dataclass(slots=True)
class _PageRow:
    """The fat columns for one hit on the page, fetched after ranking.

    `jd_clean` is None when nothing on this request needed the whole body --
    that is, when there is no candidate profile to score against. The snippet
    is always present because the UI always shows it, and it is `left(jd_clean,
    SNIPPET_CHARS)` computed in SQL so an 8KB description is not moved to
    render 400 characters of it.
    """

    snippet: str
    jd_parsed: dict[str, Any]
    title: str
    company_name: str
    jd_clean: str | None = None


def _tsquery(keywords: list[str], free_text: str | None) -> str | None:
    """Build a tsquery string: phrases AND internally, alternatives OR'd.

    Mirrors the semantics the live path already implements in `matchesTitle`: every
    word of a phrase must appear, and the phrases are alternatives. So
    "ai engineer intern" finds "AI/ML Engineer Intern" and "Software Engineer
    Intern, AI", which a phrase-adjacency search does not.

    The `&` between a phrase's words is what an Appwrite-era bug report is
    really about: MariaDB's fulltext ran an unquoted multi-word search in
    natural-language mode, satisfied by ANY of the words appearing anywhere in
    the concatenated body, so "software engineer intern" matched most of the
    table and returned an Account Executive posting first. `to_tsquery` with
    `&` requires every word, which is stricter than the quoting fix that
    replaced it and is what this path always did.
    """
    groups: list[str] = []
    for phrase in [*keywords, free_text or ""]:
        words = [w for w in _words(phrase) if w]
        if words:
            groups.append(" & ".join(words))
    if not groups:
        return None
    return " | ".join(f"({g})" for g in groups)


def _words(text: str) -> list[str]:
    out: list[str] = []
    for chunk in text.lower().replace("/", " ").split():
        cleaned = "".join(c for c in chunk if c.isalnum() or c in "+#-")
        cleaned = cleaned.strip("-")
        if cleaned:
            out.append(cleaned)
    return out


async def search_index(
    session: AsyncSession,
    query: IndexQuery,
    *,
    candidate: CandidateProfile | None = None,
) -> IndexSearchResult:
    """Retrieval against Postgres `job_postings`.

    `candidate` is the one thing here that postdates the original Postgres
    version: when a signed-in user has real profile signal, every hit on the
    returned page is scored against it (`_attach_match_scores`), enriching the
    posting first if nobody has yet. Passing None keeps the old behaviour
    exactly, which is what the frontend's own lexicon fallback renders against.
    """
    started = time.perf_counter()
    now = datetime.now(UTC)
    limit = max(1, min(query.limit, MAX_LIMIT))
    pool_size = min(MAX_CANDIDATES, (limit + query.offset) * CANDIDATE_MULTIPLIER)

    tsquery_text = _tsquery(query.title_keywords, query.query)
    effective_date = func.coalesce(JobPosting.posted_at, JobPosting.first_seen_at)

    # title_keywords is documented ("mirrors matchesTitle") as a title-only match,
    # but until now it was ANDed into the same tsquery as free-text `query` and
    # tested against the whole weighted `search_vector` -- title OR company_name OR
    # location OR up to FTS_DESCRIPTION_CHARS of the JD. That let a title search for
    # "ai engineer intern" surface "Head of IT" and "BizOps Lead" postings whose JD
    # body happened to mention "ai", "engineer" and "intern" nowhere near each
    # other, crowding out the small number of postings actually titled that. The
    # ranking below still scores against the full `search_vector` (title weighted
    # highest via setweight), so a real title hit is still preferred over a
    # same-tsquery body hit; only which rows are allowed to match at all changes.
    title_tsquery_text = _tsquery(query.title_keywords, None)
    free_tsquery_text = _tsquery([], query.query)
    title_vector = func.to_tsvector("english", func.coalesce(JobPosting.title, ""))
    title_ts_query = (
        func.to_tsquery("english", title_tsquery_text) if title_tsquery_text else None
    )
    free_ts_query = (
        func.to_tsquery("english", free_tsquery_text) if free_tsquery_text else None
    )

    ts_query: ColumnElement[Any] | None = None
    text_rank: ColumnElement[float]
    retrieve: ColumnElement[float]
    if tsquery_text:
        ts_query = func.to_tsquery("english", tsquery_text)
        text_rank = func.ts_rank_cd(JobPosting.search_vector, ts_query)
        # Squash the unbounded ts_rank_cd into (0,1] so the product with freshness
        # stays interpretable and one enormous text score cannot dominate.
        # `type_coerce` rather than `cast`: dividing two SQL floats is typed as
        # float-or-Decimal, and this says "read it back as a float" without
        # putting a CAST in the generated SQL that the planner would have to see.
        retrieve = type_coerce(text_rank / (text_rank + RANK_SATURATION_K), Float)
    else:
        # SQL literals, not Python floats: these have to be expressions so the same
        # ORDER BY and the same selected columns work with or without keywords.
        text_rank = literal(0.0, Float)
        retrieve = literal(1.0, Float)

    # Freshness in SQL, so filtering, ranking and the LIMIT happen in one pass.
    # `now` is bound rather than using SQL now(), so a frozen clock in a test
    # reaches the query and the Python twin below agrees with it exactly.
    age_days = (
        func.extract("epoch", literal(now, DateTime(timezone=True)) - effective_date) / 86400.0
    )
    freshness = func.greatest(
        func.power(0.5, func.greatest(age_days, 0.0) / FRESHNESS_HALF_LIFE_DAYS),
        MIN_FRESHNESS_WEIGHT,
    )
    sql_rank = retrieve * freshness

    statement = select(
        JobPosting.id,
        JobPosting.source,
        JobPosting.source_id,
        JobPosting.source_url,
        JobPosting.title,
        JobPosting.company_name,
        JobPosting.company_domain,
        JobPosting.location,
        JobPosting.country_code,
        JobPosting.remote,
        JobPosting.department,
        JobPosting.employment_type,
        JobPosting.salary_min,
        JobPosting.salary_max,
        JobPosting.salary_currency,
        # No jd_clean here on purpose. It is TOASTed and fetching it for the whole
        # candidate pool was 97% of this query's cost; see the module docstring.
        JobPosting.jd_hydrated,
        JobPosting.posted_at,
        JobPosting.posted_at_basis,
        JobPosting.posted_at_estimated,
        JobPosting.first_seen_at,
        JobPosting.last_seen_at,
        JobPosting.active,
        JobPosting.inactive_since,
        JobPosting.repost_count,
        text_rank.label("text_rank"),
        retrieve.label("retrieve"),
        freshness.label("freshness"),
        age_days.label("age_days"),
        effective_date.label("effective_date"),
    )

    conditions: list[ColumnElement[bool]] = []
    if not query.include_inactive:
        conditions.append(JobPosting.active.is_(True))
    if not query.include_duplicates:
        conditions.append(JobPosting.canonical_id.is_(None))
    if query.require_description:
        conditions.append(JobPosting.jd_hydrated.is_(True))
    match_conditions: list[ColumnElement[bool]] = []
    if title_ts_query is not None:
        match_conditions.append(title_vector.op("@@")(title_ts_query))
    if free_ts_query is not None:
        match_conditions.append(JobPosting.search_vector.op("@@")(free_ts_query))
    if match_conditions:
        conditions.append(
            match_conditions[0] if len(match_conditions) == 1 else or_(*match_conditions)
        )
    if query.location:
        conditions.append(JobPosting.location.ilike(f"%{query.location.strip()}%"))
    if query.company:
        conditions.append(JobPosting.company_name.ilike(f"%{query.company.strip()}%"))
    if query.sources:
        conditions.append(JobPosting.source.in_(query.sources))
    if query.remote is True:
        conditions.append(JobPosting.remote.is_(True))
    if query.country_codes:
        codes = [c.strip().upper() for c in query.country_codes if c.strip()]
        if codes:
            # A hire-from-anywhere posting has no country to match but is
            # plausibly open to the one being filtered on, so it passes. A posting
            # whose location we simply could not parse does not: unknown is not
            # the same as yes. This mirrors the live path's `matchesCountry`.
            conditions.append(
                or_(JobPosting.country_code.in_(codes), JobPosting.anywhere.is_(True))
            )
    if query.max_age_days and query.max_age_days > 0:
        cutoff = now - timedelta(days=query.max_age_days)
        # Filters on the effective date, so a posting whose board gave no date is
        # judged by when we first saw it rather than silently kept forever. The
        # Appwrite version had to filter `last_seen_at` instead, because that
        # COALESCE is not expressible as a TablesDB query.
        conditions.append(effective_date >= cutoff)
    if query.posted_within_days and query.posted_within_days > 0:
        cutoff = now - timedelta(days=query.posted_within_days)
        # Stricter: only postings with a real, non-estimated date inside the window.
        conditions.append(JobPosting.posted_at >= cutoff)
        conditions.append(JobPosting.posted_at_estimated.is_(False))
    if query.salary_min:
        conditions.append(JobPosting.salary_max >= query.salary_min)

    statement = statement.where(*conditions)
    statement = statement.order_by(sql_rank.desc(), effective_date.desc()).limit(pool_size)

    rows = (await session.execute(statement)).all()

    # Bounded count. Stops the scan at TOTAL_COUNT_CAP rather than counting every
    # match, which for a broad keyword query is most of the table.
    capped = (
        select(JobPosting.id).where(*conditions).limit(TOTAL_COUNT_CAP + 1).subquery()
    )
    total_matched = int(
        await session.scalar(select(func.count()).select_from(capped)) or 0
    )
    total_capped = total_matched > TOTAL_COUNT_CAP
    if total_capped:
        total_matched = TOTAL_COUNT_CAP

    hits = _apply_mix_and_rank(
        [tuple(row) for row in rows],
        limit=limit,
        offset=query.offset,
        explain=query.explain,
        matched_keywords=bool(tsquery_text),
    )
    page = await _fetch_page(session, hits, want_body=candidate is not None)
    _attach_snippets(page, hits)
    if candidate is not None:
        await _attach_match_scores(session, page, hits, candidate)

    took_ms = (time.perf_counter() - started) * 1000
    return IndexSearchResult(
        hits=hits,
        total_matched=total_matched,
        total_matched_capped=total_capped,
        candidates_considered=len(rows),
        took_ms=took_ms,
        keyword_query=tsquery_text,
    )


async def _fetch_page(
    session: AsyncSession, hits: list[IndexHit], *, want_body: bool
) -> dict[uuid.UUID, _PageRow]:
    """Second phase: the fat columns, for the page rather than for the pool.

    One extra round trip in exchange for reading TOAST storage tens of times
    instead of hundreds. `want_body` is the difference between a search that
    only has to render snippets and one that also has to score fit: the whole
    `jd_clean` is the input to `job_enrich.enrich_job`, so it is fetched only
    when there is a profile to score against, and `left(jd_clean, 400)` is
    computed in SQL otherwise.

    Every hit is fetched, not only the ones flagged `description_available`.
    An unhydrated row still needs its `jd_parsed` read (that is where a cached
    enrichment lives), and the flag it carries is settled by `_attach_snippets`
    from the two facts together rather than by which rows this query skipped.
    """
    if not hits:
        return {}
    columns: list[Any] = [
        JobPosting.id,
        func.left(JobPosting.jd_clean, SNIPPET_CHARS).label("snippet"),
        JobPosting.jd_parsed,
        JobPosting.title,
        JobPosting.company_name,
    ]
    if want_body:
        columns.append(JobPosting.jd_clean)
    result = await session.execute(
        select(*columns).where(JobPosting.id.in_([hit.id for hit in hits]))
    )
    page: dict[uuid.UUID, _PageRow] = {}
    for row in result.all():
        page[row.id] = _PageRow(
            snippet=row.snippet or "",
            jd_parsed=row.jd_parsed or {},
            title=row.title,
            company_name=row.company_name,
            jd_clean=row.jd_clean if want_body else None,
        )
    return page


def _attach_snippets(page: dict[uuid.UUID, _PageRow], hits: list[IndexHit]) -> None:
    """Fill in the snippet for the page that survived ranking.

    The flag is a CONJUNCTION of two facts, not whichever was computed last.
    This line used to be `= bool(hit.snippet.strip())`, which silently discarded
    the `jd_hydrated` half: an unhydrated SmartRecruiters listing carries a body
    like "Engineer\\nAcme\\nBoston", which is provider metadata rather than a job
    description, and that is not empty -- so the flag flipped to True and the
    read path advertised a description that does not exist. `require_description`
    filters on `jd_hydrated` server-side and was never affected, which is how the
    two halves came to disagree: the filter was right and the flag the UI reads
    was wrong. The unhydrated case is the one this was written for; a hydrated
    row whose body turned out empty is the same answer for the other reason, and
    an `and` gets both.
    """
    for hit in hits:
        row = page.get(hit.id)
        if row is None:
            continue
        hit.snippet = row.snippet
        hit.description_available = hit.description_available and bool(hit.snippet.strip())


async def _attach_match_scores(
    session: AsyncSession,
    page: dict[uuid.UUID, _PageRow],
    hits: list[IndexHit],
    candidate: CandidateProfile,
) -> None:
    """Score every hit on this page against `candidate`, enriching first if needed.

    Enrichment is the one LLM call per job the whole design rests on (see
    `docs/job-enrichment.md`) -- it runs here, lazily, the first time a
    posting reaches a page a real search actually returns, rather than eagerly
    over the full crawl. That bounds the cost of a backlog-heavy corpus to
    "at most one page's worth of new calls per search" instead of "the whole
    index, most of which nobody will ever look at". A posting enriched once
    stays enriched: the result is written back to `job_postings.jd_parsed` so
    every later search reads it for free, exactly like `job_match.score_job`
    itself. `jd_parsed` is the same JSONB column and the same
    `store_enrichment`/`load_enrichment` pair the `jobs` table uses, so this is
    no longer wrapping a raw column value in a fake `{"enrichment": ...}` shape
    to reuse them, the way the Appwrite version had to.

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
    to_enrich: list[tuple[IndexHit, _PageRow]] = []
    for hit in hits:
        row = page.get(hit.id)
        if row is None:
            continue
        enrichment = job_enrich.load_enrichment(row.jd_parsed)
        if enrichment is not None:
            already_scored.append((hit, enrichment))
        elif len(to_enrich) < MAX_ENRICH_PER_SEARCH:
            to_enrich.append((hit, row))

    for hit, enrichment in already_scored:
        hit.match = job_match.score_job(enrichment, candidate)

    if not to_enrich:
        return

    async def _enrich(hit: IndexHit, row: _PageRow) -> JobEnrichment:
        return await asyncio.wait_for(
            job_enrich.enrich_job(
                row.jd_clean or "",
                title_hint=row.title,
                company_hint=row.company_name,
                posted_at=hit.posted_at,
            ),
            timeout=ENRICH_DEADLINE_SECONDS,
        )

    results = await asyncio.gather(
        *(_enrich(hit, row) for hit, row in to_enrich), return_exceptions=True
    )

    to_persist: list[tuple[uuid.UUID, dict[str, Any]]] = []
    for (hit, row), result in zip(to_enrich, results, strict=True):
        if isinstance(result, BaseException):
            log.warning(
                "job_index.enrich_deadline_exceeded",
                posting_id=str(hit.id),
                error=repr(result)[:200],
            )
            continue
        hit.match = job_match.score_job(result, candidate)
        to_persist.append((hit.id, job_enrich.store_enrichment(row.jd_parsed, result)))

    if not to_persist:
        return
    try:
        for posting_id, jd_parsed in to_persist:
            await session.execute(
                update(JobPosting)
                .where(JobPosting.id == posting_id)
                .values(jd_parsed=jd_parsed)
            )
        # Committed here rather than left to the request's own commit, so a
        # cache that cost real LLM calls survives a later failure in the same
        # request. Safe to do to a caller's session because `async_session` is
        # built with `expire_on_commit=False` (see `db/session.py`): a commit
        # does not expire the User and ProfileFact rows the router loaded
        # before this, so nothing above triggers a lazy refresh afterwards.
        await session.commit()
    except Exception as exc:  # noqa: BLE001 - a cache write must not fail a search
        # A search that scored jobs correctly but failed to cache the result is
        # a slower future search, not a wrong one -- the next request just
        # enriches these rows again. Not worth failing the search a user is
        # waiting on over a write that only saves money. The rollback matters:
        # this is the request's session, and leaving it in a failed transaction
        # would break the response serialization that follows.
        await session.rollback()
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


async def index_stats(session: AsyncSession) -> dict[str, object]:
    """Counters for an ops view and for judging whether the index is worth reading.

    Every number here is an exact `COUNT(*)`, which is worth stating because for
    the two weeks this table lived in Appwrite none of them could be. Appwrite's
    own `total` saturates at 5,000, so on a 359,416-row table every counter read
    exactly 5,000 and said nothing; the alternative, walking the table with a
    cursor to count it properly, cost one billed read per row and is what
    exhausted the project's monthly quota. `counts_exact` is reported so a
    consumer that learned to check it during that period keeps working, and it
    is now always True.

    `companies_active` and `by_source` are back for the same reason: they need
    server-side aggregation, which is a `GROUP BY` here and a full table scan
    per call there.
    """
    total = await session.scalar(select(func.count()).select_from(JobPosting))
    active = await session.scalar(
        select(func.count())
        .select_from(JobPosting)
        .where(JobPosting.active.is_(True), JobPosting.canonical_id.is_(None))
    )
    companies = await session.scalar(
        select(func.count(func.distinct(func.lower(JobPosting.company_name)))).where(
            JobPosting.active.is_(True)
        )
    )
    duplicates = await session.scalar(
        select(func.count()).select_from(JobPosting).where(JobPosting.canonical_id.is_not(None))
    )
    estimated = await session.scalar(
        select(func.count())
        .select_from(JobPosting)
        .where(JobPosting.active.is_(True), JobPosting.posted_at_estimated.is_(True))
    )
    unhydrated = await session.scalar(
        select(func.count())
        .select_from(JobPosting)
        .where(JobPosting.active.is_(True), JobPosting.jd_hydrated.is_(False))
    )
    newest = await session.scalar(select(func.max(JobPosting.last_seen_at)))
    by_source = (
        await session.execute(
            select(JobPosting.source, func.count())
            .where(JobPosting.active.is_(True), JobPosting.canonical_id.is_(None))
            .group_by(JobPosting.source)
        )
    ).all()

    return {
        "counts_exact": True,
        "postings_total": int(total or 0),
        "postings_active": int(active or 0),
        "companies_active": int(companies or 0),
        "duplicates_marked": int(duplicates or 0),
        # Reported rather than buried: a searcher deserves to know how much of the
        # index has a date we inferred instead of one the employer published.
        "posted_at_estimated": int(estimated or 0),
        "descriptions_missing": int(unhydrated or 0),
        "last_crawl_seen_at": newest,
        "by_source": {source: int(count) for source, count in by_source},
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
    """The tunables, so the web app can describe the ranking without copying it."""
    return {
        "freshness_half_life_days": FRESHNESS_HALF_LIFE_DAYS,
        "min_freshness_weight": MIN_FRESHNESS_WEIGHT,
        "rank_saturation_k": RANK_SATURATION_K,
        "company_diversity_decay": COMPANY_DIVERSITY_DECAY,
        "min_mix_weight": MIN_MIX_WEIGHT,
    }
