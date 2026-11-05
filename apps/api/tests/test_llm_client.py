"""The direct OpenAI / OpenRouter client that replaced the Manifest gateway.

Everything goes through `httpx.MockTransport`, so these tests exercise the real
request builder, response parser, retry schedule and failover, and only the
network is fake. Replaces test_gateway_retry.py and test_llm_fallback.py, which
tested the same behaviour against the Anthropic SDK's streaming client.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from job_os.services import llm_client, llm_json
from job_os.services.llm_client import (
    LLMClient,
    LLMConnectionError,
    LLMStatusError,
    LLMTimeoutError,
    Route,
    build_payload,
    resolve_reasoning_effort,
    routes_for_tier,
)
from job_os.settings import Settings

DB_URL = "postgresql+asyncpg://job_os:job_os@localhost/job_os"
PDF_BLOCK = {
    "type": "document",
    "source": {"type": "base64", "media_type": "application/pdf", "data": "JVBERi0x"},
}


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "database_url": DB_URL,
        "openai_api_key": "sk-test",
        "openrouter_api_key": "or-test",
    }
    values.update(overrides)
    return Settings(**values)


def _completion(
    text: str = '{"ok": true}', *, finish_reason: str = "stop", model: str = "served-model"
) -> dict[str, Any]:
    return {
        "model": model,
        "choices": [
            {"message": {"role": "assistant", "content": text}, "finish_reason": finish_reason}
        ],
        "usage": {
            "prompt_tokens": 120,
            "completion_tokens": 30,
            "prompt_tokens_details": {"cached_tokens": 100},
            "completion_tokens_details": {"reasoning_tokens": 0},
        },
    }


def _error(
    status: int, code: str | None = None, headers: dict[str, str] | None = None
) -> httpx.Response:
    body = {"error": {"message": f"status {status}", "code": code}}
    return httpx.Response(status, json=body, headers=headers)


class _Server:
    """Serves queued responses per host and records every request."""

    def __init__(self) -> None:
        self.queues: dict[str, list[Any]] = {"api.openai.com": [], "openrouter.ai": []}
        self.requests: list[tuple[str, dict[str, Any], httpx.Headers]] = []

    def queue(self, host: str, *responses: Any) -> None:
        self.queues[host].extend(responses)

    def bodies(self, host: str | None = None) -> list[dict[str, Any]]:
        return [body for h, body, _ in self.requests if host is None or h == host]

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append((request.url.host, body, request.headers))
        queue = self.queues[request.url.host]
        item = queue.pop(0) if queue else httpx.Response(200, json=_completion())
        if isinstance(item, Exception):
            raise item
        if isinstance(item, Callable) and not isinstance(item, httpx.Response):
            return item(request)
        if isinstance(item, httpx.Response):
            return item
        return httpx.Response(200, json=item)


@pytest.fixture
def server() -> _Server:
    return _Server()


def _client(server: _Server, **settings: Any) -> LLMClient:
    return LLMClient(_settings(**settings), transport=httpx.MockTransport(server.handler))


async def _ask(client: LLMClient, tier: llm_client.LLMTier = "tailor", **kwargs: Any) -> Any:
    kwargs.setdefault("max_tokens", 1000)
    kwargs.setdefault("messages", [{"role": "user", "content": "hi"}])
    return await client.complete(tier=tier, **kwargs)


# ---------------------------------------------------------------------------
# Tier mapping


def test_tiers_map_to_the_chosen_models() -> None:
    settings = _settings()
    assert routes_for_tier(settings, "fast") == [
        Route("openrouter", "nvidia/nemotron-3.5-lightning:free"),
        Route("openai", "gpt-6-luna"),
    ]
    for tier in ("quality", "tailor"):
        routes = routes_for_tier(settings, tier)
        assert routes[0] == Route("openai", "gpt-6.1-terra")
        assert {route.provider for route in routes[1:]} == {"openrouter"}
        assert [route.model for route in routes[1:]] == list(llm_client.OPENROUTER_FALLBACK_MODELS)


def test_models_are_settings() -> None:
    settings = _settings(
        openai_model_tailor="gpt-6.1-sol",
        openai_model_quality="gpt-6-sol",
        openai_model_fast_fallback="gpt-6-luna-mini",
        openrouter_model_fast="some/free:free",
    )
    assert routes_for_tier(settings, "tailor")[0].model == "gpt-6.1-sol"
    assert routes_for_tier(settings, "quality")[0].model == "gpt-6-sol"
    assert [r.model for r in routes_for_tier(settings, "fast")] == [
        "some/free:free",
        "gpt-6-luna-mini",
    ]


def test_a_missing_key_drops_its_routes() -> None:
    no_openrouter = _settings(openrouter_api_key=None)
    assert routes_for_tier(no_openrouter, "fast") == [Route("openai", "gpt-6-luna")]
    assert routes_for_tier(no_openrouter, "tailor") == [Route("openai", "gpt-6.1-terra")]
    no_openai = _settings(openai_api_key=None)
    assert routes_for_tier(no_openai, "fast") == [
        Route("openrouter", "nvidia/nemotron-3.5-lightning:free")
    ]
    assert all(r.provider == "openrouter" for r in routes_for_tier(no_openai, "tailor"))


async def test_no_key_at_all_is_a_clear_error(server: _Server) -> None:
    client = _client(server, openai_api_key=None, openrouter_api_key=None)
    with pytest.raises(llm_client.LLMError, match="OPENAI_API_KEY"):
        await _ask(client)
    assert server.requests == []


def test_llm_configured_reads_either_key() -> None:
    assert llm_client.llm_configured(_settings(openrouter_api_key=None))
    assert llm_client.llm_configured(_settings(openai_api_key=None))
    assert not llm_client.llm_configured(_settings(openai_api_key=None, openrouter_api_key=None))


# ---------------------------------------------------------------------------
# GPT-6 request rules


def test_openai_payload_follows_the_gpt6_rules() -> None:
    payload = build_payload(
        Route("openai", "gpt-6.1-terra"),
        system="rules",
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=2000,
        effort=None,
        json_object=False,
    )
    assert payload["reasoning_effort"] == "none"
    assert payload["max_completion_tokens"] == 2000
    for banned in ("max_tokens", "temperature", "top_p"):
        assert banned not in payload
    assert payload["messages"] == [
        {"role": "system", "content": "rules"},
        {"role": "user", "content": "hi"},
    ]


@pytest.mark.parametrize("model", ["gpt-6.1-sol", "gpt-6-astra"])
def test_models_without_none_get_low_and_headroom(model: str) -> None:
    assert resolve_reasoning_effort(model, None) == "low"
    assert resolve_reasoning_effort(model, "none") == "low"
    payload = build_payload(
        Route("openai", model),
        system=None,
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=2000,
        effort=None,
        json_object=False,
    )
    assert payload["reasoning_effort"] == "low"
    assert payload["max_completion_tokens"] > 2000


@pytest.mark.parametrize("model", ["gpt-6.1-terra", "gpt-6-luna", "gpt-6-sol"])
def test_models_with_none_keep_it(model: str) -> None:
    assert resolve_reasoning_effort(model, None) == "none"


def test_a_requested_effort_is_sent_with_room_for_its_reasoning() -> None:
    payload = build_payload(
        Route("openai", "gpt-6.1-terra"),
        system=None,
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=10_000,
        effort="medium",
        json_object=False,
    )
    assert payload["reasoning_effort"] == "medium"
    assert payload["max_completion_tokens"] == 10_000 + 8192


def test_the_completion_budget_is_capped_at_the_model_ceiling() -> None:
    payload = build_payload(
        Route("openai", "gpt-6.1-terra"),
        system=None,
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=127_000,
        effort="high",
        json_object=False,
    )
    assert payload["max_completion_tokens"] == 128_000


def test_an_unknown_effort_is_rejected() -> None:
    with pytest.raises(ValueError, match="reasoning effort"):
        resolve_reasoning_effort("gpt-6.1-terra", "extreme")


def test_openrouter_payload_uses_max_tokens_and_caps_it() -> None:
    payload = build_payload(
        Route("openrouter", "nvidia/nemotron-3.5-lightning:free"),
        system=None,
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=48_000,
        effort="medium",
        json_object=True,
    )
    assert payload["max_tokens"] == 16_000
    assert "reasoning_effort" not in payload
    assert "max_completion_tokens" not in payload
    assert payload["response_format"] == {"type": "json_object"}


def test_a_pdf_becomes_a_file_part_on_openai() -> None:
    payload = build_payload(
        Route("openai", "gpt-6-luna"),
        system=[{"type": "text", "text": "parse", "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": [PDF_BLOCK, {"type": "text", "text": "go"}]}],
        max_tokens=100,
        effort=None,
        json_object=False,
    )
    system, user = payload["messages"]
    assert system == {"role": "system", "content": "parse"}
    assert user["content"] == [
        {
            "type": "file",
            "file": {"filename": "upload.pdf", "file_data": "data:application/pdf;base64,JVBERi0x"},
        },
        {"type": "text", "text": "go"},
    ]


def test_a_pdf_cannot_go_to_the_text_only_free_model() -> None:
    with pytest.raises(llm_client.UntranslatableRequestError):
        build_payload(
            Route("openrouter", "nvidia/nemotron-3.5-lightning:free"),
            system=None,
            messages=[{"role": "user", "content": [PDF_BLOCK]}],
            max_tokens=100,
            effort=None,
            json_object=False,
        )


def test_assistant_and_text_only_turns_are_plain_strings() -> None:
    payload = build_payload(
        Route("openai", "gpt-6.1-terra"),
        system=None,
        messages=[
            {
                "role": "user",
                "content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}],
            },
            {"role": "assistant", "content": "(empty)"},
            {"role": "user", "content": "again"},
        ],
        max_tokens=100,
        effort=None,
        json_object=False,
    )
    assert payload["messages"] == [
        {"role": "user", "content": "ab"},
        {"role": "assistant", "content": "(empty)"},
        {"role": "user", "content": "again"},
    ]


# ---------------------------------------------------------------------------
# Wire format and response parsing


async def test_openai_request_goes_to_openai_with_bearer_auth(server: _Server) -> None:
    reply = await _ask(_client(server), system="rules")
    host, body, headers = server.requests[0]
    assert host == "api.openai.com"
    assert headers["authorization"] == "Bearer sk-test"
    assert body["model"] == "gpt-6.1-terra"
    assert "x-manifest-tier" not in headers
    assert reply.text == '{"ok": true}'
    assert llm_json.response_text(reply) == '{"ok": true}'
    assert reply.stop_reason == "end_turn"
    assert reply.usage.input_tokens == 120
    assert reply.usage.output_tokens == 30
    assert reply.usage.cache_read_input_tokens == 100
    assert reply.model == "served-model"


async def test_a_truncated_reply_reports_max_tokens(server: _Server) -> None:
    server.queue("api.openai.com", _completion('{"cut', finish_reason="length"))
    reply = await _ask(_client(server))
    assert reply.stop_reason == "max_tokens"
    diagnostics = llm_json.response_diagnostics(reply)
    assert diagnostics == {
        "stop_reason": "max_tokens",
        "block_types": ["text"],
        "output_tokens": 30,
    }


async def test_a_refusal_is_reported_as_one(server: _Server) -> None:
    server.queue(
        "api.openai.com",
        {"choices": [{"message": {"content": None, "refusal": "no"}, "finish_reason": "stop"}]},
    )
    reply = await _ask(_client(server))
    assert reply.text == ""
    assert reply.stop_reason == "refusal"


async def test_create_message_passes_everything_through(server: _Server) -> None:
    reply = await llm_json.create_message(
        _client(server),
        tier="tailor",
        max_tokens=500,
        system="s",
        messages=[{"role": "user", "content": "u"}],
        effort="low",
        json_object=True,
    )
    body = server.bodies()[0]
    assert body["reasoning_effort"] == "low"
    assert body["response_format"] == {"type": "json_object"}
    assert reply.text


# ---------------------------------------------------------------------------
# Retries on OpenAI


async def test_a_rate_limit_is_retried_and_the_call_succeeds(
    server: _Server, gateway_waits: list[float]
) -> None:
    server.queue("api.openai.com", _error(429), _error(429))
    reply = await _ask(_client(server, openrouter_api_key=None))
    assert reply.text
    assert len(gateway_waits) == 2
    assert len(server.requests) == 3


@pytest.mark.parametrize("status", [500, 502, 503, 504])
async def test_a_server_error_is_retried(
    server: _Server, gateway_waits: list[float], status: int
) -> None:
    server.queue("api.openai.com", _error(status))
    assert (await _ask(_client(server, openrouter_api_key=None))).text
    assert len(gateway_waits) == 1


async def test_a_bad_request_is_not_retried(server: _Server, gateway_waits: list[float]) -> None:
    server.queue("api.openai.com", _error(400, code="unsupported_parameter"))
    with pytest.raises(LLMStatusError) as excinfo:
        await _ask(_client(server, openrouter_api_key=None))
    assert excinfo.value.status_code == 400
    assert excinfo.value.code == "unsupported_parameter"
    assert gateway_waits == []


async def test_out_of_credit_is_not_waited_on(server: _Server, gateway_waits: list[float]) -> None:
    server.queue("api.openai.com", _error(429, code="insufficient_quota"))
    with pytest.raises(LLMStatusError):
        await _ask(_client(server, openrouter_api_key=None))
    assert gateway_waits == []
    assert len(server.requests) == 1


async def test_the_retry_budget_is_bounded(server: _Server, gateway_waits: list[float]) -> None:
    server.queue("api.openai.com", *[_error(429) for _ in range(10)])
    with pytest.raises(LLMStatusError):
        await _ask(_client(server, openrouter_api_key=None))
    assert len(gateway_waits) == len(llm_client._RETRY_BACKOFF_SECONDS)
    assert len(server.requests) == len(llm_client._RETRY_BACKOFF_SECONDS) + 1


async def test_retry_after_wins_and_is_capped(server: _Server, gateway_waits: list[float]) -> None:
    server.queue(
        "api.openai.com",
        _error(429, headers={"retry-after": "3"}),
        _error(429, headers={"retry-after": "9999"}),
        _error(429, headers={"retry-after": "soon"}),
    )
    await _ask(_client(server, openrouter_api_key=None))
    assert gateway_waits[0] == 3.0
    assert gateway_waits[1] == llm_client._MAX_RETRY_AFTER_SECONDS
    low, high = 0.8 * 45.0, 1.2 * 45.0
    assert low <= gateway_waits[2] <= high


async def test_a_clean_call_never_sleeps(server: _Server, gateway_waits: list[float]) -> None:
    await _ask(_client(server))
    assert gateway_waits == []


async def test_a_dropped_connection_is_retried(server: _Server, gateway_waits: list[float]) -> None:
    server.queue("api.openai.com", httpx.ReadError("", request=None))  # type: ignore[arg-type]
    assert (await _ask(_client(server, openrouter_api_key=None))).text
    assert len(gateway_waits) == 1


async def test_a_dropped_connection_still_gives_up_eventually(
    server: _Server, gateway_waits: list[float]
) -> None:
    server.queue("api.openai.com", *[httpx.ConnectError("refused") for _ in range(10)])
    with pytest.raises(LLMConnectionError):
        await _ask(_client(server, openrouter_api_key=None))
    assert len(gateway_waits) == len(llm_client._RETRY_BACKOFF_SECONDS)


async def test_a_timeout_is_not_retried_and_not_failed_over(
    server: _Server, gateway_waits: list[float]
) -> None:
    server.queue("api.openai.com", httpx.ReadTimeout("slow"))
    with pytest.raises(LLMTimeoutError) as excinfo:
        await _ask(_client(server))
    # Still a TimeoutError, which the tailor's soft-fail branches catch.
    assert isinstance(excinfo.value, TimeoutError)
    assert gateway_waits == []
    assert server.bodies("openrouter.ai") == []


async def test_a_call_that_never_finishes_is_cut_off(server: _Server) -> None:
    async def hang(_request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(10)
        return httpx.Response(200, json=_completion())

    client = LLMClient(_settings(), timeout=0.05, transport=httpx.MockTransport(hang))
    with pytest.raises(LLMTimeoutError):
        await _ask(client)


# ---------------------------------------------------------------------------
# Failover


async def test_fast_tier_uses_the_free_model_first(server: _Server) -> None:
    await _ask(_client(server), tier="fast")
    assert [h for h, _, _ in server.requests] == ["openrouter.ai"]
    _, body, headers = server.requests[0]
    assert body["model"] == "nvidia/nemotron-3.5-lightning:free"
    assert headers["authorization"] == "Bearer or-test"


async def test_a_busy_free_model_fails_over_to_luna_at_once(
    server: _Server, gateway_waits: list[float]
) -> None:
    server.queue("openrouter.ai", _error(429))
    reply = await _ask(_client(server), tier="fast")
    assert reply.provider == "openai"
    assert server.bodies("api.openai.com")[0]["model"] == "gpt-6-luna"
    assert gateway_waits == []


async def test_a_free_reply_that_is_not_json_fails_over_when_json_was_asked(
    server: _Server,
) -> None:
    server.queue("openrouter.ai", _completion('{"truncated": '))
    reply = await _ask(_client(server), tier="fast", json_object=True)
    assert reply.provider == "openai"


async def test_a_free_reply_in_prose_is_kept_when_json_was_not_asked(server: _Server) -> None:
    server.queue("openrouter.ai", _completion("plain prose"))
    reply = await _ask(_client(server), tier="fast")
    assert reply.provider == "openrouter"
    assert reply.text == "plain prose"


async def test_an_empty_free_reply_fails_over(server: _Server) -> None:
    server.queue("openrouter.ai", _completion(""))
    reply = await _ask(_client(server), tier="fast")
    assert reply.provider == "openai"


async def test_a_pdf_on_the_fast_tier_skips_the_free_model(server: _Server) -> None:
    await _ask(
        _client(server),
        tier="fast",
        messages=[{"role": "user", "content": [PDF_BLOCK, {"type": "text", "text": "go"}]}],
    )
    assert server.bodies("openrouter.ai") == []
    assert server.bodies("api.openai.com")[0]["model"] == "gpt-6-luna"


async def test_an_openai_failure_falls_to_the_free_ladder(
    server: _Server, gateway_waits: list[float]
) -> None:
    server.queue("api.openai.com", _error(401, code="invalid_api_key"))
    reply = await _ask(_client(server), tier="tailor")
    assert reply.provider == "openrouter"
    assert server.bodies("openrouter.ai")[0]["model"] == llm_client.OPENROUTER_FALLBACK_MODELS[0]
    assert gateway_waits == []


async def test_a_sustained_rate_limit_is_waited_out_before_failing_over(
    server: _Server, gateway_waits: list[float]
) -> None:
    server.queue("api.openai.com", *[_error(429) for _ in range(4)])
    reply = await _ask(_client(server), tier="quality")
    assert reply.provider == "openrouter"
    assert len(gateway_waits) == len(llm_client._RETRY_BACKOFF_SECONDS)


async def test_the_free_ladder_is_walked_rung_by_rung(server: _Server) -> None:
    server.queue("api.openai.com", _error(400))
    server.queue("openrouter.ai", _error(429), _completion(""))
    reply = await _ask(_client(server), tier="tailor")
    models = [body["model"] for body in server.bodies("openrouter.ai")]
    assert models == list(llm_client.OPENROUTER_FALLBACK_MODELS)
    assert reply.model == "served-model"


async def test_when_everything_fails_the_openai_error_is_raised(server: _Server) -> None:
    server.queue("api.openai.com", _error(400, code="bad_thing"))
    server.queue("openrouter.ai", *[_error(503) for _ in llm_client.OPENROUTER_FALLBACK_MODELS])
    with pytest.raises(LLMStatusError) as excinfo:
        await _ask(_client(server), tier="tailor")
    assert excinfo.value.provider == "openai"
    assert excinfo.value.code == "bad_thing"


async def test_an_explicit_route_list_is_used_as_given(server: _Server) -> None:
    routes = llm_client.openrouter_fallback_routes(_settings())
    await _ask(_client(server), tier="fast", routes=routes)
    assert server.bodies("api.openai.com") == []
    assert server.bodies("openrouter.ai")[0]["model"] == routes[0].model


async def test_complete_json_via_openrouter_needs_its_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_json, "get_settings", lambda: _settings(openrouter_api_key=None))
    assert await llm_json.complete_json_via_openrouter(system="s", user="u", max_tokens=10) is None


async def test_complete_json_via_openrouter_returns_the_text(
    monkeypatch: pytest.MonkeyPatch, server: _Server
) -> None:
    monkeypatch.setattr(llm_json, "get_settings", _settings)
    real = llm_client.LLMClient

    def patched(settings: Settings | None = None, **kwargs: Any) -> LLMClient:
        return real(settings, transport=httpx.MockTransport(server.handler), **kwargs)

    monkeypatch.setattr(llm_client, "LLMClient", patched)
    text = await llm_json.complete_json_via_openrouter(system="s", user="u", max_tokens=10)
    assert text == '{"ok": true}'
    assert server.bodies("api.openai.com") == []
    assert server.bodies("openrouter.ai")[0]["response_format"] == {"type": "json_object"}


async def test_a_hung_free_model_leaves_time_for_the_paid_fallback(server: _Server) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "openrouter.ai":
            await asyncio.sleep(10)
        return httpx.Response(200, json=_completion())

    client = LLMClient(_settings(), timeout=0.2, transport=httpx.MockTransport(handler))
    started = asyncio.get_running_loop().time()
    reply = await _ask(client, tier="fast")
    assert reply.provider == "openai"
    # The free model was cut off at half the budget, not the whole of it.
    assert asyncio.get_running_loop().time() - started < 0.2
