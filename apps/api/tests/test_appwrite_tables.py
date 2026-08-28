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

    # First attempt: the whole 24s budget. Second: what the first attempt and
    # the one-second wait left of it.
    assert seen == [24.0, 15.0]
