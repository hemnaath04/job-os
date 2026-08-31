"""`_request`'s retry on Appwrite's own 408 "Database timed out."

Seen in production on `job_postings` search under load: four requests failed
this way inside ninety seconds, then an equivalent request later succeeded in
nine -- borderline, not a structurally missing index (confirmed against the
table's real indexes), which is exactly the shape retries are for. A single
retry (this file's first version) still wasn't enough against a later,
slower occurrence, hence two. A non-408 error, and 408s that recur through
every attempt, must still surface rather than loop forever or swallow the
real failure.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest

from job_os.services import appwrite_tables


@dataclass
class _FakeSettings:
    appwrite_api_key: str | None = "key"
    appwrite_job_postings_table_id: str = "job_postings"
    appwrite_endpoint: str = "https://appwrite.test/v1"
    appwrite_database_id: str = "job-os"
    appwrite_project_id: str = "proj"


class _FakeResponse:
    def __init__(self, status_code: int, body: dict[str, Any]) -> None:
        self.status_code = status_code
        self._body = body
        self.content = b"1" if body else b""
        self.text = str(body)

    def json(self) -> dict[str, Any]:
        return self._body


class _FakeHTTPClient:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls = 0

    async def __aenter__(self) -> _FakeHTTPClient:
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False

    async def request(self, *_args: Any, **_kwargs: Any) -> _FakeResponse:
        self.calls += 1
        return self._responses.pop(0)


@pytest.mark.asyncio
async def test_a_single_408_is_retried_and_the_retry_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(appwrite_tables, "get_settings", lambda: _FakeSettings())
    sleeps: list[float] = []

    async def _instant_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(appwrite_tables, "_sleep", _instant_sleep)
    fake_client = _FakeHTTPClient(
        [
            _FakeResponse(408, {"message": "Database timed out."}),
            _FakeResponse(200, {"rows": [{"id": "1"}]}),
        ]
    )
    monkeypatch.setattr(appwrite_tables.httpx, "AsyncClient", lambda **_kw: fake_client)

    rows = await appwrite_tables.list_rows(filters=["active=true"])

    assert rows == [{"id": "1"}]
    assert fake_client.calls == 2
    assert sleeps == [appwrite_tables._TIMEOUT_RETRY_DELAY_SECONDS]


@pytest.mark.asyncio
async def test_two_consecutive_408s_then_the_second_retry_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(appwrite_tables, "get_settings", lambda: _FakeSettings())
    sleeps: list[float] = []

    async def _instant_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(appwrite_tables, "_sleep", _instant_sleep)
    fake_client = _FakeHTTPClient(
        [
            _FakeResponse(408, {"message": "Database timed out."}),
            _FakeResponse(408, {"message": "Database timed out."}),
            _FakeResponse(200, {"rows": [{"id": "1"}]}),
        ]
    )
    monkeypatch.setattr(appwrite_tables.httpx, "AsyncClient", lambda **_kw: fake_client)

    rows = await appwrite_tables.list_rows(filters=["active=true"])

    assert rows == [{"id": "1"}]
    assert fake_client.calls == 3
    assert sleeps == [
        appwrite_tables._TIMEOUT_RETRY_DELAY_SECONDS,
        appwrite_tables._TIMEOUT_RETRY_DELAY_SECONDS,
    ]


@pytest.mark.asyncio
async def test_three_consecutive_408s_surface_as_the_real_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(appwrite_tables, "get_settings", lambda: _FakeSettings())

    async def _instant_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(appwrite_tables, "_sleep", _instant_sleep)
    fake_client = _FakeHTTPClient(
        [
            _FakeResponse(408, {"message": "Database timed out."}),
            _FakeResponse(408, {"message": "Database timed out."}),
            _FakeResponse(408, {"message": "Database timed out."}),
        ]
    )
    monkeypatch.setattr(appwrite_tables.httpx, "AsyncClient", lambda **_kw: fake_client)

    with pytest.raises(appwrite_tables.AppwriteTablesError) as excinfo:
        await appwrite_tables.list_rows(filters=["active=true"])

    assert excinfo.value.status_code == 408
    assert fake_client.calls == 3


@pytest.mark.asyncio
async def test_a_non_timeout_error_is_never_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(appwrite_tables, "get_settings", lambda: _FakeSettings())

    async def _fail_if_slept(_seconds: float) -> None:
        raise AssertionError("a non-408 error must not trigger a retry wait")

    monkeypatch.setattr(appwrite_tables, "_sleep", _fail_if_slept)
    fake_client = _FakeHTTPClient([_FakeResponse(500, {"message": "server error"})])
    monkeypatch.setattr(appwrite_tables.httpx, "AsyncClient", lambda **_kw: fake_client)

    with pytest.raises(appwrite_tables.AppwriteTablesError) as excinfo:
        await appwrite_tables.list_rows(filters=["active=true"])

    assert excinfo.value.status_code == 500
    assert fake_client.calls == 1


# ---------------------------------------------------------------------------
# The read budget
#
# The retry ladder above reads correctly and, on the search path, could not
# work: three attempts of up to 30s plus two 1s waits is 92 seconds inside a
# request Heroku's router abandons at 30. The 408 never surfaced -- the router
# returned an H12 503 first, and the web app rendered that as "the saved index
# was restarting", for something that was neither a restart nor finished. So a
# read now bounds the whole ladder, and a write, which nothing is waiting on,
# does not.
# ---------------------------------------------------------------------------


class _ClockedClient(_FakeHTTPClient):
    """A client that also spends time, so the budget has something to run out of."""

    def __init__(self, responses: list[_FakeResponse], seconds_per_call: float) -> None:
        super().__init__(responses)
        self._seconds_per_call = seconds_per_call
        self.timeouts: list[float] = []

    async def request(self, *args: Any, **kwargs: Any) -> _FakeResponse:
        _advance(self._seconds_per_call)
        return await super().request(*args, **kwargs)


_now = 0.0


def _advance(seconds: float) -> None:
    global _now
    _now += seconds


@pytest.fixture
def frozen_clock(monkeypatch: pytest.MonkeyPatch):
    global _now
    _now = 0.0
    monkeypatch.setattr(appwrite_tables.time, "monotonic", lambda: _now)

    async def _sleep_the_clock(seconds: float) -> None:
        _advance(seconds)

    monkeypatch.setattr(appwrite_tables, "_sleep", _sleep_the_clock)
    return _advance


@pytest.mark.asyncio
async def test_a_read_stops_retrying_once_the_router_would_have_given_up(
    monkeypatch: pytest.MonkeyPatch, frozen_clock
) -> None:
    monkeypatch.setattr(appwrite_tables, "get_settings", lambda: _FakeSettings())
    timeout = _FakeResponse(408, {"message": "Database timed out."})
    # Two slow attempts spend 22 of the 24 second budget, so there is no room
    # for a third: the real error surfaces now rather than after the router has
    # already answered 503 on this request's behalf.
    fake_client = _ClockedClient([timeout, timeout, timeout], seconds_per_call=11.0)
    monkeypatch.setattr(appwrite_tables.httpx, "AsyncClient", lambda **_kw: fake_client)

    with pytest.raises(appwrite_tables.AppwriteTablesError) as excinfo:
        await appwrite_tables.list_rows(filters=["active=true"])

    assert excinfo.value.status_code == 408
    assert fake_client.calls == 2


@pytest.mark.asyncio
async def test_a_read_that_fails_fast_still_gets_all_three_attempts(
    monkeypatch: pytest.MonkeyPatch, frozen_clock
) -> None:
    monkeypatch.setattr(appwrite_tables, "get_settings", lambda: _FakeSettings())
    timeout = _FakeResponse(408, {"message": "Database timed out."})
    fake_client = _ClockedClient([timeout, timeout, timeout], seconds_per_call=0.5)
    monkeypatch.setattr(appwrite_tables.httpx, "AsyncClient", lambda **_kw: fake_client)

    with pytest.raises(appwrite_tables.AppwriteTablesError):
        await appwrite_tables.list_rows(filters=["active=true"])

    assert fake_client.calls == 3


@pytest.mark.asyncio
async def test_a_write_keeps_the_budget_it_always_had(
    monkeypatch: pytest.MonkeyPatch, frozen_clock
) -> None:
    # The crawler is not inside a router timeout, and a bulk write is allowed to
    # take as long as it takes.
    monkeypatch.setattr(appwrite_tables, "get_settings", lambda: _FakeSettings())
    timeout = _FakeResponse(408, {"message": "Database timed out."})
    ok = _FakeResponse(200, {})
    fake_client = _ClockedClient([timeout, timeout, ok], seconds_per_call=29.0)
    monkeypatch.setattr(appwrite_tables.httpx, "AsyncClient", lambda **_kw: fake_client)

    await appwrite_tables.upsert_rows([{"$id": "a"}])

    assert fake_client.calls == 3


@pytest.mark.asyncio
async def test_each_read_attempt_is_given_only_the_time_that_is_left(
    monkeypatch: pytest.MonkeyPatch, frozen_clock
) -> None:
    monkeypatch.setattr(appwrite_tables, "get_settings", lambda: _FakeSettings())
    seen: list[float] = []
    timeout = _FakeResponse(408, {"message": "Database timed out."})
    fake_client = _ClockedClient([timeout, _FakeResponse(200, {"rows": []})], 8.0)

    def _client(**kwargs: Any) -> _ClockedClient:
        seen.append(kwargs["timeout"])
        return fake_client

    monkeypatch.setattr(appwrite_tables.httpx, "AsyncClient", _client)
    await appwrite_tables.list_rows(filters=["active=true"])

    # Each attempt is capped, so the ladder can actually run.
    #
    # This asserted `[24.0, 15.0]` and that was the bug rather than the
    # contract: handing attempt one the entire 24s budget means a single slow
    # draw consumes it and there is never an attempt two. The retry was
    # documented, tested and unable to fire on the failure it was written for.
    assert seen == [8.0, 8.0]


@pytest.mark.asyncio
async def test_a_transport_timeout_is_retried_like_a_408(
    monkeypatch: pytest.MonkeyPatch, frozen_clock
) -> None:
    """The failure the ladder was written for and did not catch.

    Appwrite answers a slow query with its own 408 only sometimes. When the
    query outruns the client it raises `httpx.ReadTimeout` instead, which is an
    exception rather than a response, and the loop let it straight out. That is
    the `httpx.ReadTimeout` behind a 500 on `POST /api/v1/index/search`: a
    retry policy that never once applied to the thing that actually broke.
    """
    monkeypatch.setattr(appwrite_tables, "get_settings", lambda: _FakeSettings())
    calls = 0

    class _TimeoutThenOk:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> _TimeoutThenOk:
            return self

        async def __aexit__(self, *_exc: Any) -> None:
            return None

        async def request(self, *_a: Any, **_kw: Any) -> Any:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise appwrite_tables.httpx.ReadTimeout("timed out")
            return _FakeResponse(200, {"rows": []})

    monkeypatch.setattr(appwrite_tables.httpx, "AsyncClient", _TimeoutThenOk)

    assert await appwrite_tables.list_rows(filters=["active=true"]) == []
    assert calls == 2, "the timeout must be retried, not raised"


@pytest.mark.asyncio
async def test_a_read_that_times_out_every_attempt_reports_it_as_a_timeout(
    monkeypatch: pytest.MonkeyPatch, frozen_clock
) -> None:
    """Exhausting the ladder must still say what happened.

    Raising the raw `httpx.ReadTimeout` gave the caller a 500 with a traceback
    into httpx internals and nothing about which table or how many tries. A
    504 naming the attempts is something a log reader can act on.
    """
    monkeypatch.setattr(appwrite_tables, "get_settings", lambda: _FakeSettings())

    class _AlwaysTimeout:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> _AlwaysTimeout:
            return self

        async def __aexit__(self, *_exc: Any) -> None:
            return None

        async def request(self, *_a: Any, **_kw: Any) -> Any:
            raise appwrite_tables.httpx.ReadTimeout("timed out")

    monkeypatch.setattr(appwrite_tables.httpx, "AsyncClient", _AlwaysTimeout)

    with pytest.raises(appwrite_tables.AppwriteTablesError) as raised:
        await appwrite_tables.list_rows(filters=["active=true"])

    assert raised.value.status_code == 504
    assert "attempts" in str(raised.value)


# ---------------------------------------------------------------------------
# The two request shapes Appwrite wants, which the unit tests above could not
# see because they assert on what the module sends rather than on what Appwrite
# accepts. Both of these shipped broken and were found by running the sweep.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_bulk_update_sends_its_queries_as_json_strings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A read and a write disagree about how a query travels, and only one was right.

    A read sends each query as its own `queries[]` URL parameter, already
    serialised. A bulk PATCH carries them in the body, where Appwrite validates
    the array as strings and rejects objects with a message that reads like a
    size complaint:

        Invalid `queries` param: Value must a valid array no longer than 100
        items and Value must be a valid string ...

    It is not about size. `deactivate_missing` sent two filters and got it, so
    every sweep died at the point it tried to close postings that had gone.
    """
    monkeypatch.setattr(appwrite_tables, "get_settings", lambda: _FakeSettings())
    sent: dict[str, Any] = {}

    class _Capture(_FakeHTTPClient):
        async def request(self, *args: Any, **kwargs: Any) -> _FakeResponse:
            sent.update(kwargs.get("json") or {})
            return await super().request(*args, **kwargs)

    client = _Capture([_FakeResponse(200, {"total": 0})])
    monkeypatch.setattr(appwrite_tables.httpx, "AsyncClient", lambda **_kw: client)

    await appwrite_tables.update_rows(
        filters=["source=greenhouse", "active=true"], data={"active": False}
    )

    assert all(isinstance(q, str) for q in sent["queries"]), sent["queries"]
    # Still real queries, not stringified junk.
    assert json.loads(sent["queries"][0]) == {
        "method": "equal",
        "attribute": "source",
        "values": ["greenhouse"],
    }


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("canonical_id=null", {"method": "isNull", "attribute": "canonical_id"}),
        ("canonical_id!=null", {"method": "isNotNull", "attribute": "canonical_id"}),
    ],
)
def test_a_null_comparison_becomes_a_null_check(
    expression: str, expected: dict[str, Any]
) -> None:
    """Appwrite has no null-valued equality.

    `equal` with a null value is rejected outright: "Query value is invalid for
    attribute". So `canonical_id=null`, which is how `index_stats` counts
    unmerged rows, has to become `isNull` rather than pass a null through as a
    value. It did not, and the sweep's own status report died on it.
    """
    assert appwrite_tables._parse_filter(expression) == expected


def test_a_normal_filter_still_carries_its_value() -> None:
    """The null branch must not swallow ordinary comparisons."""
    assert appwrite_tables._parse_filter("active=true") == {
        "method": "equal",
        "attribute": "active",
        "values": [True],
    }
    assert appwrite_tables._parse_filter("salary_max>=40") == {
        "method": "greaterThanEqual",
        "attribute": "salary_max",
        "values": [40],
    }


# ---------------------------------------------------------------------------
# Appwrite stacks two caps on one `queries[]` entry, and which one binds
# depends on the data. Both measured against the live table on 2026-08-30 with
# 36-character ids: 100 -> 200, 101 -> 400 "no longer than 100 items", 200 ->
# 414 URI Too Long.
# ---------------------------------------------------------------------------


def test_short_ids_are_chunked_by_the_item_cap() -> None:
    """The half a character-only budget missed.

    `_lookup_by_source_id` bounded chunks by characters alone. At 2-character
    ids a 3,500-char budget yields over a thousand items in one chunk, more
    than ten times Appwrite's separate 100-item cap. That is the "400 this
    budget-based chunking should have prevented but, live, has not" the
    diagnostics in `upsert.py` were added for.
    """
    chunks = appwrite_tables.chunk_values(["ab"] * 500)

    assert all(len(c) <= appwrite_tables.MAX_QUERY_ITEMS for c in chunks)
    assert sum(len(c) for c in chunks) == 500, "nothing is dropped"


def test_long_ids_are_chunked_by_the_character_cap() -> None:
    """The half it did catch, kept.

    The scraper falls back to a full posting URL when a board gives it no
    native id, and a handful of those blow past 4096 encoded characters while
    still being far under 100 items.
    """
    chunks = appwrite_tables.chunk_values(["u" * 900] * 8)

    assert all(len(c) < appwrite_tables.MAX_QUERY_ITEMS for c in chunks)
    for chunk in chunks:
        assert sum(len(v) + 3 for v in chunk) <= appwrite_tables.MAX_QUERY_CHARS


def test_a_full_page_of_uuids_is_split() -> None:
    """The case that made this urgent.

    `_hydrate_page` sent every id on the page in one `equal`, and a page is up
    to `MAX_LIMIT` (200). At 36-character ids that is 8,065 encoded characters,
    which the live table answered with 414 URI Too Long. Not a risk: a
    certainty, reached the moment anyone asked for more than 100 results.
    """
    chunks = appwrite_tables.chunk_values([f"{i:036d}" for i in range(200)])

    assert len(chunks) > 1
    assert all(len(c) <= appwrite_tables.MAX_QUERY_ITEMS for c in chunks)


def test_a_single_oversized_value_is_not_split() -> None:
    """Splitting one value would change what is being asked for.

    A query that is too long fails loudly. A query that silently asks about
    half an id returns the wrong rows, which is worse.
    """
    huge = "u" * (appwrite_tables.MAX_QUERY_CHARS + 500)

    chunks = appwrite_tables.chunk_values([huge])

    assert chunks == [[huge]]


def test_nothing_in_gives_nothing_out() -> None:
    assert appwrite_tables.chunk_values([]) == []


# ---------------------------------------------------------------------------
# Appwrite bills reads PER ROW, not per API call. From its docs: "if you fetch
# a table of 50 rows with a single API call, this counts as 50 read operations,
# not as a single operation." A full-table walk of this index is 359,416 reads
# against a 500,000/month Free allowance, and running one took the project
# offline with 402 limit_databases_reads_exceeded.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_counting_costs_one_row_not_the_whole_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The regression that took the database down.

    `count_rows_exact` used to page the table with a cursor. It was correct,
    it was fast, and at per-row billing it spent roughly ten times the monthly
    quota over three development runs. Whatever this function does, it must
    not scale its cost with the size of the table, so this asserts on the wire
    request rather than on the answer.
    """
    seen: list[list[tuple[str, str]]] = []

    async def _capture(_method: str, **kwargs: Any) -> dict[str, Any]:
        seen.append(kwargs.get("params") or [])
        return {"total": 359_416, "rows": [{"$id": "1"}]}

    monkeypatch.setattr(appwrite_tables, "get_settings", lambda: _FakeSettings())
    monkeypatch.setattr(appwrite_tables, "_request", _capture)

    total, exact = await appwrite_tables.count_rows_exact(filters=["active=true"])

    assert len(seen) == 1, "one request, not one per page"
    limits = [json.loads(v)["values"][0] for _k, v in seen[0] if '"limit"' in v]
    assert limits == [1], "and it asks the wire for a single row"
    assert not any("cursorAfter" in v for _k, v in seen[0]), "no paging"
    assert total == 359_416
    assert exact is False, "above the cap, so not exact and it must say so"


@pytest.mark.asyncio
async def test_a_count_under_the_cap_is_reported_as_exact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Below `TOTAL_CAP` Appwrite's own total is a real number, measured:
    `source=icims` returned 3042 and `active=false` returned 1412."""

    async def _small(_method: str, **_kwargs: Any) -> dict[str, Any]:
        return {"total": 3042, "rows": [{"$id": "1"}]}

    monkeypatch.setattr(appwrite_tables, "get_settings", lambda: _FakeSettings())
    monkeypatch.setattr(appwrite_tables, "_request", _small)

    total, exact = await appwrite_tables.count_rows_exact(filters=["source=icims"])

    assert (total, exact) == (3042, True)
