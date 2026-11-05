"""The one place this codebase talks to a language model.

Every agent here used to call Anthropic's Messages API through the Manifest
gateway, which picked a model from an `x-manifest-tier` header. Manifest Cloud
stopped routing free accounts (every request now fails with M204, "needs a Pro
subscription"), and its Claude route depended on a Claude subscription, which
Anthropic's terms do not allow to be routed through a third-party gateway. So
the gateway is gone and this module calls the providers directly:

    tier      primary                                   then
    fast      OpenRouter nvidia/nemotron-3.5-lightning  OpenAI gpt-6-luna
    quality   OpenAI gpt-6.1-terra                      OpenRouter free ladder
    tailor    OpenAI gpt-6.1-terra                      OpenRouter free ladder

The tiers map one to one onto the old Manifest routes: `job-os-fast` is
`fast`, `job-os-quality` (template builds) is `quality`, and `job-os-sonnet`
(tailoring, review, cover letters, interview prep, outreach, enrichment) is
`tailor`. Every model id is a setting, so `OPENAI_MODEL_TAILOR=gpt-6.1-sol`
moves the tailor tier to the stronger model without a code change.

Both providers speak OpenAI Chat Completions, so one request builder and one
response parser serve both. Callers still describe a request the way they did
for Anthropic (`system`, `messages` with text or base64 PDF blocks,
`max_tokens`, an optional `effort`) and still get back an object with
`content` blocks, `stop_reason` and `usage`, which is what `llm_json`'s
`response_text` and `response_diagnostics` read. That kept the change out of
every prompt and every parser.

GPT-6 request rules, checked against developers.openai.com on 2026-11-05:

- `temperature` and `top_p` are rejected unless `reasoning_effort` is `none`.
  Nothing here sends either.
- `max_completion_tokens`, not the deprecated `max_tokens`, and it counts
  reasoning tokens too, so a reasoning request gets headroom on top of what the
  caller asked for.
- The default effort is `medium`, not `none`, so the effort is always sent.
  `gpt-6.1-sol` and `gpt-6-astra` do not accept `none`; they get `low`.
"""

from __future__ import annotations

import asyncio
import json
import random
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx
import structlog

from job_os.settings import Settings, get_settings

log = structlog.get_logger(__name__)

LLMTier = Literal["fast", "quality", "tailor"]
Provider = Literal["openai", "openrouter"]
TIERS: tuple[LLMTier, ...] = ("fast", "quality", "tailor")

# ---------------------------------------------------------------------------
# Errors. Same shape as the Anthropic SDK's, so the callers' except clauses
# translated one for one: APIError -> LLMError, APIStatusError ->
# LLMStatusError, APIConnectionError -> LLMConnectionError, APITimeoutError ->
# LLMTimeoutError.


class LLMError(Exception):
    """Any failure to get a reply from a model provider."""


class LLMStatusError(LLMError):
    """The provider answered with an error status."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        provider: str,
        model: str,
        code: str | None = None,
        headers: httpx.Headers | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.provider = provider
        self.model = model
        self.code = code
        self.headers = headers or httpx.Headers()


class LLMConnectionError(LLMError):
    """The request never got a complete answer: refused, reset, or dropped."""


class LLMTimeoutError(LLMConnectionError, TimeoutError):
    """The provider stopped answering inside the time allowed.

    Also a `TimeoutError`, so callers that already catch the builtin (the
    tailor's soft-fail branches, jd_parse's retry) keep catching it.
    """


class UntranslatableRequestError(LLMError):
    """The request carries content this route cannot take (a PDF on a
    text-only model). The route is skipped rather than sent something else."""


# ---------------------------------------------------------------------------
# Reply. Mirrors the parts of an Anthropic `Message` this codebase reads.


@dataclass(frozen=True)
class TextBlock:
    text: str
    type: str = "text"


@dataclass(frozen=True)
class LLMUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    # OpenAI caches prompts on its own and reports reads, never writes.
    cache_creation_input_tokens: int | None = None
    reasoning_tokens: int | None = None


@dataclass(frozen=True)
class LLMReply:
    content: list[TextBlock]
    stop_reason: str
    usage: LLMUsage = field(default_factory=LLMUsage)
    provider: str = ""
    model: str = ""

    @property
    def text(self) -> str:
        return "".join(block.text for block in self.content if block.type == "text")


# OpenAI `finish_reason` -> the Anthropic `stop_reason` vocabulary callers
# compare against (`latex_from_document` checks `== "max_tokens"` directly).
_FINISH_REASON_TO_STOP_REASON: dict[str, str] = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "content_filter": "refusal",
}


# ---------------------------------------------------------------------------
# Routing.


@dataclass(frozen=True)
class Route:
    provider: Provider
    model: str


# The emergency ladder behind the paid tiers, kept from the Manifest days when
# it sat behind the gateway. Free first; the paid DeepSeek rung last because a
# fallback that bills a fraction of a cent beats one that is absent.
# Benchmarked 2026-08-29 on a real 5.4KB iCIMS posting extracting the full
# ParsedJD schema: minimax-m3:free validated 4/4 in 3.6-4.9s,
# nemotron-3-super:free 2/4 in 19-43s.
OPENROUTER_FALLBACK_MODELS: tuple[str, ...] = (
    "minimax/minimax-m3:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "deepseek/deepseek-v4-flash-0731",
)


def routes_for_tier(settings: Settings, tier: LLMTier) -> list[Route]:
    """The providers a tier tries, in order, limited to the ones with a key."""
    has_openai = bool(settings.openai_api_key)
    has_openrouter = bool(settings.openrouter_api_key)
    routes: list[Route] = []
    if tier == "fast":
        if has_openrouter:
            routes.append(Route("openrouter", settings.openrouter_model_fast))
        if has_openai:
            routes.append(Route("openai", settings.openai_model_fast_fallback))
        return routes
    if tier == "quality":
        primary = settings.openai_model_quality
    elif tier == "tailor":
        primary = settings.openai_model_tailor
    else:  # pragma: no cover - Literal keeps this unreachable for typed callers
        raise ValueError(f"unknown LLM tier: {tier!r}")
    if has_openai:
        routes.append(Route("openai", primary))
    if has_openrouter:
        routes.extend(Route("openrouter", model) for model in OPENROUTER_FALLBACK_MODELS)
    return routes


def llm_configured(settings: Any) -> bool:
    """Whether any provider key is set. Takes `Any` so a test's namespace works."""
    return bool(
        getattr(settings, "openai_api_key", None) or getattr(settings, "openrouter_api_key", None)
    )


# ---------------------------------------------------------------------------
# OpenAI request rules.

REASONING_EFFORTS: tuple[str, ...] = ("none", "low", "medium", "high", "xhigh", "max")
# Model ids (prefixes) that reject `reasoning_effort: none`.
_NO_NONE_EFFORT_PREFIXES: tuple[str, ...] = ("gpt-6.1-sol", "gpt-6-astra")
# Reasoning tokens count against `max_completion_tokens`. A caller's budget was
# sized for the answer, so reasoning gets room on top of it rather than eating
# into the JSON.
_REASONING_HEADROOM: dict[str, int] = {
    "none": 0,
    "low": 4096,
    "medium": 8192,
    "high": 16384,
    "xhigh": 32768,
    "max": 32768,
}
# Every GPT-6 model lists 128,000 max output tokens.
_OPENAI_MAX_COMPLETION_TOKENS = 128_000
# A free fallback is a best-effort emergency draft; cap what it is asked for.
_OPENROUTER_MAX_TOKENS_CEILING = 16_000


def supports_no_reasoning(model: str) -> bool:
    return not model.startswith(_NO_NONE_EFFORT_PREFIXES)


def resolve_reasoning_effort(model: str, requested: str | None) -> str:
    """The effort to send. Unset means `none` (fast, and what the old
    non-thinking calls did), raised to `low` where the model has no `none`."""
    effort = (requested or "none").strip().lower()
    if effort == "minimal":
        effort = "low"
    if effort not in REASONING_EFFORTS:
        raise ValueError(f"unknown reasoning effort {requested!r}")
    if effort == "none" and not supports_no_reasoning(model):
        effort = "low"
    return effort


def _block_field(block: Any, name: str) -> Any:
    return block.get(name) if isinstance(block, dict) else getattr(block, name, None)


def _content_parts(content: Any, *, provider: Provider) -> list[dict[str, Any]]:
    """Anthropic-shaped content -> Chat Completions content parts."""
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if not isinstance(content, list):
        raise UntranslatableRequestError(f"unsupported content shape: {type(content)!r}")
    parts: list[dict[str, Any]] = []
    for block in content:
        block_type = _block_field(block, "type")
        if block_type == "text":
            parts.append({"type": "text", "text": _block_field(block, "text") or ""})
            continue
        if block_type in ("document", "image") and provider == "openai":
            source = _block_field(block, "source") or {}
            if _block_field(source, "type") != "base64":
                raise UntranslatableRequestError(f"unsupported {block_type} source")
            media_type = _block_field(source, "media_type") or ""
            data_url = f"data:{media_type};base64,{_block_field(source, 'data') or ''}"
            if block_type == "image":
                parts.append({"type": "image_url", "image_url": {"url": data_url}})
            elif media_type == "application/pdf":
                parts.append(
                    {"type": "file", "file": {"filename": "upload.pdf", "file_data": data_url}}
                )
            else:
                raise UntranslatableRequestError(f"unsupported document type {media_type!r}")
            continue
        # The free OpenRouter models are text only, so a PDF there is skipped
        # rather than silently dropped from the prompt.
        raise UntranslatableRequestError(f"unsupported content block type: {block_type!r}")
    return parts


def _as_text(parts: list[dict[str, Any]]) -> str:
    return "".join(part.get("text", "") for part in parts)


def to_chat_messages(
    system: Any, messages: Sequence[Any], *, provider: Provider
) -> list[dict[str, Any]]:
    """Translate Anthropic-style `system` + `messages` into Chat Completions."""
    chat: list[dict[str, Any]] = []
    if system:
        chat.append(
            {"role": "system", "content": _as_text(_content_parts(system, provider=provider))}
        )
    for entry in messages:
        role = _block_field(entry, "role")
        parts = _content_parts(_block_field(entry, "content"), provider=provider)
        if role == "user" and any(part["type"] != "text" for part in parts):
            chat.append({"role": role, "content": parts})
        else:
            # Plain strings everywhere else: every provider accepts them, and
            # assistant turns may not carry file parts anyway.
            chat.append({"role": role, "content": _as_text(parts)})
    return chat


def build_payload(
    route: Route,
    *,
    system: Any,
    messages: Sequence[Any],
    max_tokens: int,
    effort: str | None,
    json_object: bool,
) -> dict[str, Any]:
    """The request body for one route."""
    payload: dict[str, Any] = {
        "model": route.model,
        "messages": to_chat_messages(system, messages, provider=route.provider),
    }
    if route.provider == "openai":
        resolved = resolve_reasoning_effort(route.model, effort)
        payload["reasoning_effort"] = resolved
        payload["max_completion_tokens"] = min(
            max_tokens + _REASONING_HEADROOM[resolved], _OPENAI_MAX_COMPLETION_TOKENS
        )
    else:
        payload["max_tokens"] = min(max_tokens, _OPENROUTER_MAX_TOKENS_CEILING)
    if json_object:
        payload["response_format"] = {"type": "json_object"}
    return payload


def parse_chat_completion(data: dict[str, Any], *, route: Route) -> LLMReply:
    """A Chat Completions body -> LLMReply."""
    choices = data.get("choices") or []
    if not choices:
        raise LLMError(f"{route.provider} returned no choices for {route.model}")
    choice = choices[0] or {}
    message = choice.get("message") or {}
    text = message.get("content") or ""
    if isinstance(text, list):  # some providers return content parts
        text = "".join(part.get("text", "") for part in text if isinstance(part, dict))
    finish_reason = choice.get("finish_reason")
    stop_reason = _FINISH_REASON_TO_STOP_REASON.get(
        finish_reason or "", finish_reason or "end_turn"
    )
    if not text and message.get("refusal"):
        stop_reason = "refusal"
    usage = data.get("usage") or {}
    prompt_details = usage.get("prompt_tokens_details") or {}
    completion_details = usage.get("completion_tokens_details") or {}
    return LLMReply(
        content=[TextBlock(text)],
        stop_reason=stop_reason,
        usage=LLMUsage(
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            cache_read_input_tokens=prompt_details.get("cached_tokens"),
            reasoning_tokens=completion_details.get("reasoning_tokens"),
        ),
        provider=route.provider,
        model=data.get("model") or route.model,
    )


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def _is_json_object(text: str) -> bool:
    fenced = _FENCE_RE.search(text)
    body = (fenced.group(1) if fenced else text).strip()
    start, end = body.find("{"), body.rfind("}")
    if start == -1 or end <= start:
        return False
    try:
        return isinstance(json.loads(body[start : end + 1]), dict)
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Retry policy.

# Waits before asking a paid provider again after a 429 or an overload. Kept
# from the Manifest days: a sustained rate limit clears on the order of tens of
# seconds, and these add at most ~75s to a call that would otherwise fail.
_RETRY_BACKOFF_SECONDS: tuple[float, ...] = (8.0, 20.0, 45.0)
_MAX_RETRY_AFTER_SECONDS = 60.0
# The request was never processed, so re-sending is not a duplicate.
_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504, 529})
# A 429 that will not clear by waiting: the account is out of credit.
_NON_RETRYABLE_CODES = frozenset({"insufficient_quota", "billing_hard_limit_reached"})
# The Appwrite function is killed at 900s, so one call may not outlive 600s.
DEFAULT_TIMEOUT_SECONDS = 600.0
_OPENROUTER_TIMEOUT_SECONDS = 120.0
# Indirected so tests can skip the real waits by patching this name.
_sleep = asyncio.sleep


def _retry_after_seconds(exc: LLMStatusError) -> float | None:
    raw = exc.headers.get("retry-after")
    if not raw:
        return None
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    return min(seconds, _MAX_RETRY_AFTER_SECONDS)


def _is_retryable(exc: LLMError) -> bool:
    if isinstance(exc, LLMTimeoutError):
        # A provider that stopped answering is unlikely to answer a second
        # identical request, and retrying risks the 900s hard kill.
        return False
    if isinstance(exc, LLMConnectionError):
        return True
    if isinstance(exc, LLMStatusError):
        return exc.status_code in _RETRYABLE_STATUSES and exc.code not in _NON_RETRYABLE_CODES
    return False


def _error_from_response(response: httpx.Response, route: Route) -> LLMStatusError:
    code: str | None = None
    detail = response.text[:300]
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        err = body["error"]
        code = err.get("code") if isinstance(err.get("code"), str) else None
        detail = str(err.get("message") or detail)[:300]
    return LLMStatusError(
        f"{route.provider} {route.model} returned {response.status_code}: {detail}",
        status_code=response.status_code,
        provider=route.provider,
        model=route.model,
        code=code,
        headers=response.headers,
    )


class LLMClient:
    """Sends one completion per call, across the routes of a tier.

    `transport` exists for tests (an `httpx.MockTransport`); production leaves
    it unset.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        timeout: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._timeout = timeout or DEFAULT_TIMEOUT_SECONDS
        self._transport = transport

    def _endpoint(self, route: Route) -> tuple[str, dict[str, str]]:
        s = self._settings
        if route.provider == "openai":
            base, key = s.openai_base_url, s.openai_api_key
        else:
            base, key = s.openrouter_base_url, s.openrouter_api_key
        return (
            f"{base.rstrip('/')}/chat/completions",
            {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )

    def _route_timeout(self, route: Route, *, has_next: bool) -> float:
        """A free model gets at most two minutes, and at most half the budget
        when a paid route is waiting behind it, so a hung free model cannot
        spend a short caller budget (jd_parse allows 25s) on its own."""
        seconds = self._timeout
        if route.provider == "openrouter":
            seconds = min(seconds, _OPENROUTER_TIMEOUT_SECONDS)
            if has_next:
                seconds = min(seconds, self._timeout / 2)
        return seconds

    async def _send(self, route: Route, payload: dict[str, Any], *, seconds: float) -> LLMReply:
        url, headers = self._endpoint(route)
        timeout = httpx.Timeout(seconds, connect=min(10.0, seconds))
        try:
            async with httpx.AsyncClient(timeout=timeout, transport=self._transport) as http:
                # A wall clock as well as httpx's own: its read timeout resets on
                # every byte, so a provider trickling keep-alives never trips it.
                response = await asyncio.wait_for(
                    http.post(url, headers=headers, json=payload), timeout=seconds
                )
        except (httpx.TimeoutException, TimeoutError) as exc:
            raise LLMTimeoutError(f"{route.provider} {route.model} timed out: {exc!r}") from exc
        except httpx.TransportError as exc:
            raise LLMConnectionError(
                f"{route.provider} {route.model} connection failed: {exc!r}"
            ) from exc
        if response.status_code >= 400:
            raise _error_from_response(response, route)
        try:
            data = response.json()
        except ValueError as exc:
            raise LLMError(f"{route.provider} {route.model} returned non-JSON") from exc
        return parse_chat_completion(data, route=route)

    async def _attempt_route(
        self, route: Route, payload: dict[str, Any], *, patient: bool, seconds: float
    ) -> LLMReply:
        """One route. A patient route (paid OpenAI) waits out a rate limit or
        a dropped connection; an impatient one (free OpenRouter, which answers
        429 within a tenth of a second when busy) fails over at once."""
        schedule: tuple[float | None, ...] = (*_RETRY_BACKOFF_SECONDS, None) if patient else (None,)
        for attempt, backoff in enumerate(schedule):
            try:
                return await self._send(route, payload, seconds=seconds)
            except LLMError as exc:
                if backoff is None or not _is_retryable(exc):
                    raise
                wait = backoff * random.uniform(0.8, 1.2)  # noqa: S311 - jitter, not security
                if isinstance(exc, LLMStatusError):
                    wait = _retry_after_seconds(exc) or wait
                log.warning(
                    "llm.retrying",
                    provider=route.provider,
                    model=route.model,
                    attempt=attempt + 1,
                    waiting_seconds=round(wait, 1),
                    error=repr(exc)[:200],
                )
                await _sleep(wait)
        raise AssertionError("unreachable")  # pragma: no cover

    async def complete(
        self,
        *,
        tier: LLMTier,
        max_tokens: int,
        messages: Sequence[Any],
        system: Any = None,
        effort: str | None = None,
        json_object: bool = False,
        routes: Sequence[Route] | None = None,
    ) -> LLMReply:
        """Ask the tier's routes in order until one answers.

        Fails over when a route errors, cannot take the request (a PDF on a
        text-only model), or, on OpenRouter, answers with nothing or with
        something that is not the JSON object asked for. An OpenAI reply is
        returned as it came: callers already handle an empty or chatty reply
        with their own corrective retry. When every route fails, the error
        raised is the first OpenAI route's, since that is the one worth
        reading; a timeout on OpenAI is raised at once.
        """
        candidates = list(routes) if routes is not None else routes_for_tier(self._settings, tier)
        if not candidates:
            raise LLMError(
                f"No LLM provider is configured for the {tier!r} tier: set OPENAI_API_KEY"
                + (" or OPENROUTER_API_KEY" if tier == "fast" else "")
            )
        primary_error: LLMError | None = None
        last_error: LLMError | None = None
        for index, route in enumerate(candidates):
            has_next = index < len(candidates) - 1
            try:
                payload = build_payload(
                    route,
                    system=system,
                    messages=messages,
                    max_tokens=max_tokens,
                    effort=effort,
                    json_object=json_object,
                )
            except UntranslatableRequestError as exc:
                log.info(
                    "llm.route_skipped", provider=route.provider, model=route.model, reason=str(exc)
                )
                last_error = exc
                continue
            try:
                reply = await self._attempt_route(
                    route,
                    payload,
                    patient=route.provider == "openai",
                    seconds=self._route_timeout(route, has_next=has_next),
                )
            except LLMError as exc:
                last_error = exc
                if route.provider == "openai":
                    primary_error = primary_error or exc
                    if isinstance(exc, LLMTimeoutError):
                        raise
                log.warning(
                    "llm.route_failed",
                    provider=route.provider,
                    model=route.model,
                    tier=tier,
                    error=repr(exc)[:200],
                    failing_over=has_next,
                )
                continue
            if route.provider == "openrouter":
                # A 200 carrying nothing usable is a failure with the wrong
                # status, so the next rung still runs.
                if not reply.text.strip():
                    log.info("llm.route_empty", provider=route.provider, model=route.model)
                    continue
                if json_object and not _is_json_object(reply.text):
                    log.info("llm.route_not_json", provider=route.provider, model=route.model)
                    continue
            if index > 0:
                log.warning(
                    "llm.fallback_served", provider=route.provider, model=route.model, tier=tier
                )
            return reply
        error = primary_error or last_error
        if error is None:  # every route answered unusably and none raised
            error = LLMError(f"No route for the {tier!r} tier returned a usable reply")
        raise error


def openrouter_fallback_routes(settings: Settings) -> list[Route]:
    """The free-first OpenRouter ladder, for callers that ask it directly."""
    if not settings.openrouter_api_key:
        return []
    return [Route("openrouter", model) for model in OPENROUTER_FALLBACK_MODELS]


# One chat message in the Anthropic-style shape callers build: a role and
# either a string or a list of content blocks. Replaces
# `anthropic.types.MessageParam` in type hints.
MessageParam = dict[str, Any]
