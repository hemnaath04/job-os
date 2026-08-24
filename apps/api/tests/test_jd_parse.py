"""`parse_jd`'s degraded paths: no key, invalid JSON, and a gateway failure.

Same shape as `test_discovery_smart_search.py` and the same underlying bug:
`jd_parse.py` called `client.messages.create` directly, bypassing
`create_message`'s retry and fallback schedule, so a Manifest outage raised
straight through the add-job-from-url/text flow instead of degrading to
"added without structured JD fields" the way the no-key branch already did.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import anthropic
import httpx
import pytest

from job_os.services import jd_parse


@dataclass
class _FakeSettings:
    anthropic_api_key: str | None = "manifest-key"
    anthropic_base_url: str | None = None
    manifest_tier_fast: str = "job-os-haiku"
    anthropic_model_extract: str = "manifest/auto"


@pytest.mark.asyncio
async def test_no_api_key_returns_just_the_title_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jd_parse, "get_settings", lambda: _FakeSettings(anthropic_api_key=None))
    result = await jd_parse.parse_jd("some jd text", title_hint="Backend Engineer")
    assert result == {"parse_incomplete": True, "title": "Backend Engineer"}


@pytest.mark.asyncio
async def test_gateway_failure_degrades_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(jd_parse, "get_settings", lambda: _FakeSettings())

    async def _raise_gateway_error(*_args: Any, **_kwargs: Any) -> None:
        request = httpx.Request("POST", "https://gateway.test/v1/messages")
        response = httpx.Response(401, request=request, json={})
        raise anthropic.APIStatusError("M102", response=response, body=None)

    monkeypatch.setattr(jd_parse, "create_message", _raise_gateway_error)

    result = await jd_parse.parse_jd("some jd text", title_hint="Backend Engineer")
    assert result == {"parse_incomplete": True, "title": "Backend Engineer"}


@pytest.mark.asyncio
async def test_gateway_failure_with_no_title_hint_returns_empty_dict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(jd_parse, "get_settings", lambda: _FakeSettings())

    async def _raise_gateway_error(*_args: Any, **_kwargs: Any) -> None:
        request = httpx.Request("POST", "https://gateway.test/v1/messages")
        response = httpx.Response(500, request=request, json={})
        raise anthropic.APIStatusError("boom", response=response, body=None)

    monkeypatch.setattr(jd_parse, "create_message", _raise_gateway_error)

    result = await jd_parse.parse_jd("some jd text")
    assert result == {"parse_incomplete": True}


class _FakeMessage:
    def __init__(self, text: str) -> None:
        self.content = [type("Block", (), {"type": "text", "text": text})()]


@pytest.mark.asyncio
async def test_a_single_timeout_is_retried_once_and_the_retry_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A normal-length JD hit this 3/3 for real in one session: a timeout on
    the first attempt should not immediately give up when one retry has a
    real chance of landing."""
    monkeypatch.setattr(jd_parse, "get_settings", lambda: _FakeSettings())
    slept: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(jd_parse, "_sleep", _fake_sleep)
    calls = 0

    async def _flaky(*_args: Any, **_kwargs: Any) -> _FakeMessage:
        nonlocal calls
        calls += 1
        if calls == 1:
            request = httpx.Request("POST", "https://gateway.test/v1/messages")
            raise anthropic.APITimeoutError(request=request)
        return _FakeMessage('{"title": "Backend Engineer"}')

    monkeypatch.setattr(jd_parse, "create_message", _flaky)

    result = await jd_parse.parse_jd("some jd text")
    assert calls == 2
    assert slept == [jd_parse._JD_PARSE_RETRY_DELAY_SECONDS]
    assert result["title"] == "Backend Engineer"
    assert result["parse_incomplete"] is False


@pytest.mark.asyncio
async def test_two_consecutive_timeouts_still_degrade_honestly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The retry reduces how often a user hits this, it does not remove the
    honest fallback: a second timeout in a row still reports incomplete
    rather than hanging or raising."""
    monkeypatch.setattr(jd_parse, "get_settings", lambda: _FakeSettings())

    async def _fake_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr(jd_parse, "_sleep", _fake_sleep)
    calls = 0

    async def _always_times_out(*_args: Any, **_kwargs: Any) -> _FakeMessage:
        nonlocal calls
        calls += 1
        request = httpx.Request("POST", "https://gateway.test/v1/messages")
        raise anthropic.APITimeoutError(request=request)

    monkeypatch.setattr(jd_parse, "create_message", _always_times_out)

    result = await jd_parse.parse_jd("some jd text", title_hint="Backend Engineer")
    assert calls == 2
    assert result == {"parse_incomplete": True, "title": "Backend Engineer"}
