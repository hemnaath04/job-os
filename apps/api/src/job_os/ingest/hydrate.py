"""The second pass: fetch the body a board's list endpoint did not carry.

    pick unhydrated rows, newest first -> one detail request each
                                       -> write back body and date

Five of the eight providers write rows with `jd_hydrated=False` because their
list endpoint has no description in it at all: Workday, SmartRecruiters,
BambooHR, iCIMS and Oracle. Four of them already exposed a `hydrate()` that
turns one row into a real body, and nothing ever called it; SmartRecruiters
had none at all, so its rows carried a flag nothing could clear. Measured
against the live index on 2026-08-30, before this module: `descriptions_missing`
was 5,000 of `postings_active` 5,000 -- both numbers were Appwrite's capped
`total` estimate rather than a true count, so read them as "the whole visible
index", not as exactly five thousand rows. The same counters are exact
`COUNT(*)`s again now that the table is back on Postgres.

The cost of that gap is not that a posting looks thin. It is that
`job_index.search_index` matches on `search_vector`, which for an unhydrated row
is a title, a company, a location and a few taxonomy labels, so the index can
rank a posting on its title and cannot score it on its body; the tailor, which
reads the description, has nothing to read at all.

**This is an N+1 and it is bounded like one.** One posting is one request, so
the whole unhydrated set is the whole unhydrated set in requests. `--limit`
caps postings per run and defaults to `DEFAULT_LIMIT`; coverage accumulates
across runs the way the sweep's own `DEFAULT_TOKEN_LIMIT` intends it to.

**A failed hydrate never deactivates a row, and that is deliberate.** A detail
endpoint answering 404 is good evidence the posting closed between the crawl
and now. The problem is that this pass cannot see the 404: every provider's
`hydrate()` swallows a bad response and returns the posting unchanged, so a
timeout, a 429 that outlived its retries, a payload with an empty description
and a genuine 404 all arrive here as the same "not hydrated". Deactivating on
that would mean closing live postings because a vendor was slow, which is the
exact mistake `BoardStatus` and `deactivate_missing` exist to prevent
(`worker.py`: "A sweep never destroys data on incomplete information"). The
list crawl is already the authority on whether a posting is still listed, and
it decides that from a board it actually re-read. A posting that really closed
disappears from its board's next list and is deactivated there, with that
guard behind it. So a failure here is counted, recorded on the row, and
skipped.

**One thing this pass no longer has to get right.** While `job_postings` lived
in Appwrite it also had to rewrite `search_text`, a stored copy of the text the
fulltext index matched on, byte-for-byte the way `ingest/upsert.py` built it --
a second writer of a derived value, with a comment explaining what would break
if the two ever drifted. Postgres computes `search_vector` as a STORED
generated column, so replacing `jd_clean` here updates the index by
construction and there is nothing left to keep in step.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from job_os.db.models.job_posting import JobPosting
from job_os.ingest import normalize
from job_os.ingest.fetcher import DEFAULT_CONCURRENCY, BoardTiming, PoliteFetcher
from job_os.ingest.providers import RawPosting, get_provider

log = structlog.get_logger(__name__)

#: Postings hydrated per run unless `--limit` says otherwise.
#:
#: 200 is `job_index.MAX_LIMIT`, the largest page of results a single search
#: can ask for, so one run fills at least one whole page of the freshest end of
#: the index rather than sprinkling bodies across it. It is also small enough
#: to stay obviously polite: because candidates come out newest-first they tend
#: to arrive in board-sized clumps (measured: the newest 1,000 unhydrated rows
#: were 1,000 of 1,000 from one BambooHR sweep spanning 51 seconds), so a run
#: can be 200 requests aimed at one vendor, which at the fetcher's per-host
#: ceiling of 8 is a steady trickle rather than a burst.
#:
#: Runtime is not what bounds this. A live run of 200 through the real fetcher
#: took 11.0 seconds and moved 4.1 MB, so a scheduler could afford far more.
#: What 200 bounds is how much of someone else's API one run is entitled to
#: spend, and that is a judgement rather than a measurement: unverified whether
#: a larger default would draw a rate limit from any of the five vendors, since
#: no run so far has been answered with a 429.
DEFAULT_LIMIT = 200

#: Candidate rows read per run, as a multiple of `limit`. Rows are dropped
#: before any request is made (a source with no `hydrate()`, a row that has
#: already failed its attempts), so reading exactly `limit` rows would let a
#: run of nothing-but-skips do no work at all while still reporting success.
POOL_MULTIPLIER = 4

#: Ceiling on that pool regardless of `limit`. Matches `job_index`'s own
#: `MAX_CANDIDATES`: it is the largest page this codebase asks for anywhere.
MAX_POOL = 2_000

#: How many times one row may fail hydration before the pass stops spending
#: requests on it. Without a ceiling the newest-first ordering would hand every
#: run the same dead rows forever, since a failure leaves `jd_hydrated=False`
#: and `last_seen_at` untouched, so the row keeps its place at the front of the
#: queue. Three because the fetcher already retries transient failures inside
#: one attempt (`MAX_ATTEMPTS`), so a row that fails three separate runs is
#: failing for a reason waiting will not fix.
MAX_ROW_ATTEMPTS = 3

#: Where that counter lives. `job_postings` has no column for it, and it does
#: not deserve one: it goes in the provider `extra` blob under a namespaced
#: key. Safe there: nothing reads `job_postings.jd_parsed` for provider data (a
#: promoted job gets a fresh LLM parse -- see `job_index.promote_payload`), and
#: a sweep that sees a genuine content change rewrites the whole blob from the
#: provider's own `extra`, which resets the counter. That reset is correct
#: rather than accidental: a posting the employer just edited deserves a fresh
#: try.
#:
#: The one other writer of this column is `job_index._attach_match_scores`,
#: which stores a cached enrichment under its own key via
#: `job_enrich.store_enrichment`. The two do not collide because both merge
#: into the existing dict rather than replacing it, and neither uses the
#: other's key.
_ATTEMPTS_KEY = "hydrate_attempts"


@runtime_checkable
class SupportsHydrate(Protocol):
    """A provider that can turn one row into a real description.

    Three of the eight cannot and never need to: Greenhouse, Lever and Ashby
    carry the body in the list response, so their rows are born hydrated.
    `isinstance` against a runtime-checkable Protocol only asserts the
    attribute exists, not that its signature matches -- good enough here, since
    the five that define it were all written against the same three-argument
    shape, and a provider that got it wrong would surface as a recorded
    per-row error rather than a crash.
    """

    async def hydrate(self, fetcher: Any, token: str, posting: RawPosting) -> RawPosting: ...


@dataclass(slots=True)
class _Candidate:
    """One unhydrated row, with only the columns this pass reads.

    `jd_clean` is deliberately absent. No provider's `hydrate()` reads the old
    body -- all five replace it outright -- and `job_index`'s own docstring
    measured what selecting it costs: `jd_clean` is TOASTed, and fetching it
    for a pool this size was 97% of the equivalent query's cost.
    """

    id: uuid.UUID
    source: str
    board_token: str
    external_id: str
    title: str
    company_name: str
    company_domain: str | None
    location: str | None
    source_url: str
    jd_parsed: dict[str, Any]
    posted_at: datetime | None
    posted_at_basis: str


@dataclass(slots=True)
class HydrateResult:
    candidates_scanned: int = 0
    attempted: int = 0
    hydrated: int = 0
    failed: int = 0
    basis_upgraded: int = 0
    rows_written: int = 0
    #: Rows dropped before a request was made, keyed by why. Reported rather
    #: than silently filtered: a run whose whole pool was unhydratable looks
    #: identical to a run with nothing to do unless it says so.
    skipped_no_hydrate: dict[str, int] = field(default_factory=dict)
    skipped_exhausted: int = 0
    hydrated_by_source: dict[str, int] = field(default_factory=dict)
    requests_made: int = 0
    bytes_fetched: int = 0
    duration_s: float = 0.0
    errors: list[str] = field(default_factory=list)

    @property
    def postings_per_second(self) -> float:
        return self.attempted / self.duration_s if self.duration_s else 0.0

    def as_dict(self) -> dict[str, object]:
        return {
            "candidates_scanned": self.candidates_scanned,
            "attempted": self.attempted,
            "hydrated": self.hydrated,
            "failed": self.failed,
            "basis_upgraded": self.basis_upgraded,
            "rows_written": self.rows_written,
            "skipped_no_hydrate": dict(sorted(self.skipped_no_hydrate.items())),
            "skipped_exhausted": self.skipped_exhausted,
            "hydrated_by_source": dict(sorted(self.hydrated_by_source.items())),
            "requests_made": self.requests_made,
            "bytes_fetched": self.bytes_fetched,
            "duration_s": round(self.duration_s, 2),
            "postings_per_second": round(self.postings_per_second, 1),
            "errors": self.errors[:10],
        }


async def hydrate_descriptions(
    session: AsyncSession,
    *,
    providers: list[str] | None = None,
    limit: int = DEFAULT_LIMIT,
    concurrency: int = DEFAULT_CONCURRENCY,
    fetcher: PoliteFetcher | None = None,
) -> HydrateResult:
    """Fetch and store real descriptions for up to `limit` unhydrated postings."""
    result = HydrateResult()
    timing = BoardTiming()
    limit = max(1, limit)

    rows = await _candidate_rows(
        session, providers, pool_size=min(MAX_POOL, limit * POOL_MULTIPLIER)
    )
    result.candidates_scanned = len(rows)

    work: list[tuple[_Candidate, SupportsHydrate, RawPosting]] = []
    for row in rows:
        if len(work) >= limit:
            break
        provider = _hydrating_provider(row.source)
        if provider is None:
            # Rows a source that cannot hydrate produced. Real, and not
            # hypothetical: `scraper_import` files rows under whatever string
            # the standalone scraper calls the ATS, and it sets
            # `jd_hydrated=False` whenever its export carried no description,
            # so the live index holds unhydrated rows under `greenhouse` -- a
            # provider with no `hydrate()` at all. Counted by source and
            # dropped, never re-queued.
            source = row.source or "unknown"
            result.skipped_no_hydrate[source] = result.skipped_no_hydrate.get(source, 0) + 1
            continue
        if _attempts(row.jd_parsed) >= MAX_ROW_ATTEMPTS:
            result.skipped_exhausted += 1
            continue
        work.append((row, provider, _row_to_posting(row)))

    if not work:
        result.duration_s = timing.stop()
        log.info("ingest.hydrate_done", **result.as_dict())
        return result

    owns_fetcher = fetcher is None
    client = fetcher or PoliteFetcher(concurrency=concurrency, per_host_concurrency=concurrency)
    try:
        outcomes = await asyncio.gather(
            *(
                _hydrate_one(client, provider, posting)
                for _row, provider, posting in work
            )
        )
    finally:
        if owns_fetcher:
            await client.aclose()

    patches: list[tuple[uuid.UUID, dict[str, Any]]] = []
    for (row, _provider, _posting), hydrated in zip(work, outcomes, strict=True):
        result.attempted += 1
        if hydrated is None:
            result.failed += 1
            patches.append((row.id, _failure_patch(row)))
            continue
        result.hydrated += 1
        result.hydrated_by_source[row.source] = result.hydrated_by_source.get(row.source, 0) + 1
        patch = _success_patch(row, hydrated)
        # Only a basis that actually moved counts. Oracle's detail gives a
        # timestamp where its list gave a date, which changes `posted_at` while
        # leaving the basis `published`; that is a sharper number, not a better
        # kind of claim, and counting it here would overstate what this pass
        # bought.
        if "posted_at_basis" in patch and patch["posted_at_basis"] != row.posted_at_basis:
            result.basis_upgraded += 1
        patches.append((row.id, patch))

    if patches:
        # One UPDATE per row, keyed by primary key, carrying only the columns
        # that change. Columns absent from the payload are left alone, which is
        # what keeps `first_seen_at`, `content_hash` and `last_crawl_run_id`
        # intact through a hydration write. A statement per row is not the cost
        # here: this pass already made one HTTP request per row to get the body.
        for posting_id, patch in patches:
            await session.execute(
                update(JobPosting).where(JobPosting.id == posting_id).values(**patch)
            )
        await session.commit()
        result.rows_written = len(patches)

    result.requests_made = client.stats.requests
    result.bytes_fetched = client.stats.bytes_read
    result.duration_s = timing.stop()
    log.info("ingest.hydrate_done", **result.as_dict())
    return result


async def _candidate_rows(
    session: AsyncSession, providers: list[str] | None, *, pool_size: int
) -> list[_Candidate]:
    """The rows worth spending a request on, most recently seen first.

    Ordering is the whole design decision here, so: `last_seen_at DESC` makes
    this pass fill the window a search reads first rather than a random slice
    nobody will load. It is also the ordering least likely to waste a request: a
    row seen minutes ago is one a crawl just re-confirmed as still listed, where
    a row last seen weeks ago may well 404.

    The honest cost: because `last_seen_at` moves in whole sweeps, the front of
    this queue is usually one vendor. `--providers` is the lever for that.

    `canonical_id IS NULL` because a row already marked a duplicate is filtered
    out of every search, so hydrating it buys a body nobody will read.

    No index serves this exact predicate, so it is a scan and a sort. That is a
    deliberate omission rather than an oversight: see the note in
    `alembic/versions/20260831_0000_postings_back_to_postgres.py`. This runs in
    a scheduled pass with nobody waiting on it, and an index is storage in a
    change whose whole purpose was storage.
    """
    statement = (
        select(
            JobPosting.id,
            JobPosting.source,
            JobPosting.board_token,
            JobPosting.external_id,
            JobPosting.title,
            JobPosting.company_name,
            JobPosting.company_domain,
            JobPosting.location,
            JobPosting.source_url,
            JobPosting.jd_parsed,
            JobPosting.posted_at,
            JobPosting.posted_at_basis,
        )
        .where(
            JobPosting.active.is_(True),
            JobPosting.jd_hydrated.is_(False),
            JobPosting.canonical_id.is_(None),
        )
        .order_by(JobPosting.last_seen_at.desc())
        .limit(pool_size)
    )
    if providers:
        statement = statement.where(JobPosting.source.in_(providers))
    result = await session.execute(statement)
    return [
        _Candidate(
            id=row.id,
            source=row.source or "",
            board_token=row.board_token or "",
            external_id=row.external_id or "",
            title=row.title or "",
            company_name=row.company_name or "",
            company_domain=row.company_domain,
            location=row.location,
            source_url=row.source_url or "",
            jd_parsed=row.jd_parsed or {},
            posted_at=row.posted_at,
            posted_at_basis=row.posted_at_basis or "first_crawl",
        )
        for row in result.all()
    ]


def _hydrating_provider(source: str) -> SupportsHydrate | None:
    """The provider for a row's `source`, if it can hydrate. None otherwise."""
    if not source:
        return None
    try:
        provider = get_provider(source)
    except ValueError:
        # A `source` no provider claims. `scraper_import` writes whatever the
        # standalone scraper called the ATS, which is not required to be one of
        # `PROVIDER_NAMES`.
        return None
    return provider if isinstance(provider, SupportsHydrate) else None


async def _hydrate_one(
    fetcher: PoliteFetcher,
    provider: SupportsHydrate,
    posting: RawPosting,
) -> RawPosting | None:
    """One posting's detail request, or None if it did not produce a body.

    Every failure mode ends as None rather than an exception. `asyncio.gather`
    without `return_exceptions` would abandon the other 199 in-flight requests
    on the first provider that raised, and the bodies they already fetched
    would be thrown away unwritten -- so the catch is here, per posting, rather
    than around the gather.
    """
    try:
        hydrated = await provider.hydrate(fetcher, posting.board_token, posting)
    except Exception as exc:  # noqa: BLE001 - one bad row must not end the pass
        # Reachable with a well-behaved provider: `scraper_import` can file a
        # row under `source="workday"` with a board token the scraper invented,
        # and `workday.parse_token` raises `WorkdayTokenError` on anything that
        # is not `tenant:wdN:site` -- deliberately, since a malformed token
        # would otherwise address some other tenant's board.
        log.warning(
            "ingest.hydrate_failed",
            source=posting.source,
            token=posting.board_token,
            external_id=posting.external_id,
            error=f"{type(exc).__name__}: {exc}",
        )
        return None

    return hydrated if hydrated.jd_hydrated else None


def _success_patch(row: _Candidate, posting: RawPosting) -> dict[str, Any]:
    """The columns a hydrated posting is allowed to rewrite.

    Short on purpose, and the omissions are the interesting part.

    **`content_hash` is not here, and must not be.** It is the sweep's change
    detector: `upsert._write_batch` compares the hash it computes from the
    board's *list* payload against the stored one, and rewrites every mutable
    column when they differ. Rehashing the hydrated body would make that
    comparison fail on every future sweep, so each sweep would overwrite
    `jd_clean` with the thin list stand-in, reset `jd_hydrated` to false, and
    hand this pass the same row again -- a body that flickers in and out of the
    index and a request bill that never ends. Leaving it alone means the next
    sweep computes the same hash it stored, calls the row unchanged, and the
    body survives.

    **`updated_at` is not here either.** It means "the employer edited this".
    Learning what a posting always said is not an edit.

    **`title`, `location`, `company_name`, `external_id`, `source_url`, salary
    and `closes_at` are dropped**, even though several providers improve them
    (iCIMS's hydrate replaces a title guessed from a URL slug with the real
    one, which is a genuine loss to give up). They feed `source_id`,
    `dedupe_key` and `content_hash`, the row's identity and change-detection
    triad, and rewriting one of the three inputs without the other two leaves
    the row internally inconsistent in a way that only shows up as a phantom
    edit on some later sweep. Worth doing properly later; not worth doing
    halfway inside a pass whose job is descriptions.

    **`search_vector` is not here because it cannot be.** It is a STORED
    generated column over `jd_clean`, so writing the body below rewrites the
    index Postgres searches, and no code has the option of forgetting to.
    """
    patch: dict[str, Any] = {
        # `normalize.MAX_DESCRIPTION_CHARS` is the index's own bound on a body
        # ("so one pathological posting cannot dominate a row"). Applied here
        # because three of the five hydrators (Workday, BambooHR, Oracle) clean
        # HTML with their own local helper that does not go through
        # `normalize.html_to_text` and so never applies it.
        "jd_clean": normalize.truncate(posting.jd_clean, normalize.MAX_DESCRIPTION_CHARS),
        "jd_hydrated": True,
        "jd_parsed": posting.extra or {},
    }
    if posting.posted_at is not None and (
        posting.posted_at != row.posted_at or posting.posted_at_basis != row.posted_at_basis
    ):
        # The gain worth naming: a Workday row is `first_crawl` off the list
        # (its `postedOn` is prose -- "Posted 30+ Days Ago") and `published`
        # off the detail's `startDate`. Dropping this would keep the index
        # dating those postings to the day it happened to crawl them.
        # `posted_at_estimated` is not written: Postgres generates it from the
        # basis, so the two can never disagree. The Appwrite version had to set
        # it by hand, and could have got it wrong.
        patch["posted_at"] = posting.posted_at
        patch["posted_at_basis"] = posting.posted_at_basis
    return patch


def _failure_patch(row: _Candidate) -> dict[str, Any]:
    """Record one failed attempt on the row, and nothing else.

    Not a no-op: without it the newest-first ordering re-picks the same failing
    rows on every run, and a posting whose detail endpoint is permanently gone
    would absorb the budget forever while the rows behind it never get a turn.
    """
    extra = dict(row.jd_parsed)
    extra[_ATTEMPTS_KEY] = _attempts(row.jd_parsed) + 1
    extra["hydrate_last_attempt"] = datetime.now(UTC).isoformat()
    return {"jd_parsed": extra}


def _attempts(jd_parsed: dict[str, Any]) -> int:
    raw = jd_parsed.get(_ATTEMPTS_KEY)
    return raw if isinstance(raw, int) and raw > 0 else 0


def _row_to_posting(row: _Candidate) -> RawPosting:
    """A stored row back into the `RawPosting` a provider's `hydrate()` takes."""
    return RawPosting(
        source=row.source,
        board_token=row.board_token,
        external_id=row.external_id,
        title=row.title,
        company_name=row.company_name,
        company_domain=row.company_domain,
        source_url=row.source_url,
        # Never read by any provider's `hydrate()` -- all five replace the body
        # outright -- and not worth the megabytes of selecting it. See
        # `_Candidate`.
        jd_clean="",
        location=row.location,
        posted_at=row.posted_at,
        posted_at_basis=row.posted_at_basis,
        jd_hydrated=False,
        extra=dict(row.jd_parsed),
    )
