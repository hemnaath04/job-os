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

Appwrite and the network are both faked here. The write payloads are the
contract, so the tests assert on those.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from job_os.ingest import hydrate as hydrate_module
from job_os.ingest.fetcher import FetchResponse, FetchStats
from job_os.ingest.hydrate import MAX_ROW_ATTEMPTS, hydrate_descriptions
from job_os.services import appwrite_tables

pytestmark = pytest.mark.asyncio

WD_TOKEN = "nvidia:wd5:NVIDIAExternalCareerSite"


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


def wd_detail(*, start_date: str = "2026-08-29") -> dict[str, Any]:
    """A Workday detail payload, shaped like the live one this was read from."""
    return {
        "jobPostingInfo": {
            "jobReqId": "JR1",
            "startDate": start_date,
            "jobDescription": "<p>Build <b>compilers</b>.</p><li>C++</li>",
        }
    }


def wd_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "$id": "appwrite-row-1",
        "source": "workday",
        "board_token": WD_TOKEN,
        "external_id": "JR1",
        "title": "Senior Compiler Engineer",
        "company_name": "NVIDIA",
        "company_domain": "nvidia.com",
        "location": "US-CA-Santa Clara",
        "source_url": "https://nvidia.wd5.myworkdayjobs.com/site/job/loc/E_JR1",
        "jd_parsed": json.dumps({"external_path": "/job/loc/E_JR1"}),
        "posted_at": None,
        "posted_at_basis": "first_crawl",
    }
    row.update(overrides)
    return row


@pytest.fixture
def appwrite(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Stand in for the two TablesDB calls the pass makes.

    Records what was asked for and what would have been written, so a test can
    assert on the query shape (the ordering decision) and on the patch shape
    (everything the pass must not touch).
    """
    state: dict[str, Any] = {"rows": [], "list_kwargs": {}, "written": []}

    async def fake_list_rows(**kwargs: Any) -> list[dict[str, Any]]:
        state["list_kwargs"] = kwargs
        return list(state["rows"])[: kwargs.get("limit")]

    async def fake_upsert_rows(rows: list[dict[str, Any]]) -> None:
        state["written"].extend(rows)

    monkeypatch.setattr(appwrite_tables, "list_rows", fake_list_rows)
    monkeypatch.setattr(appwrite_tables, "upsert_rows", fake_upsert_rows)
    return state


async def test_hydration_never_rewrites_the_content_hash(
    appwrite: dict[str, Any],
) -> None:
    """The bug that would make this pass pay for the same posting forever.

    `upsert._write_batch` decides a posting was edited by comparing the hash it
    computes from the board's *list* payload against the stored `content_hash`.
    If hydration rehashed the body it just fetched, that comparison would fail
    on every subsequent sweep: the sweep would take the "changed" branch,
    overwrite `jd_clean` with the thin list stand-in, reset `jd_hydrated` to
    false, and put the row back at the front of this pass's queue. The body
    would flicker in and out of the index and the request bill would never end.
    """
    appwrite["rows"] = [wd_row()]

    result = await hydrate_descriptions(fetcher=FakeFetcher(ok(wd_detail())), limit=5)

    assert result.hydrated == 1
    patch = appwrite["written"][0]
    assert "content_hash" not in patch
    assert "dedupe_key" not in patch
    # For the same reason: this is not the employer editing the posting, it is
    # us finally reading what it always said.
    assert "content_updated_at" not in patch


async def test_a_hydrated_row_keeps_its_first_sighting_and_its_crawl_run(
    appwrite: dict[str, Any],
) -> None:
    """Two columns hydration must never touch, for two different reasons.

    `first_seen_at` is the whole honest-freshness claim; a pass that reset it
    would turn "first seen three weeks ago" into a lie on every row it touched.
    `last_crawl_run_id` is what `deactivate_missing` uses to tell "the board
    still lists this" from "it did not come back this run" -- stamping this
    run's id on a row would make the next sweep treat a posting it never saw as
    one it did, and closed postings would stay active indefinitely.
    """
    appwrite["rows"] = [wd_row()]

    await hydrate_descriptions(fetcher=FakeFetcher(ok(wd_detail())), limit=5)

    patch = appwrite["written"][0]
    assert patch["$id"] == "appwrite-row-1"
    for column in ("first_seen_at", "last_seen_at", "last_crawl_run_id", "active"):
        assert column not in patch, f"hydration must not write {column}"


async def test_hydration_upgrades_the_posted_date_to_the_employers_own(
    appwrite: dict[str, Any],
) -> None:
    """The gain worth making the extra request for, besides the body.

    A Workday list row is honestly `first_crawl`: its `postedOn` is prose
    ("Posted 30+ Days Ago") with no date in it. The detail's `startDate` is a
    real one. Dropping this would leave the index dating every Workday posting
    to the day it happened to be crawled, and `posted_at_estimated` would keep
    claiming that guess was as good as a published date.
    """
    appwrite["rows"] = [wd_row()]

    result = await hydrate_descriptions(fetcher=FakeFetcher(ok(wd_detail())), limit=5)

    assert result.basis_upgraded == 1
    patch = appwrite["written"][0]
    assert patch["posted_at"].startswith("2026-08-29")
    assert patch["posted_at_basis"] == "published"
    # Appwrite has no generated columns, so this has to move with the basis by
    # hand. Left behind, the row would read as an estimate forever.
    assert patch["posted_at_estimated"] is False


async def test_the_body_reaches_the_column_a_search_actually_matches_on(
    appwrite: dict[str, Any],
) -> None:
    """`jd_clean` alone would leave the whole pass invisible to search.

    `job_index.search_index` matches Appwrite's one fulltext index, which is
    over `search_text`, not over `jd_clean`. A pass that stored the description
    and forgot to rebuild `search_text` would spend one request per posting to
    make no difference to a single query.
    """
    appwrite["rows"] = [wd_row()]

    await hydrate_descriptions(fetcher=FakeFetcher(ok(wd_detail())), limit=5)

    patch = appwrite["written"][0]
    assert "compilers" in patch["jd_clean"]
    assert "<p>" not in patch["jd_clean"], "the markup belongs in jd_raw"
    assert "<p>" in patch["jd_raw"]
    assert "compilers" in patch["search_text"]
    # Same join the write path uses, so two rows from one board cannot end up
    # matching different queries for no visible reason.
    assert patch["search_text"].startswith("Senior Compiler Engineer NVIDIA US-CA-Santa Clara")


async def test_the_candidate_query_asks_for_the_rows_a_search_reads_first(
    appwrite: dict[str, Any],
) -> None:
    """The ordering decision, pinned.

    `last_seen_at DESC` is `job_index.search_index`'s own ORDER BY, so this
    fills the window a search reaches first. Ordering by anything else would
    hydrate rows nobody loads. Dropping the `canonical_id IS NULL` guard would
    spend the budget on rows already merged into a duplicate and filtered out
    of every result set. Reading rows that are not `active` would buy bodies
    for postings that already closed.
    """
    appwrite["rows"] = [wd_row()]

    await hydrate_descriptions(fetcher=FakeFetcher(ok(wd_detail())), limit=5)

    kwargs = appwrite["list_kwargs"]
    assert kwargs["sort_desc"] == "last_seen_at"
    assert set(kwargs["filters"]) == {"active=true", "jd_hydrated=false"}
    assert {"method": "isNull", "attribute": "canonical_id"} in kwargs["queries"]
    # A narrow select is not a micro-optimisation here: the same query with no
    # select at all timed out against the live table (see `_CANDIDATE_COLUMNS`).
    assert "jd_clean" not in kwargs["select"]


async def test_a_provider_that_cannot_hydrate_is_skipped_by_name(
    appwrite: dict[str, Any],
) -> None:
    """Greenhouse has no `hydrate()`, and the live index holds unhydrated
    greenhouse rows anyway -- `scraper_import` files rows under whatever the
    standalone scraper called the ATS and clears the flag when its export
    carried no description.

    Skipping them silently would leave a run reporting a clean pass having done
    nothing, forever, with no way to see why. Worse, attempting them would
    raise `AttributeError` once per row.
    """
    appwrite["rows"] = [
        {**wd_row(), "$id": "gh-1", "source": "greenhouse", "board_token": "trace3"},
        wd_row(),
    ]
    fetcher = FakeFetcher(ok(wd_detail()))

    result = await hydrate_descriptions(fetcher=fetcher, limit=5)

    assert result.skipped_no_hydrate == {"greenhouse": 1}
    assert result.hydrated == 1
    assert len(fetcher.urls) == 1, "the greenhouse row must not cost a request"
    assert [p["$id"] for p in appwrite["written"]] == ["appwrite-row-1"]


async def test_a_source_no_provider_claims_does_not_crash_the_pass(
    appwrite: dict[str, Any],
) -> None:
    """`scraper_import` writes `source=row["ats"]`, an arbitrary string from a
    separate service, so the index can hold a source `get_provider` has never
    heard of. Letting that `ValueError` out would kill the whole run on one
    row that a different codebase chose the name of.
    """
    appwrite["rows"] = [{**wd_row(), "$id": "x-1", "source": "some-other-ats"}]

    result = await hydrate_descriptions(fetcher=FakeFetcher(), limit=5)

    assert result.skipped_no_hydrate == {"some-other-ats": 1}
    assert result.attempted == 0
    assert appwrite["written"] == []


async def test_one_posting_that_raises_does_not_take_the_run_down(
    appwrite: dict[str, Any],
) -> None:
    """A provider raising mid-pass must cost that posting, not the batch.

    Reachable without any provider bug: the scraper can file a row under
    `source="workday"` with a board token it invented, and `parse_token` raises
    on anything that is not `tenant:wdN:site` precisely so a malformed token
    cannot address some other tenant's board. Without the per-posting catch,
    `asyncio.gather` would abandon every other in-flight request and throw away
    bodies already fetched and paid for.
    """
    appwrite["rows"] = [
        {**wd_row(), "$id": "bad-token", "board_token": "not-a-workday-token"},
        wd_row(),
    ]

    result = await hydrate_descriptions(fetcher=FakeFetcher(ok(wd_detail())), limit=5)

    assert result.attempted == 2
    assert result.hydrated == 1
    assert result.failed == 1
    written = {p["$id"]: p for p in appwrite["written"]}
    assert written["appwrite-row-1"]["jd_hydrated"] is True


async def test_a_failed_hydrate_is_recorded_and_never_deactivates(
    appwrite: dict[str, Any],
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
    appwrite["rows"] = [wd_row()]

    result = await hydrate_descriptions(fetcher=FakeFetcher(status(404)), limit=5)

    assert result.failed == 1
    assert result.hydrated == 0
    patch = appwrite["written"][0]
    assert "active" not in patch
    assert "inactive_since" not in patch
    assert "jd_hydrated" not in patch, "a failure must not claim the row was filled"
    assert json.loads(patch["jd_parsed"])["hydrate_attempts"] == 1
    # The provider's own payload survives the counter being written beside it;
    # losing it would cost Workday its `external_path` and make the row
    # permanently unhydratable.
    assert json.loads(patch["jd_parsed"])["external_path"] == "/job/loc/E_JR1"


async def test_a_row_that_has_failed_enough_stops_being_asked(
    appwrite: dict[str, Any],
) -> None:
    """Without a ceiling this queue never moves past its dead rows.

    A failure leaves `jd_hydrated=False` and does not touch `last_seen_at`, so
    a newest-first ordering hands the same rows back on every single run. A
    posting whose detail endpoint is permanently gone would absorb the budget
    forever while the rows behind it never got a turn.
    """
    exhausted = wd_row(
        **{
            "$id": "worn-out",
            "jd_parsed": json.dumps(
                {"external_path": "/job/loc/E_JR9", "hydrate_attempts": MAX_ROW_ATTEMPTS}
            ),
        }
    )
    appwrite["rows"] = [exhausted, wd_row()]
    fetcher = FakeFetcher(ok(wd_detail()))

    result = await hydrate_descriptions(fetcher=fetcher, limit=5)

    assert result.skipped_exhausted == 1
    assert len(fetcher.urls) == 1
    assert [p["$id"] for p in appwrite["written"]] == ["appwrite-row-1"]


async def test_the_limit_bounds_requests_not_just_the_read(
    appwrite: dict[str, Any],
) -> None:
    """`--limit` is the request budget, and this is an N+1 by nature.

    The candidate pool is read wider than the limit on purpose, so a page full
    of unhydratable rows does not turn into a run that does nothing. If the
    limit were applied to the read instead of to the work, that widening would
    silently multiply the number of vendor requests a run makes by
    `POOL_MULTIPLIER`.
    """
    appwrite["rows"] = [wd_row(**{"$id": f"row-{i}"}) for i in range(10)]
    fetcher = FakeFetcher(*[ok(wd_detail()) for _ in range(3)])

    result = await hydrate_descriptions(fetcher=fetcher, limit=3)

    assert result.candidates_scanned == 10
    assert result.attempted == 3
    assert len(fetcher.urls) == 3
    assert appwrite["list_kwargs"]["limit"] == 3 * hydrate_module.POOL_MULTIPLIER


async def test_a_run_with_nothing_to_do_makes_no_requests_and_no_writes(
    appwrite: dict[str, Any],
) -> None:
    """The success case once the index is caught up.

    An empty pool must not open a fetcher, write an empty batch, or look like a
    failure -- a scheduler reading this output should see a finished index, not
    an alarm.
    """
    appwrite["rows"] = []

    result = await hydrate_descriptions(fetcher=FakeFetcher(), limit=5)

    assert result.attempted == 0
    assert result.rows_written == 0
    assert appwrite["written"] == []
    assert result.as_dict()["hydrated"] == 0
