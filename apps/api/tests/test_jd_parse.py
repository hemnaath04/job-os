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


class _FakeUsage:
    def __init__(self, output_tokens: int) -> None:
        self.output_tokens = output_tokens


class _TruncatedMessage(_FakeMessage):
    """What the gateway returns in a degraded window: JSON cut off mid-value."""

    def __init__(self, text: str, output_tokens: int = 4096) -> None:
        super().__init__(text)
        self.stop_reason = "max_tokens"
        self.usage = _FakeUsage(output_tokens)


# A real one, copied from a live degraded run rather than invented.
TRUNCATED_JSON = (
    '{\n  "title": "Software Engineering Intern, Summer 2027",\n'
    '  "location": "New York, NY",\n  "salary_min": 52000,\n'
    '  "required_skills": ["Pytho'
)


@pytest.mark.asyncio
async def test_a_truncated_answer_is_retried_rather_than_accepted_as_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bug this covers reached a user: a JD with a location, a salary band
    and a skills list came back as "no details could be read from it", because
    the one reply arrived cut off mid-value and nothing tried again."""
    monkeypatch.setattr(jd_parse, "get_settings", lambda: _FakeSettings())
    calls = 0

    async def _truncated_then_whole(*_args: Any, **_kwargs: Any) -> _FakeMessage:
        nonlocal calls
        calls += 1
        if calls == 1:
            return _TruncatedMessage(TRUNCATED_JSON)
        return _FakeMessage('{"title": "Software Engineering Intern", "location": "New York, NY"}')

    monkeypatch.setattr(jd_parse, "create_message", _truncated_then_whole)

    result = await jd_parse.parse_jd("a full length jd")

    assert calls == 2
    assert result["title"] == "Software Engineering Intern"
    assert result["location"] == "New York, NY"
    assert result["parse_incomplete"] is False


@pytest.mark.asyncio
async def test_the_retry_does_not_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unlike a timeout, a truncated answer is not rate limiting, and the
    callers are interactive requests inside a hard 30s ceiling. Sleeping here
    would spend the budget the retry needs."""
    monkeypatch.setattr(jd_parse, "get_settings", lambda: _FakeSettings())
    slept: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(jd_parse, "_sleep", _fake_sleep)
    calls = 0

    async def _truncated_then_whole(*_args: Any, **_kwargs: Any) -> _FakeMessage:
        nonlocal calls
        calls += 1
        return _TruncatedMessage(TRUNCATED_JSON) if calls == 1 else _FakeMessage('{"title": "X"}')

    monkeypatch.setattr(jd_parse, "create_message", _truncated_then_whole)

    await jd_parse.parse_jd("a full length jd")
    assert slept == []


@pytest.mark.asyncio
async def test_two_truncated_answers_still_degrade_honestly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The retry lowers how often this is hit, it does not remove the honest
    fallback. Twice unusable still reports incomplete rather than inventing
    fields out of half an object."""
    monkeypatch.setattr(jd_parse, "get_settings", lambda: _FakeSettings())
    calls = 0

    async def _always_truncated(*_args: Any, **_kwargs: Any) -> _FakeMessage:
        nonlocal calls
        calls += 1
        return _TruncatedMessage(TRUNCATED_JSON)

    monkeypatch.setattr(jd_parse, "create_message", _always_truncated)

    result = await jd_parse.parse_jd("a full length jd", title_hint="Backend Engineer")

    assert calls == 2
    assert result == {"parse_incomplete": True, "title": "Backend Engineer"}


@pytest.mark.asyncio
async def test_an_empty_reply_is_retried_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """The other shape seen live: the reply had no text at all."""
    monkeypatch.setattr(jd_parse, "get_settings", lambda: _FakeSettings())
    calls = 0

    async def _empty_then_whole(*_args: Any, **_kwargs: Any) -> _FakeMessage:
        nonlocal calls
        calls += 1
        return _TruncatedMessage("") if calls == 1 else _FakeMessage('{"title": "X"}')

    monkeypatch.setattr(jd_parse, "create_message", _empty_then_whole)

    result = await jd_parse.parse_jd("a full length jd")
    assert calls == 2
    assert result["title"] == "X"


@pytest.mark.asyncio
async def test_a_whole_answer_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """The normal path stays one call. A second would double the cost and the
    latency of every import for nothing."""
    monkeypatch.setattr(jd_parse, "get_settings", lambda: _FakeSettings())
    calls = 0

    async def _whole(*_args: Any, **_kwargs: Any) -> _FakeMessage:
        nonlocal calls
        calls += 1
        return _FakeMessage('{"title": "Backend Engineer"}')

    monkeypatch.setattr(jd_parse, "create_message", _whole)

    result = await jd_parse.parse_jd("some jd text")
    assert calls == 1
    assert result["title"] == "Backend Engineer"


@pytest.mark.asyncio
async def test_the_token_ceiling_is_the_one_that_was_measured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """2048 was what a degraded reply ran out of. Pinned so a well-meaning
    trim back does not quietly reintroduce the truncation."""
    monkeypatch.setattr(jd_parse, "get_settings", lambda: _FakeSettings())
    seen: dict[str, Any] = {}

    async def _capture(*_args: Any, **kwargs: Any) -> _FakeMessage:
        seen.update(kwargs)
        return _FakeMessage('{"title": "Backend Engineer"}')

    monkeypatch.setattr(jd_parse, "create_message", _capture)

    await jd_parse.parse_jd("some jd text")
    assert seen["max_tokens"] == jd_parse._JD_PARSE_MAX_TOKENS
    assert jd_parse._JD_PARSE_MAX_TOKENS > 2048
