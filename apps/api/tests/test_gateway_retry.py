"""A rate-limited gateway is a delay, not a failed operation.

Two real `resume_revision` jobs died outright on Manifest's
`429 ... 'code': 'fallback_exhausted'` thirty-five seconds apart, taking a
two-minute edit with them. `create_message` is the one place every agent in this
codebase reaches the gateway, so the retry lives there and these tests pin the
behaviour that matters: 429 and 529 are retried, everything else is not, the
gateway's own `retry-after` wins when it sends one, and the retry budget is
bounded so a sustained outage still fails rather than hanging.
"""
from __future__ import annotations

from typing import Any

import anthropic
import httpx
import pytest

from job_os.services import llm_json


class _Stream:
    def __init__(self, message: Any) -> None:
        self._message = message

    async def __aenter__(self) -> _Stream:
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False

    async def get_final_message(self) -> Any:
        return self._message


class _Messages:
    """A gateway that raises the given errors in order, then answers."""

    def __init__(self, errors: list[Exception], answer: str = "ok") -> None:
        self._errors = list(errors)
        self._answer = answer
        self.calls = 0

    def stream(self, **_kwargs: Any) -> _Stream:
        self.calls += 1
        if self._errors:
            raise self._errors.pop(0)
        return _Stream(self._answer)


class _Client:
    def __init__(self, messages: _Messages) -> None:
        self.messages = messages


def _status_error(status: int, *, retry_after: str | None = None) -> anthropic.APIStatusError:
    headers = {"retry-after": retry_after} if retry_after else {}
    request = httpx.Request("POST", "https://gateway.test/v1/messages")
    response = httpx.Response(status, headers=headers, request=request, json={})
    return anthropic.APIStatusError("busy", response=response, body=None)


@pytest.fixture()
def slept(gateway_waits: list[float]) -> list[float]:
    """The waits the retry would have served, recorded by the conftest fixture."""
    return gateway_waits


@pytest.mark.asyncio
async def test_rate_limit_is_retried_and_the_call_succeeds(slept: list[float]) -> None:
    messages = _Messages([_status_error(429), _status_error(429)])
    result = await llm_json.create_message(_Client(messages))
    assert result == "ok"
    assert messages.calls == 3
    assert len(slept) == 2


@pytest.mark.asyncio
async def test_overloaded_gateway_is_retried_too(slept: list[float]) -> None:
    messages = _Messages([_status_error(529)])
    assert await llm_json.create_message(_Client(messages)) == "ok"
    assert messages.calls == 2


@pytest.mark.asyncio
async def test_a_bad_request_is_not_retried(slept: list[float]) -> None:
    # A 400 will fail identically on a second attempt, and retrying it would turn
    # a fast, clear error into a slow, identical one.
    messages = _Messages([_status_error(400)])
    with pytest.raises(anthropic.APIStatusError):
        await llm_json.create_message(_Client(messages))
    assert messages.calls == 1
    assert slept == []


@pytest.mark.asyncio
async def test_the_retry_budget_is_bounded(slept: list[float]) -> None:
    # A sustained outage has to surface as a failure. Hanging on it would blow the
    # Appwrite function's 900s timeout and lose the whole job instead of the call.
    messages = _Messages([_status_error(429) for _ in range(10)])
    with pytest.raises(anthropic.APIStatusError):
        await llm_json.create_message(_Client(messages))
    assert messages.calls == len(llm_json._RETRY_BACKOFF_SECONDS) + 1
    assert len(slept) == len(llm_json._RETRY_BACKOFF_SECONDS)
    assert sum(slept) < 120


@pytest.mark.asyncio
async def test_the_gateway_retry_after_header_wins(slept: list[float]) -> None:
    messages = _Messages([_status_error(429, retry_after="12")])
    assert await llm_json.create_message(_Client(messages)) == "ok"
    assert slept == [12.0]


@pytest.mark.asyncio
async def test_an_absurd_retry_after_is_capped(slept: list[float]) -> None:
    messages = _Messages([_status_error(429, retry_after="3600")])
    assert await llm_json.create_message(_Client(messages)) == "ok"
    assert slept == [llm_json._MAX_RETRY_AFTER_SECONDS]


@pytest.mark.asyncio
async def test_an_unparseable_retry_after_falls_back_to_the_schedule(
    slept: list[float],
) -> None:
    # The HTTP-date form. Guessing at a parse is worse than using our own wait.
    messages = _Messages([_status_error(429, retry_after="Wed, 21 Oct 2026 07:28:00 GMT")])
    assert await llm_json.create_message(_Client(messages)) == "ok"
    assert len(slept) == 1
    first = llm_json._RETRY_BACKOFF_SECONDS[0]
    assert first * 0.8 <= slept[0] <= first * 1.2


@pytest.mark.asyncio
async def test_a_clean_call_never_sleeps(slept: list[float]) -> None:
    messages = _Messages([])
    assert await llm_json.create_message(_Client(messages)) == "ok"
    assert messages.calls == 1
    assert slept == []
