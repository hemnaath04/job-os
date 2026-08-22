"""`_request`'s retry on Appwrite's own 408 "Database timed out."

Seen in production on `job_postings` search under load: four requests failed
this way inside ninety seconds, then an equivalent request later succeeded in
nine -- borderline, not a structurally missing index, which is exactly the
shape one retry is for. A non-408 error, and a 408 that recurs on the retry
too, must still surface rather than loop forever or swallow the real failure.
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
async def test_two_consecutive_408s_surface_as_the_real_error(
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
        ]
    )
    monkeypatch.setattr(appwrite_tables.httpx, "AsyncClient", lambda **_kw: fake_client)

    with pytest.raises(appwrite_tables.AppwriteTablesError) as excinfo:
        await appwrite_tables.list_rows(filters=["active=true"])

    assert excinfo.value.status_code == 408
    assert fake_client.calls == 2


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
