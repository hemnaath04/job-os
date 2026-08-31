"""The second pass: fetch the body a board's list endpoint did not carry.

    pick unhydrated rows, newest first -> one detail request each
                                       -> write back body, date, search text

Five of the eight providers write rows with `jd_hydrated=False` because their
list endpoint has no description in it at all: Workday, SmartRecruiters,
BambooHR, iCIMS and Oracle. Four of them already exposed a `hydrate()` that
turns one row into a real body, and nothing ever called it; SmartRecruiters
had none at all, so its rows carried a flag nothing could clear. Measured
against the live index on 2026-08-30, before this module: `descriptions_missing` was
5,000 of `postings_active` 5,000 -- both numbers are Appwrite's capped `total`
estimate rather than a true count, so read them as "the whole visible index",
not as exactly five thousand rows.

The cost of that gap is not that a posting looks thin. It is that
`job_index.search_index` matches on `search_text`, which for an unhydrated row
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
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

import structlog

from job_os.ingest import normalize
from job_os.ingest.fetcher import DEFAULT_CONCURRENCY, BoardTiming, PoliteFetcher
from job_os.ingest.providers import RawPosting, get_provider
from job_os.ingest.upsert import search_text_for
from job_os.services import appwrite_tables

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
#: `MAX_CANDIDATES`: it is the largest page this codebase asks Appwrite for,
#: and the same table answers it in well under a second when the select list is
#: narrow (measured 0.31s for 1,000 rows; the same query with no `select` at
#: all timed out at 8s per attempt, which is why `_CANDIDATE_COLUMNS` is short).
MAX_POOL = 2_000

#: How many times one row may fail hydration before the pass stops spending
#: requests on it. Without a ceiling the newest-first ordering would hand every
#: run the same dead rows forever, since a failure leaves `jd_hydrated=False`
#: and `last_seen_at` untouched, so the row keeps its place at the front of the
#: queue. Three because the fetcher already retries transient failures inside
#: one attempt (`MAX_ATTEMPTS`), so a row that fails three separate runs is
#: failing for a reason waiting will not fix.
MAX_ROW_ATTEMPTS = 3

#: Where that counter lives. `job_postings` has no column for it, and adding
#: one means a console-side schema change that cannot ship with this code, so
#: it goes in the provider `extra` blob under a namespaced key. Safe there:
#: nothing reads `job_postings.jd_parsed` (a promoted job gets a fresh LLM
#: parse -- see `job_index.promote_payload`), and a sweep that sees a genuine
#: content change rewrites the whole blob from the provider's own `extra`,
#: which resets the counter. That reset is correct rather than accidental: a
#: posting the employer just edited deserves a fresh try.
_ATTEMPTS_KEY = "hydrate_attempts"

#: Columns the pass needs to rebuild a `RawPosting` and address the row again.
#:
#: `jd_clean` is deliberately absent. No provider's `hydrate()` reads the old
#: body -- all five replace it outright -- and `job_index`'s own docstring
#: measured what selecting it costs: a pool carrying up to 8,000 characters of
#: `jd_clean` per row is around four megabytes for MariaDB to sort and ship,
#: and that is the shape that made Appwrite answer with its own 408.
_CANDIDATE_COLUMNS = [
    "$id",
    "source",
    "board_token",
    "external_id",
    "title",
    "company_name",
    "company_domain",
    "location",
    "source_url",
    "jd_parsed",
    "posted_at",
    "posted_at_basis",
]


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

    rows = await _candidate_rows(providers, pool_size=min(MAX_POOL, limit * POOL_MULTIPLIER))
    result.candidates_scanned = len(rows)

    work: list[tuple[dict[str, Any], SupportsHydrate, RawPosting]] = []
    for row in rows:
        if len(work) >= limit:
            break
        provider = _hydrating_provider(str(row.get("source") or ""))
        if provider is None:
            # Rows a source that cannot hydrate produced. Real, and not
            # hypothetical: `scraper_import` files rows under whatever string
            # the standalone scraper calls the ATS, and it sets
            # `jd_hydrated=False` whenever its export carried no description,
            # so the live index holds unhydrated rows under `greenhouse` -- a
            # provider with no `hydrate()` at all. Counted by source and
            # dropped, never re-queued.
            source = str(row.get("source") or "unknown")
            result.skipped_no_hydrate[source] = result.skipped_no_hydrate.get(source, 0) + 1
            continue
        if _attempts(row) >= MAX_ROW_ATTEMPTS:
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
                _hydrate_one(client, provider, row, posting)
                for row, provider, posting in work
            )
        )
    finally:
        if owns_fetcher:
            await client.aclose()

    patches: list[dict[str, Any]] = []
    for (row, _provider, _posting), patch in zip(work, outcomes, strict=True):
        result.attempted += 1
        source = str(row.get("source") or "unknown")
        if patch is None:
            result.failed += 1
            patches.append(_failure_patch(row))
            continue
        result.hydrated += 1
        result.hydrated_by_source[source] = result.hydrated_by_source.get(source, 0) + 1
        # Only a basis that actually moved counts. Oracle's detail gives a
        # timestamp where its list gave a date, which changes `posted_at` while
        # leaving the basis `published`; that is a sharper number, not a better
        # kind of claim, and counting it here would overstate what this pass
        # bought.
        if "posted_at_basis" in patch and patch["posted_at_basis"] != (
            row.get("posted_at_basis") or "first_crawl"
        ):
            result.basis_upgraded += 1
        patches.append(patch)

    if patches:
        # Same mechanism `upsert._write_batch` uses for an existing row: a
        # payload keyed by `$id` carrying only the columns that change, batched
        # by `appwrite_tables.upsert_rows`. Columns absent from the payload are
        # left alone, which is what keeps `first_seen_at` and `content_hash`
        # intact through a hydration write.
        await appwrite_tables.upsert_rows(patches)
        result.rows_written = len(patches)

    result.requests_made = client.stats.requests
    result.bytes_fetched = client.stats.bytes_read
    result.duration_s = timing.stop()
    log.info("ingest.hydrate_done", **result.as_dict())
    return result


async def _candidate_rows(
    providers: list[str] | None, *, pool_size: int
) -> list[dict[str, Any]]:
    """The rows worth spending a request on, most recently seen first.

    Ordering is the whole design decision here, so: `last_seen_at DESC` is the
    read path's own ORDER BY (`job_index.search_index` builds its candidate
    pool with exactly `sort_desc="last_seen_at"`), which makes this pass fill
    the window a search reads first rather than a random slice nobody will
    load. It is also the ordering least likely to waste a request: a row seen
    minutes ago is one a crawl just re-confirmed as still listed, where a row
    last seen weeks ago may well 404. And the table already carries the
    composite `active`+`canonical_id`+`last_seen_at DESC` index this shape
    needs (see `appwrite_tables.py`), so it is answered from an index rather
    than a sort.

    The honest cost: because `last_seen_at` moves in whole sweeps, the front of
    this queue is usually one vendor. `--providers` is the lever for that.

    `canonical_id IS NULL` because a row already marked a duplicate is filtered
    out of every search, so hydrating it buys a body nobody will read.
    """
    queries: list[dict[str, Any]] = [{"method": "isNull", "attribute": "canonical_id"}]
    if providers:
        queries.append({"method": "equal", "attribute": "source", "values": list(providers)})
    return await appwrite_tables.list_rows(
        filters=["active=true", "jd_hydrated=false"],
        queries=queries,
        select=_CANDIDATE_COLUMNS,
        sort_desc="last_seen_at",
        limit=pool_size,
    )


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
    row: dict[str, Any],
    posting: RawPosting,
) -> dict[str, Any] | None:
    """One posting's detail request, turned into a write payload or None.

    Every failure mode ends as None rather than an exception. `asyncio.gather`
    without `return_exceptions` would abandon the other 199 in-flight requests
    on the first provider that raised, and the bodies they already fetched
    would be thrown away unwritten -- so the catch is here, per posting, rather
    than around the gather.
    """
    before_posted_at = posting.posted_at
    before_basis = posting.posted_at_basis
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

    if not hydrated.jd_hydrated:
        return None
    return _success_patch(row, hydrated, before_posted_at, before_basis)


def _success_patch(
    row: dict[str, Any],
    posting: RawPosting,
    before_posted_at: datetime | None,
    before_basis: str,
) -> dict[str, Any]:
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

    **`content_updated_at` is not here either.** It means "the employer edited
    this". Learning what a posting always said is not an edit.

    **`title`, `location`, `company_name`, `external_id`, `source_url`, salary
    and `closes_at` are dropped**, even though several providers improve them
    (iCIMS's hydrate replaces a title guessed from a URL slug with the real
    one, which is a genuine loss to give up). They feed `source_id`,
    `dedupe_key` and `content_hash`, the row's identity and change-detection
    triad, and rewriting one of the three inputs without the other two leaves
    the row internally inconsistent in a way that only shows up as a phantom
    edit on some later sweep. Worth doing properly later; not worth doing
    halfway inside a pass whose job is descriptions.
    """
    patch: dict[str, Any] = {
        "$id": row["$id"],
        # `normalize.MAX_DESCRIPTION_CHARS` is the index's own bound on a body
        # ("so one pathological posting cannot dominate a row"). Applied here
        # because three of the five hydrators (Workday, BambooHR, Oracle) clean
        # HTML with their own local helper that does not go through
        # `normalize.html_to_text` and so never applies it.
        "jd_clean": normalize.truncate(posting.jd_clean, normalize.MAX_DESCRIPTION_CHARS),
        "jd_raw": posting.jd_raw or None,
        "jd_hydrated": True,
        "jd_parsed": json.dumps(posting.extra or {}),
    }
    patch["search_text"] = search_text_for(
        title=posting.title,
        company_name=posting.company_name,
        location=posting.location,
        jd_clean=patch["jd_clean"],
    )
    if posting.posted_at is not None and (
        posting.posted_at != before_posted_at or posting.posted_at_basis != before_basis
    ):
        # The gain worth naming: a Workday row is `first_crawl` off the list
        # (its `postedOn` is prose -- "Posted 30+ Days Ago") and `published`
        # off the detail's `startDate`. Dropping this would keep the index
        # dating those postings to the day it happened to crawl them.
        patch["posted_at"] = posting.posted_at.isoformat()
        patch["posted_at_basis"] = posting.posted_at_basis
        # Appwrite has no generated columns, so the flag Postgres derived from
        # the basis is derived here too, exactly as `upsert.to_row` does it.
        patch["posted_at_estimated"] = posting.posted_at_estimated
    return patch


def _failure_patch(row: dict[str, Any]) -> dict[str, Any]:
    """Record one failed attempt on the row, and nothing else.

    Not a no-op: without it the newest-first ordering re-picks the same failing
    rows on every run, and a posting whose detail endpoint is permanently gone
    would absorb the budget forever while the rows behind it never get a turn.
    """
    extra = _extra(row)
    extra[_ATTEMPTS_KEY] = _attempts(row) + 1
    extra["hydrate_last_attempt"] = datetime.now(UTC).isoformat()
    return {"$id": row["$id"], "jd_parsed": json.dumps(extra)}


def _extra(row: dict[str, Any]) -> dict[str, Any]:
    """A row's `jd_parsed` back into the dict a provider put there.

    Anything unparseable becomes an empty dict rather than an exception: this
    column is provider-shaped JSON written by several code paths (including the
    standalone scraper's import), and one malformed blob should cost that row
    its extras, not the run.
    """
    try:
        parsed = json.loads(str(row.get("jd_parsed") or "{}"))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _attempts(row: dict[str, Any]) -> int:
    raw = _extra(row).get(_ATTEMPTS_KEY)
    return raw if isinstance(raw, int) and raw > 0 else 0


def _row_to_posting(row: dict[str, Any]) -> RawPosting:
    """A stored row back into the `RawPosting` a provider's `hydrate()` takes.

    `.get(...) or default` throughout because Appwrite omits an empty-string
    column from a row payload entirely rather than returning `""` for it, so an
    absent key means "empty", not "corrupt" (the same trap `worker.dedupe_recent`
    documents for `jd_clean`).
    """
    return RawPosting(
        source=str(row.get("source") or ""),
        board_token=str(row.get("board_token") or ""),
        external_id=str(row.get("external_id") or ""),
        title=str(row.get("title") or ""),
        company_name=str(row.get("company_name") or ""),
        company_domain=str(row.get("company_domain") or "") or None,
        source_url=str(row.get("source_url") or ""),
        # Never read by any provider's `hydrate()` -- all five replace the body
        # outright -- and not worth the megabytes of selecting it. See
        # `_CANDIDATE_COLUMNS`.
        jd_clean="",
        location=str(row.get("location") or "") or None,
        posted_at=_parse_dt(row.get("posted_at")),
        posted_at_basis=str(row.get("posted_at_basis") or "first_crawl"),
        jd_hydrated=False,
        extra=_extra(row),
    )


def _parse_dt(value: object) -> datetime | None:
    """An Appwrite timestamp column back into a `datetime`, or None.

    Same job as `worker._parse_dt` and deliberately not imported from it:
    `worker` pulls in the whole sweep (liveness, dedupe, the ORM models), and
    this pass needs none of that to run.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
