"""The description hydration pass: what it writes, and what it must not.

This pass exists because five providers write rows with `jd_hydrated=False` and
nothing ever filled them in. It is an N+1 over someone else's API writing into
rows a sweep also writes, so almost every test here is about a way it could
quietly cost either requests or data:

  * Rewriting `content_hash` would make every later sweep see a phantom edit,
    overwrite the body it just paid for with the thin list stand-in, and hand
    the row straight back to this pass. A body that flickers, forever.
  * Writing `first_seen_at` or `last_crawl_run_id` would break the two things
    the sweep guarantees: honest freshness, and deactivating only what a board
    was genuinely re-read without.
  * Deactivating on a failed detail request would close live postings whenever
    a vendor was slow, since the failure this pass can see is "no body", not
    "404".
  * Skipping a source silently, rather than by name, turns a provider that can
    never hydrate into a run that reports success having done nothing.

The network is faked; the database is real. That split moved: while
`job_postings` lived in Appwrite these tests mocked the two TablesDB calls and
asserted on the write PAYLOADS, which is one step removed from the thing that
matters. Against Postgres they assert on the row afterwards, which catches a
class of mistake a payload assertion cannot -- most usefully that a hydrated
body actually becomes searchable, because `search_vector` is a generated column
and nothing in this module writes it.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from job_os.db.models.job_posting import JobPosting
from job_os.ingest import hydrate as hydrate_module
from job_os.ingest.fetcher import FetchResponse, FetchStats
from job_os.ingest.hydrate import MAX_ROW_ATTEMPTS, hydrate_descriptions
from job_os.ingest.providers import RawPosting
from job_os.ingest.upsert import upsert_postings
from job_os.services.job_index import IndexQuery, search_index

pytestmark = pytest.mark.asyncio

WD_TOKEN = "nvidia:wd5:NVIDIAExternalCareerSite"
NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


class FakeFetcher:
    """Serves canned responses in order and records the URLs asked for.

    Carries a real `FetchStats` because the pass reports request and byte
    counts off it, and a run that under-reported its own request count would
    hide exactly the cost this command is budgeted against.
    """

    def __init__(self, *responses: FetchResponse) -> None:
        self._responses = list(responses)
        self.urls: list[str] = []
        self.stats = FetchStats()

    async def get_json(
        self, url: str, *, host: str, etag: str | None = None, expect_bytes: int = 0
    ) -> FetchResponse:
        self.urls.append(url)
        self.stats.requests += 1
        if not self._responses:
            raise AssertionError(f"unexpected extra request to {url}")
        response = self._responses.pop(0)
        self.stats.bytes_read += response.bytes_read
        return response

    async def aclose(self) -> None:
        raise AssertionError("the pass must not close a fetcher it was handed")


def ok(payload: Any, *, size: int = 2048) -> FetchResponse:
    return FetchResponse(
        status_code=200, payload=payload, etag=None, bytes_read=size, requests_made=1
    )


def status(code: int) -> FetchResponse:
    return FetchResponse(
        status_code=code, payload=None, etag=None, bytes_read=64, requests_made=1
    )


#: A word that appears ONLY in the fetched description, never in the title,
#: company or location a list row already carries. Searching for it is how
#: `test_the_body_becomes_searchable_and_not_just_stored` can tell that
#: hydration changed the index rather than only the stored text. "compilers"
#: cannot do that job: the seeded title is "Senior Compiler Engineer" and
#: English stemming folds both to `compil`, so the row matches before the pass
#: has done anything.
BODY_ONLY_WORD = "raytracing"


def wd_detail(*, start_date: str = "2026-08-29") -> dict[str, Any]:
    """A Workday detail payload, shaped like the live one this was read from."""
    return {
        "jobPostingInfo": {
            "jobReqId": "JR1",
            "startDate": start_date,
            "jobDescription": (
                f"<p>Build <b>compilers</b> for {BODY_ONLY_WORD}.</p><li>C++</li>"
            ),
        }
    }


@pytest.fixture
def source() -> str:
    """A source namespace unique to one test.

    The index is a shared table, so an unscoped candidate query would pick up
    whatever else the database holds and hydrate it. Every seeded row here uses
    this, and the provider is resolved from it, so the value has to be a real
    provider name for the rows that are meant to be hydrated.
    """
    return f"hydr_{uuid.uuid4().hex[:12]}"


def wd_posting(
    *,
    external_id: str = "JR1",
    source: str = "workday",
    board_token: str = WD_TOKEN,
    external_path: str = "/job/loc/E_JR1",
    attempts: int | None = None,
) -> RawPosting:
    extra: dict[str, Any] = {"external_path": external_path}
    if attempts is not None:
        extra["hydrate_attempts"] = attempts
    return RawPosting(
        source=source,
        board_token=board_token,
        external_id=external_id,
        title="Senior Compiler Engineer",
        company_name="NVIDIA",
        company_domain="nvidia.com",
        location="US-CA-Santa Clara",
        source_url=f"https://nvidia.wd5.myworkdayjobs.com/site/job/loc/E_{external_id}",
        jd_clean="Senior Compiler Engineer\nNVIDIA\nUS-CA-Santa Clara",
        jd_hydrated=False,
        posted_at=None,
        posted_at_basis="first_crawl",
        extra=extra,
    )


async def seed(
    session: AsyncSession, *postings: RawPosting, seen_at: datetime | None = None
) -> None:
    await upsert_postings(session, list(postings), seen_at=seen_at or NOW)


async def row_for(session: AsyncSession, source: str, source_id: str) -> JobPosting:
    result = await session.execute(
        select(JobPosting)
        .where(JobPosting.source == source, JobPosting.source_id == source_id)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one()


async def only_row(session: AsyncSession, sources: list[str]) -> JobPosting:
    result = await session.execute(
        select(JobPosting)
        .where(JobPosting.source.in_(sources))
        .execution_options(populate_existing=True)
    )
    return result.scalars().one()


async def run(
    session: AsyncSession, fetcher: FakeFetcher, *, limit: int = 5, providers: list[str] | None = None
) -> hydrate_module.HydrateResult:
    return await hydrate_descriptions(
        session, fetcher=fetcher, limit=limit, providers=providers or ["workday"]
    )


async def test_hydration_never_rewrites_the_content_hash(db_session: AsyncSession) -> None:
    """The bug that would make this pass pay for the same posting forever.

    `upsert._write_batch` decides a posting was edited by comparing the hash it
    computes from the board's *list* payload against the stored `content_hash`.
    If hydration rehashed the body it just fetched, that comparison would fail
    on every subsequent sweep: the sweep would take the "changed" branch,
    overwrite `jd_clean` with the thin list stand-in, reset `jd_hydrated` to
    false, and put the row back at the front of this pass's queue. The body
    would flicker in and out of the index and the request bill would never end.
    """
    await seed(db_session, wd_posting())
    before = await only_row(db_session, ["workday"])
    hash_before, key_before, updated_before = (
        before.content_hash,
        before.dedupe_key,
        before.updated_at,
    )

    result = await run(db_session, FakeFetcher(ok(wd_detail())))

    assert result.hydrated == 1
    after = await only_row(db_session, ["workday"])
    assert after.content_hash == hash_before
    assert after.dedupe_key == key_before
    # For the same reason: this is not the employer editing the posting, it is
    # us finally reading what it always said.
    assert after.updated_at == updated_before


async def test_a_hydrated_row_keeps_its_first_sighting_and_its_crawl_run(
    db_session: AsyncSession,
) -> None:
    """Two columns hydration must never touch, for two different reasons.

    `first_seen_at` is the whole honest-freshness claim; a pass that reset it
    would turn "first seen three weeks ago" into a lie on every row it touched.
    `last_crawl_run_id` is what `deactivate_missing` uses to tell "the board
    still lists this" from "it did not come back this run" -- stamping this
    run's id on a row would make the next sweep treat a posting it never saw as
    one it did, and closed postings would stay active indefinitely.
    """
    from job_os.db.models.ingest import CrawlRun

    crawl = CrawlRun(status="running", providers=["workday"])
    db_session.add(crawl)
    await db_session.flush()
    first_seen = NOW - timedelta(days=21)
    await upsert_postings(
        db_session, [wd_posting()], run_id=crawl.id, seen_at=first_seen
    )

    await run(db_session, FakeFetcher(ok(wd_detail())))

    after = await only_row(db_session, ["workday"])
    assert after.first_seen_at == first_seen
    assert after.last_seen_at == first_seen
    assert after.last_crawl_run_id == crawl.id
    assert after.active is True


async def test_hydration_upgrades_the_posted_date_to_the_employers_own(
    db_session: AsyncSession,
) -> None:
    """The gain worth making the extra request for, besides the body.

    A Workday list row is honestly `first_crawl`: its `postedOn` is prose
    ("Posted 30+ Days Ago") with no date in it. The detail's `startDate` is a
    real one. Dropping this would leave the index dating every Workday posting
    to the day it happened to be crawled, and `posted_at_estimated` would keep
    claiming that guess was as good as a published date.
    """
    await seed(db_session, wd_posting())

    result = await run(db_session, FakeFetcher(ok(wd_detail())))

    assert result.basis_upgraded == 1
    after = await only_row(db_session, ["workday"])
    assert after.posted_at is not None
    assert after.posted_at.date().isoformat() == "2026-08-29"
    assert after.posted_at_basis == "published"
    # A generated column now, so it cannot be forgotten. Under Appwrite this
    # had to be written by hand alongside the basis, and a row left behind
    # would have read as an estimate forever.
    assert after.posted_at_estimated is False


async def test_the_body_becomes_searchable_and_not_just_stored(
    db_session: AsyncSession,
) -> None:
    """The point of the whole pass, asserted end to end.

    Before hydration the row carries a title, a company and a location, so a
    search for a word from the description finds nothing. Afterwards it does,
    because `search_vector` is a STORED generated column over `jd_clean` and
    writing the body rewrites the index by construction. Under Appwrite this
    was a second stored column (`search_text`) that this module had to rebuild
    byte-for-byte, and a pass that stored the body and forgot it would have
    spent one request per posting to make no difference to a single query.
    """
    await seed(db_session, wd_posting())

    query = IndexQuery(sources=["workday"], query=BODY_ONLY_WORD)
    before = await search_index(db_session, query)
    await run(db_session, FakeFetcher(ok(wd_detail())))
    after = await search_index(db_session, query)

    assert before.hits == []
    assert [hit.source_id for hit in after.hits] == [f"{WD_TOKEN}:JR1"]
    row = await only_row(db_session, ["workday"])
    assert "compilers" in row.jd_clean
    # The markup is stripped and then dropped, not archived. `jd_raw` used to
    # hold the vendor's own HTML and was read by nothing: 5,581 compressed
    # bytes a row, the largest column in the table. Providers still build
    # `jd_clean` out of it in memory, which is the only thing it was ever for.
    assert "<p>" not in row.jd_clean, "the markup is stripped, not stored"
    assert not hasattr(row, "jd_raw"), "the column is gone, deliberately"
    assert row.jd_hydrated is True


async def test_the_candidate_query_asks_for_the_rows_a_search_reads_first(
    db_session: AsyncSession,
) -> None:
    """The ordering decision, pinned as behaviour rather than as a query shape.

    `last_seen_at DESC` is `job_index.search_index`'s own tie-break, so this
    fills the window a search reaches first. Ordering by anything else would
    hydrate rows nobody loads. With a budget of one request and two rows to
    choose from, the newest has to be the one that gets it.
    """
    await seed(db_session, wd_posting(external_id="OLD"), seen_at=NOW - timedelta(days=30))
    await seed(db_session, wd_posting(external_id="NEW"), seen_at=NOW)

    await run(db_session, FakeFetcher(ok(wd_detail())), limit=1)

    assert (await row_for(db_session, "workday", f"{WD_TOKEN}:NEW")).jd_hydrated is True
    assert (await row_for(db_session, "workday", f"{WD_TOKEN}:OLD")).jd_hydrated is False


async def test_an_inactive_or_duplicate_row_is_never_hydrated(
    db_session: AsyncSession,
) -> None:
    """Both guards, and both cost real requests if dropped.

    A row already merged into a duplicate is filtered out of every search, so
    hydrating it buys a body nobody will read. A closed posting is worse: the
    request is spent on something that is very likely 404 anyway.
    """
    from job_os.db.models.ingest import CrawlRun
    from job_os.ingest.upsert import deactivate_missing, mark_duplicates

    crawl = CrawlRun(status="running", providers=["workday"])
    other = CrawlRun(status="running", providers=["workday"])
    db_session.add_all([crawl, other])
    await db_session.flush()

    await upsert_postings(
        db_session,
        [wd_posting(external_id="CLOSED", board_token="closed:wd5:site")],
        run_id=crawl.id,
        seen_at=NOW,
    )
    await deactivate_missing(
        db_session, source="workday", board_token="closed:wd5:site", run_id=other.id
    )
    await seed(db_session, wd_posting(external_id="DUPE"), wd_posting(external_id="KEEP"))
    dupe = await row_for(db_session, "workday", f"{WD_TOKEN}:DUPE")
    keep = await row_for(db_session, "workday", f"{WD_TOKEN}:KEEP")
    await mark_duplicates(db_session, [(dupe.id, keep.id, "exact_key", None)])

    fetcher = FakeFetcher(ok(wd_detail()))
    await run(db_session, fetcher, limit=5)

    assert len(fetcher.urls) == 1, "two of the three rows must not cost a request"
    assert (await row_for(db_session, "workday", f"{WD_TOKEN}:KEEP")).jd_hydrated is True
    assert (await row_for(db_session, "workday", f"{WD_TOKEN}:DUPE")).jd_hydrated is False
    assert (
        await row_for(db_session, "workday", "closed:wd5:site:CLOSED")
    ).jd_hydrated is False


async def test_a_provider_that_cannot_hydrate_is_skipped_by_name(
    db_session: AsyncSession, source: str
) -> None:
    """Greenhouse has no `hydrate()`, and the live index holds unhydrated
    greenhouse rows anyway -- `scraper_import` files rows under whatever the
    standalone scraper called the ATS and clears the flag when its export
    carried no description.

    Skipping them silently would leave a run reporting a clean pass having done
    nothing, forever, with no way to see why. Worse, attempting them would
    raise `AttributeError` once per row.
    """
    await seed(
        db_session,
        wd_posting(source="greenhouse", board_token="trace3", external_id="GH1"),
        wd_posting(),
    )
    fetcher = FakeFetcher(ok(wd_detail()))

    result = await run(db_session, fetcher, providers=["workday", "greenhouse"])

    assert result.skipped_no_hydrate == {"greenhouse": 1}
    assert result.hydrated == 1
    assert len(fetcher.urls) == 1, "the greenhouse row must not cost a request"
    assert (await row_for(db_session, "greenhouse", "trace3:GH1")).jd_hydrated is False


async def test_a_source_no_provider_claims_does_not_crash_the_pass(
    db_session: AsyncSession, source: str
) -> None:
    """`scraper_import` writes `source=row["ats"]`, an arbitrary string from a
    separate service, so the index can hold a source `get_provider` has never
    heard of. Letting that `ValueError` out would kill the whole run on one
    row that a different codebase chose the name of.
    """
    await seed(db_session, wd_posting(source=source))

    result = await run(db_session, FakeFetcher(), providers=[source])

    assert result.skipped_no_hydrate == {source: 1}
    assert result.attempted == 0
    assert result.rows_written == 0


async def test_one_posting_that_raises_does_not_take_the_run_down(
    db_session: AsyncSession,
) -> None:
    """A provider raising mid-pass must cost that posting, not the batch.

    Reachable without any provider bug: the scraper can file a row under
    `source="workday"` with a board token it invented, and `parse_token` raises
    on anything that is not `tenant:wdN:site` precisely so a malformed token
    cannot address some other tenant's board. Without the per-posting catch,
    `asyncio.gather` would abandon every other in-flight request and throw away
    bodies already fetched and paid for.
    """
    await seed(
        db_session,
        wd_posting(external_id="BAD", board_token="not-a-workday-token"),
        wd_posting(),
    )

    result = await run(db_session, FakeFetcher(ok(wd_detail())))

    assert result.attempted == 2
    assert result.hydrated == 1
    assert result.failed == 1
    assert (await row_for(db_session, "workday", f"{WD_TOKEN}:JR1")).jd_hydrated is True


async def test_a_failed_hydrate_is_recorded_and_never_deactivates(
    db_session: AsyncSession,
) -> None:
    """The deactivation question, decided in a test.

    Every provider's `hydrate()` swallows a bad response and returns the row
    unchanged, so a 404, a timeout and a 429 that outlived its retries all
    arrive here as the same "no body". Deactivating on that would close live
    postings because a vendor was slow -- the exact mistake `BoardStatus` and
    `deactivate_missing` exist to prevent. The list crawl already closes real
    closures, from a board it actually re-read. So the failure is recorded on
    the row and nothing else changes.
    """
    await seed(db_session, wd_posting())

    result = await run(db_session, FakeFetcher(status(404)))

    assert result.failed == 1
    assert result.hydrated == 0
    after = await only_row(db_session, ["workday"])
    assert after.active is True
    assert after.inactive_since is None
    assert after.jd_hydrated is False, "a failure must not claim the row was filled"
    assert after.jd_parsed["hydrate_attempts"] == 1
    # The provider's own payload survives the counter being written beside it;
    # losing it would cost Workday its `external_path` and make the row
    # permanently unhydratable.
    assert after.jd_parsed["external_path"] == "/job/loc/E_JR1"


async def test_a_row_that_has_failed_enough_stops_being_asked(
    db_session: AsyncSession,
) -> None:
    """Without a ceiling this queue never moves past its dead rows.

    A failure leaves `jd_hydrated=False` and does not touch `last_seen_at`, so
    a newest-first ordering hands the same rows back on every single run. A
    posting whose detail endpoint is permanently gone would absorb the budget
    forever while the rows behind it never got a turn.
    """
    await seed(
        db_session,
        wd_posting(external_id="WORN", attempts=MAX_ROW_ATTEMPTS),
        wd_posting(),
    )
    fetcher = FakeFetcher(ok(wd_detail()))

    result = await run(db_session, fetcher)

    assert result.skipped_exhausted == 1
    assert len(fetcher.urls) == 1
    assert (await row_for(db_session, "workday", f"{WD_TOKEN}:WORN")).jd_hydrated is False
    assert (await row_for(db_session, "workday", f"{WD_TOKEN}:JR1")).jd_hydrated is True


async def test_the_limit_bounds_requests_not_just_the_read(
    db_session: AsyncSession,
) -> None:
    """`--limit` is the request budget, and this is an N+1 by nature.

    The candidate pool is read wider than the limit on purpose, so a page full
    of unhydratable rows does not turn into a run that does nothing. If the
    limit were applied to the read instead of to the work, that widening would
    silently multiply the number of vendor requests a run makes by
    `POOL_MULTIPLIER`.
    """
    await seed(db_session, *[wd_posting(external_id=f"JR{i}") for i in range(20)])
    fetcher = FakeFetcher(*[ok(wd_detail()) for _ in range(3)])

    result = await run(db_session, fetcher, limit=3)

    assert result.attempted == 3
    assert len(fetcher.urls) == 3
    # Wider than the limit, and bounded by it: 3 * POOL_MULTIPLIER of the 20
    # available rows were read, not 3 and not 20.
    assert result.candidates_scanned == 3 * hydrate_module.POOL_MULTIPLIER


async def test_a_run_with_nothing_to_do_makes_no_requests_and_no_writes(
    db_session: AsyncSession, source: str
) -> None:
    """The success case once the index is caught up.

    An empty pool must not open a fetcher, write an empty batch, or look like a
    failure -- a scheduler reading this output should see a finished index, not
    an alarm.
    """
    result = await run(db_session, FakeFetcher(), providers=[source])

    assert result.candidates_scanned == 0
    assert result.attempted == 0
    assert result.rows_written == 0
    assert result.as_dict()["hydrated"] == 0
