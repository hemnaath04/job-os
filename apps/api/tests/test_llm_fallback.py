"""Fallback provider for a Manifest gateway that cannot serve a request at all.

`create_message` already retries a rate-limited or overloaded Manifest on its
own schedule (see `test_gateway_retry.py`); these tests cover the two ways
that schedule stops helping: Manifest fails outright with something other
than a 429/529 (a 401 like the M102 "OAuth credentials could not be
refreshed" error seen in production, a 403, its own 500), tried immediately
without waiting; or Manifest stays 429/529 for the whole retry schedule, a
sustained capacity failure rather than the blip that schedule exists to
absorb. With `OPENROUTER_API_KEY` configured, either case falls to OpenRouter
for the same completion (a DeepSeek model) before giving up. The cases that
matter: Manifest succeeding never touches any of this, either failure mode
with OpenRouter healthy ships OpenRouter's answer, and OpenRouter also down
(or unconfigured) surfaces Manifest's own real error rather than swallowing
it into something misleading.
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
        # What the SDK was actually handed. The real SDK rejects an unknown
        # keyword, so a fallback-only option leaking into these is a broken
        # primary call rather than a cosmetic slip.
        self.seen_kwargs: list[dict[str, Any]] = []

    def stream(self, **_kwargs: Any) -> _Stream:
        self.calls += 1
        self.seen_kwargs.append(dict(_kwargs))
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
# 1b. Manifest fails outright (not a 429/529) -- e.g. the M102 "OAuth
# credentials could not be refreshed" error, a real 401 seen in production.
# The fallback is tried immediately, without spending the retry schedule on
# a capacity blip this plainly is not.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_openrouter_serves_immediately_on_a_non_retryable_status(
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
                "usage": {"completion_tokens": 5},
            },
        )
    )
    monkeypatch.setattr(llm_json.httpx, "AsyncClient", lambda **_kw: fake_http)

    # One 401, not ten 429s: this must not wait out the retry schedule.
    messages = _ManifestMessages([_status_error(401)])
    result = await llm_json.create_message(_ManifestClient(messages), **_TAILOR_KWARGS)

    assert messages.calls == 1
    assert fake_http.calls == 1
    assert llm_json.response_text(result) == '{"ok": true}'
    assert gateway_waits == []


@pytest.mark.asyncio
async def test_non_retryable_status_with_no_provider_still_raises(
    monkeypatch: pytest.MonkeyPatch, gateway_waits: list[float]
) -> None:
    monkeypatch.setattr(llm_json, "get_settings", lambda: _FakeSettings())
    messages = _ManifestMessages([_status_error(401)])

    with pytest.raises(anthropic.APIStatusError) as excinfo:
        await llm_json.create_message(_ManifestClient(messages), **_TAILOR_KWARGS)

    assert excinfo.value.status_code == 401
    assert messages.calls == 1


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
    # The first rung of the ladder, and the free one. A change that reorders
    # these should have to say so here.
    assert fake_http.last_payload["model"] == llm_json._OPENROUTER_MODELS[0]
    assert llm_json._OPENROUTER_MODELS[0].endswith(":free")
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

    # Every layer was genuinely tried -- this is not a short-circuit. That now
    # includes every rung of the model ladder: a free model answering 429 is
    # cheap to discover and a poor reason to give up while a second free model
    # and a paid one are still untried.
    assert messages.calls == len(llm_json._RETRY_BACKOFF_SECONDS) + 1
    assert fake_http.calls == len(llm_json._OPENROUTER_MODELS)


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


# ---------------------------------------------------------------------------
# The model ladder. Free rungs first, and a rung that fails is not the end of
# the attempt: the free tier answers 429 in a tenth of a second when it is
# busy, which is cheap to discover and a poor reason to give up.
# ---------------------------------------------------------------------------


class _LadderHTTPClient(_FakeHTTPClient):
    """Fails every model except one, and records the order they were tried."""

    def __init__(self, succeed_on: str) -> None:
        super().__init__()
        self._succeed_on = succeed_on
        self.models: list[str] = []
        self.payloads: list[dict[str, Any]] = []

    async def post(
        self, url: str, *, headers: dict[str, str], json: dict[str, Any]
    ) -> _FakeHTTPResponse:
        self.calls += 1
        self.last_payload = json
        self.last_headers = headers
        self.models.append(json["model"])
        self.payloads.append(json)
        if json["model"] != self._succeed_on:
            raise httpx.ConnectError(f"{json['model']} is busy")
        return _FakeHTTPResponse(
            200,
            {
                "choices": [
                    {"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}
                ],
                "usage": {"completion_tokens": 5},
            },
        )


@pytest.mark.asyncio
async def test_a_busy_free_model_falls_through_to_the_next_rung(
    monkeypatch: pytest.MonkeyPatch, gateway_waits: list[float]
) -> None:
    """The failure this exists for: `z-ai/glm-5.2:free` answers 429 in 0.1s.

    Giving up there would leave a configured, working, free second model and a
    paid third one untried, and surface the gateway's error as if nothing could
    have served the request.
    """
    monkeypatch.setattr(
        llm_json, "get_settings", lambda: _FakeSettings(openrouter_api_key="or-key")
    )
    second = llm_json._OPENROUTER_MODELS[1]
    fake_http = _LadderHTTPClient(succeed_on=second)
    monkeypatch.setattr(llm_json.httpx, "AsyncClient", lambda **_kw: fake_http)

    result = await llm_json.create_message(
        _ManifestClient(_ManifestMessages(_sustained_429s())), **_TAILOR_KWARGS
    )

    assert fake_http.models == list(llm_json._OPENROUTER_MODELS[:2])
    assert llm_json.response_text(result) == '{"ok": true}'


@pytest.mark.asyncio
async def test_a_two_hundred_carrying_nothing_is_treated_as_a_failure(
    monkeypatch: pytest.MonkeyPatch, gateway_waits: list[float]
) -> None:
    """An empty body is a failure that happens to have the wrong status code.

    Returning it would hand the caller a shim whose text is "", which every
    JSON parser above here reports as a malformed reply rather than as a
    provider that did not answer.
    """
    monkeypatch.setattr(
        llm_json, "get_settings", lambda: _FakeSettings(openrouter_api_key="or-key")
    )

    class _EmptyThenFine(_LadderHTTPClient):
        async def post(self, url: str, *, headers, json):  # type: ignore[no-untyped-def]
            self.calls += 1
            self.models.append(json["model"])
            if len(self.models) == 1:
                return _FakeHTTPResponse(
                    200,
                    {"choices": [{"message": {"content": "   "}, "finish_reason": "stop"}]},
                )
            return _FakeHTTPResponse(
                200,
                {
                    "choices": [
                        {"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}
                    ]
                },
            )

    fake_http = _EmptyThenFine(succeed_on="unused")
    monkeypatch.setattr(llm_json.httpx, "AsyncClient", lambda **_kw: fake_http)

    result = await llm_json.create_message(
        _ManifestClient(_ManifestMessages(_sustained_429s())), **_TAILOR_KWARGS
    )

    assert len(fake_http.models) == 2, "the empty reply did not end the attempt"
    assert llm_json.response_text(result) == '{"ok": true}'


@pytest.mark.asyncio
async def test_json_mode_is_opt_in_and_never_reaches_the_sdk(
    monkeypatch: pytest.MonkeyPatch, gateway_waits: list[float]
) -> None:
    """Two claims in one, because breaking either is silent.

    `fallback_json` has to reach the OpenRouter payload, since without it the
    free models returned unparseable JSON on nearly every measured attempt. And
    it must never reach the Anthropic SDK, which rejects an unknown keyword:
    forwarding it would break the primary call in order to improve the fallback.
    """
    monkeypatch.setattr(
        llm_json, "get_settings", lambda: _FakeSettings(openrouter_api_key="or-key")
    )
    first = llm_json._OPENROUTER_MODELS[0]
    fake_http = _LadderHTTPClient(succeed_on=first)
    monkeypatch.setattr(llm_json.httpx, "AsyncClient", lambda **_kw: fake_http)

    messages = _ManifestMessages(_sustained_429s())
    await llm_json.create_message(
        _ManifestClient(messages), **_TAILOR_KWARGS, fallback_json=True
    )

    assert fake_http.payloads[0]["response_format"] == {"type": "json_object"}
    # The SDK saw the real request and nothing invented for the fallback.
    assert "fallback_json" not in messages.seen_kwargs


@pytest.mark.asyncio
async def test_a_prose_caller_does_not_get_json_mode(
    monkeypatch: pytest.MonkeyPatch, gateway_waits: list[float]
) -> None:
    """`latex_from_document` asks this function for LaTeX, not an object."""
    monkeypatch.setattr(
        llm_json, "get_settings", lambda: _FakeSettings(openrouter_api_key="or-key")
    )
    fake_http = _LadderHTTPClient(succeed_on=llm_json._OPENROUTER_MODELS[0])
    monkeypatch.setattr(llm_json.httpx, "AsyncClient", lambda **_kw: fake_http)

    await llm_json.create_message(
        _ManifestClient(_ManifestMessages(_sustained_429s())), **_TAILOR_KWARGS
    )

    assert "response_format" not in fake_http.payloads[0]
