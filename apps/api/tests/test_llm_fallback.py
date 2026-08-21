"""Fallback provider for a Manifest gateway that is genuinely out of capacity.

`create_message` already retries a rate-limited or overloaded Manifest on its
own schedule (see `test_gateway_retry.py`); these tests cover what happens
once that whole schedule is spent and Manifest is *still* 429/529 -- a
sustained capacity failure, not the blip the schedule above exists to absorb.
With `OPENROUTER_API_KEY` configured, that is exactly when `create_message`
falls to OpenRouter for the same completion (a DeepSeek model) before giving
up. The cases that matter: Manifest succeeding never touches any of this,
Manifest exhausted with OpenRouter healthy ships OpenRouter's answer, and
OpenRouter also down surfaces Manifest's own real error rather than
swallowing it into something misleading.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import anthropic
import httpx
import pytest

from job_os.services import llm_json

# ---------------------------------------------------------------------------
# Fakes for the primary (Manifest) call, matching test_gateway_retry.py's
# shapes so a sustained-outage setup here reads the same way it does there.
# ---------------------------------------------------------------------------


class _Stream:
    def __init__(self, message: Any) -> None:
        self._message = message

    async def __aenter__(self) -> _Stream:
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False

    async def get_final_message(self) -> Any:
        return self._message


class _ManifestMessages:
    """A Manifest gateway that raises the given errors in order, then answers."""

    def __init__(self, errors: list[Exception], answer: Any = "manifest-ok") -> None:
        self._errors = list(errors)
        self._answer = answer
        self.calls = 0

    def stream(self, **_kwargs: Any) -> _Stream:
        self.calls += 1
        if self._errors:
            raise self._errors.pop(0)
        return _Stream(self._answer)


class _ManifestClient:
    def __init__(self, messages: _ManifestMessages) -> None:
        self.messages = messages


def _status_error(status: int) -> anthropic.APIStatusError:
    request = httpx.Request("POST", "https://gateway.test/v1/messages")
    response = httpx.Response(status, request=request, json={})
    return anthropic.APIStatusError("busy", response=response, body=None)


def _sustained_429s(count: int = 10) -> list[Exception]:
    # More than the retry schedule can absorb, so `create_message` reaches
    # exhaustion (the branch this whole file is about) rather than recovering
    # mid-schedule.
    return [_status_error(429) for _ in range(count)]


# ---------------------------------------------------------------------------
# Fakes for the fallback provider itself.
# ---------------------------------------------------------------------------


@dataclass
class _FakeSettings:
    openrouter_api_key: str | None = None


class _FakeHTTPResponse:
    def __init__(self, status_code: int, json_body: dict[str, Any]) -> None:
        self.status_code = status_code
        self._json_body = json_body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError(
                "fallback provider error", request=request, response=response
            )

    def json(self) -> dict[str, Any]:
        return self._json_body


class _FakeHTTPClient:
    """Stands in for `httpx.AsyncClient` inside `_call_openrouter`."""

    def __init__(
        self, response: _FakeHTTPResponse | None = None, error: Exception | None = None
    ) -> None:
        self._response = response
        self._error = error
        self.calls = 0
        self.last_payload: dict[str, Any] | None = None
        self.last_headers: dict[str, str] | None = None

    async def __aenter__(self) -> _FakeHTTPClient:
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False

    async def post(
        self, url: str, *, headers: dict[str, str], json: dict[str, Any]
    ) -> _FakeHTTPResponse:
        self.calls += 1
        self.last_payload = json
        self.last_headers = headers
        if self._error is not None:
            raise self._error
        assert self._response is not None
        return self._response


_TAILOR_KWARGS: dict[str, Any] = {
    "model": "anthropic/claude-sonnet-5-subscription",
    "max_tokens": 32000,
    "system": "be terse",
    "messages": [{"role": "user", "content": "draft the resume"}],
    "extra_headers": {"x-manifest-tier": "job-os-sonnet"},
    "output_config": {"effort": "high"},
}


# ---------------------------------------------------------------------------
# 1. Manifest succeeds: no fallback attempted, behaviour unchanged.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manifest_success_never_touches_fallback(
    monkeypatch: pytest.MonkeyPatch, gateway_waits: list[float]
) -> None:
    async def _fail_if_called(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("fallback must not be attempted when Manifest succeeds")

    # Even with a provider configured, a clean Manifest call must never reach
    # the fallback path at all.
    monkeypatch.setattr(
        llm_json, "get_settings", lambda: _FakeSettings(openrouter_api_key="or-key")
    )
    monkeypatch.setattr(llm_json, "_try_fallback_providers", _fail_if_called)

    messages = _ManifestMessages([])
    result = await llm_json.create_message(_ManifestClient(messages), **_TAILOR_KWARGS)

    assert result == "manifest-ok"
    assert messages.calls == 1
    assert gateway_waits == []


# ---------------------------------------------------------------------------
# Inert by default: with no key configured, exhaustion behaves exactly as it
# did before this feature existed.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_provider_configured_is_a_no_op(
    monkeypatch: pytest.MonkeyPatch, gateway_waits: list[float]
) -> None:
    monkeypatch.setattr(llm_json, "get_settings", lambda: _FakeSettings())
    messages = _ManifestMessages(_sustained_429s())

    with pytest.raises(anthropic.APIStatusError):
        await llm_json.create_message(_ManifestClient(messages), **_TAILOR_KWARGS)

    assert messages.calls == len(llm_json._RETRY_BACKOFF_SECONDS) + 1


# ---------------------------------------------------------------------------
# 2. Manifest exhausts its retries; OpenRouter succeeds.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_openrouter_serves_once_manifest_is_exhausted(
    monkeypatch: pytest.MonkeyPatch, gateway_waits: list[float]
) -> None:
    monkeypatch.setattr(
        llm_json, "get_settings", lambda: _FakeSettings(openrouter_api_key="or-key")
    )
    fake_http = _FakeHTTPClient(
        response=_FakeHTTPResponse(
            200,
            {
                "choices": [
                    {"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}
                ],
                "usage": {"completion_tokens": 12},
            },
        )
    )
    monkeypatch.setattr(llm_json.httpx, "AsyncClient", lambda **_kw: fake_http)

    messages = _ManifestMessages(_sustained_429s())
    result = await llm_json.create_message(_ManifestClient(messages), **_TAILOR_KWARGS)

    # Manifest's own schedule ran to completion before the fallback was tried.
    assert messages.calls == len(llm_json._RETRY_BACKOFF_SECONDS) + 1
    assert fake_http.calls == 1

    # The shim satisfies the same two functions every real caller uses.
    assert llm_json.response_text(result) == '{"ok": true}'
    diagnostics = llm_json.response_diagnostics(result)
    assert diagnostics["stop_reason"] == "end_turn"
    assert diagnostics["output_tokens"] == 12

    # The request was translated correctly: system became a system message,
    # the user turn survived, and the primary call's 32000 max_tokens was
    # capped rather than passed through uncapped.
    assert fake_http.last_payload is not None
    assert fake_http.last_payload["model"] == llm_json._OPENROUTER_MODEL
    assert fake_http.last_payload["messages"][0] == {"role": "system", "content": "be terse"}
    assert fake_http.last_payload["messages"][1] == {
        "role": "user",
        "content": "draft the resume",
    }
    assert fake_http.last_payload["max_tokens"] == llm_json._FALLBACK_MAX_TOKENS_CEILING
    assert fake_http.last_headers is not None
    assert fake_http.last_headers["Authorization"] == "Bearer or-key"


# ---------------------------------------------------------------------------
# 3. Manifest exhausts, OpenRouter also fails: the real error surfaces, it is
# not swallowed.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_openrouter_failing_surfaces_the_real_error(
    monkeypatch: pytest.MonkeyPatch, gateway_waits: list[float]
) -> None:
    monkeypatch.setattr(
        llm_json, "get_settings", lambda: _FakeSettings(openrouter_api_key="or-key")
    )
    fake_http = _FakeHTTPClient(error=httpx.ConnectError("openrouter is down"))
    monkeypatch.setattr(llm_json.httpx, "AsyncClient", lambda **_kw: fake_http)

    messages = _ManifestMessages(_sustained_429s())

    with pytest.raises(anthropic.APIStatusError):
        await llm_json.create_message(_ManifestClient(messages), **_TAILOR_KWARGS)

    # Every layer was genuinely tried -- this is not a short-circuit.
    assert messages.calls == len(llm_json._RETRY_BACKOFF_SECONDS) + 1
    assert fake_http.calls == 1


# ---------------------------------------------------------------------------
# Translation helpers, tested directly.
# ---------------------------------------------------------------------------


def test_to_openai_messages_flattens_system_and_user_turns() -> None:
    result = llm_json._to_openai_messages(
        "be terse",
        [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": [{"type": "text", "text": "hi there"}]},
        ],
    )
    assert result == [
        {"role": "system", "content": "be terse"},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
    ]


def test_to_openai_messages_omits_system_when_none() -> None:
    result = llm_json._to_openai_messages(None, [{"role": "user", "content": "hi"}])
    assert result == [{"role": "user", "content": "hi"}]


def test_a_pdf_content_block_is_untranslatable() -> None:
    with pytest.raises(llm_json._FallbackUntranslatableError):
        llm_json._flatten_content_to_text(
            [{"type": "document", "source": {"type": "base64", "data": "..."}}]
        )


@pytest.mark.asyncio
async def test_openrouter_is_skipped_not_crashed_on_untranslatable_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A request carrying a PDF block (the shape `latex_from_document.py`
    # builds) can't be expressed to OpenRouter's OpenAI-compatible endpoint --
    # `_call_openrouter` should skip it (return None) rather than sending
    # something silently wrong or raising an exception of its own.
    settings = _FakeSettings(openrouter_api_key="or-key")
    kwargs = {
        "system": "extract the template",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "document", "source": {"type": "base64", "data": "..."}},
                    {"type": "text", "text": "go"},
                ],
            }
        ],
        "max_tokens": 8000,
    }
    result = await llm_json._call_openrouter(settings, kwargs)
    assert result is None


def test_fallback_max_tokens_is_capped() -> None:
    assert llm_json._fallback_max_tokens({"max_tokens": 48000}) == (
        llm_json._FALLBACK_MAX_TOKENS_CEILING
    )
    assert llm_json._fallback_max_tokens({"max_tokens": 4000}) == 4000
    assert llm_json._fallback_max_tokens({}) == llm_json._FALLBACK_MAX_TOKENS_CEILING
