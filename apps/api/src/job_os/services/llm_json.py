"""Read JSON out of a model reply that may not be pure JSON.

Every agent here asks Claude for a single JSON object and validates it with
pydantic. Models comply almost always, and then occasionally answer
conversationally instead, which used to fail the whole request:

    1 validation error for RevisionOutput
      Invalid JSON: expected value at line 1 column 1
      [input_value='**Assistant message:**\\n... Review" action myself']

A chatty reply is a recoverable event, not a user-facing error, so callers
extract the object from whatever came back and, when that genuinely is not
JSON, ask once more with a corrective instruction.
"""
from __future__ import annotations

import asyncio
import random
import re
from typing import Any

import anthropic
import httpx
import structlog
from pydantic import BaseModel

from job_os.settings import Settings, get_settings

log = structlog.get_logger(__name__)

# Prefer the contents of a fenced block when there is one: a reply shaped like
# "Sure, here you go: ```json {...}```" hides the object inside the fence.
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)

JSON_ONLY_RETRY = (
    "That reply was not valid JSON, so it could not be used. Send the same "
    "content again as one raw JSON object matching the schema you were given. "
    "Output the object only: no prose before or after it, no markdown fences, "
    "no explanation."
)

# An empty reply is a different problem from a chatty one, and telling a model
# that produced nothing "that was not valid JSON" does not help it. The usual
# cause is the answer running past the output ceiling, so ask for the same
# decisions carried in less text.
EMPTY_REPLY_RETRY = (
    "That reply came back empty, most likely because the answer ran past the "
    "output limit. Send the object again, complete but as compact as you can "
    "make it: keep every selected bullet, and shorten the prose fields. At most "
    "four gap_questions, one sentence each, and keep agent_note to two "
    "sentences. Output one raw JSON object only, no fences, no prose."
)


# How long to wait before asking the gateway again after it says "not now".
# The SDK's own retry gives up inside eight seconds, which is the right shape for
# a blip and the wrong shape for this failure: Manifest returns 429 with
# `fallback_exhausted`, meaning the primary model AND every fallback are rate
# limited upstream, and that clears on the order of tens of seconds. Two real
# resume_revision jobs died outright on it thirty-five seconds apart. These waits
# add at most ~75s to a call that would otherwise have failed, which is a trade a
# user waiting on a two-minute edit will take every time.
_RETRY_BACKOFF_SECONDS = (8.0, 20.0, 45.0)
# The gateway is honest about how long it needs when it says so, but a header can
# also name a wait longer than the whole operation has left. Cap it.
_MAX_RETRY_AFTER_SECONDS = 60.0
# 429 is "too many requests", 529 is Anthropic's "overloaded". Both mean the same
# thing to us: the request was never processed, so re-sending it is safe and is
# not a duplicate side effect.
_RETRYABLE_STATUSES = frozenset({429, 529})
# A stream that breaks after the gateway has already answered 200 arrives as a
# raw httpx transport error rather than an anthropic one, because
# `messages.stream` reads the body as it comes and the SDK's own retry is long
# finished by the time the socket dies. A real tailor run lost its compose pass
# to `ReadError('')` 164 seconds into the stream; no branch here matched it, so
# the whole job failed. Re-sending is safe for the same reason a 429 is: a reply
# that never finished arriving was never used.
_RETRYABLE_TRANSPORT_ERRORS = (httpx.TransportError, anthropic.APIConnectionError)
# Timeouts are deliberately excluded. The SDK's default budget is 600s and the
# Appwrite function is killed at 900s, so retrying one would trade a clean
# failure for a killed run whose job row never leaves "running". A timeout also
# means the gateway stopped answering rather than the connection glitching, which
# a second identical request is unlikely to fix.
_NON_RETRYABLE_TRANSPORT_ERRORS = (httpx.TimeoutException, anthropic.APITimeoutError)
# httpx's read timeout resets on every chunk it receives, not on real progress --
# a gateway that keeps a stream alive with periodic bytes (a keep-alive ping, a
# slow trickle) while the actual completion is stalled upstream never trips it,
# and `get_final_message()` can then hang well past the 600s this module's own
# retry-vs-timeout tradeoff assumes as the outer bound. A real resume_tailor job
# sat at "Finding the real gaps" for over 12 minutes with no error, still
# `status: "running"`, headed straight for Appwrite's 900s hard kill -- at which
# point the process is killed outright and the job row never gets the chance to
# become "failed" that the comment above this constant promises. `wait_for`
# enforces the 600s as a genuine wall-clock deadline regardless of what the
# stream is doing, and raises plain `TimeoutError`, which (like the SDK's own
# timeout errors) matches no retry branch below and propagates straight out --
# same clean, non-retried failure the comment above already argues for.
_STREAM_WALL_CLOCK_TIMEOUT_SECONDS = 600.0
# Indirected so a test can shorten the wait by patching this name alone. Patching
# `asyncio.sleep` itself would reach every coroutine in the process, which is a
# much bigger blast radius than "do not really wait forty-five seconds".
_sleep = asyncio.sleep

# ---------------------------------------------------------------------------
# Fallback provider.
#
# Tried in two places below: immediately, for any non-retryable
# `APIStatusError` (a 401 like Manifest's own OAuth-expired M102, a 403, a
# plain 500 -- Manifest itself could not serve the request at all, which is a
# stronger signal than a 429/529 blip, not a weaker one); and after Manifest's
# own retry schedule has been spent on a *sustained* 429/529, the last of the
# four attempts in `create_message`'s loop, still rate-limited or overloaded.
# `_try_fallback_providers` is a no-op (returns `None`) when nothing is
# configured or OpenRouter also fails, so trying it here costs nothing when it
# cannot help -- the caller's `raise` below is unchanged in that case. A
# transport error (dropped socket) is excluded on purpose: that is a
# connectivity blip the retry schedule above already exists to absorb, not the
# "the primary is actually unusable" case this provider is for. See
# settings.py's comment on `openrouter_api_key` for the user-visible
# consequence this fixes.
#
# Model choice: the user who asked for this called it "DeepSeek flash".
# DeepSeek's historical lineup (V3, V3.1, R1, Chat, Reasoner) never had a
# "Flash" tier -- Flash is Gemini's naming, not DeepSeek's -- so this was
# checked against OpenRouter's live catalog rather than assumed. DeepSeek in
# fact shipped a V4 line on 2026-07-31 that *does* include a "Flash" tier (a
# sparse MoE model, 13B active of 284B total parameters). Re-verified live
# 2026-08-21 against OpenRouter's machine-readable `/api/v1/models` catalog:
# still listed, $0.08/M input + $0.18/M output tokens -- cheaper than
# gpt-4o-mini, gemini-2.5-flash-lite, and claude-haiku-4.5 on the same
# catalog, and beaten only by a couple of small Qwen variants. Pinned to that
# dated snapshot (`-0731`) rather than the provider's floating "latest"
# alias, so a provider-side model swap can't silently change what this
# pipeline ships.
#
# A second fallback (Ramp Router) was built and then pulled before ever
# shipping: verifying it here caught that it was written against the wrong
# API shape (Ramp Router is OpenAI-Responses-compatible, `POST /v1/responses`
# with account-specific model ids -- not an Anthropic-Messages-compatible
# `/v1/messages` endpoint taking a bare "deepseek-v4-flash-0731" the way this
# first draft assumed). Re-add it once there is a real Ramp Router account to
# confirm the correct request shape and valid model id against, rather than
# guessing a second time.
_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
_OPENROUTER_MODEL = "deepseek/deepseek-v4-flash-0731"

# A fallback reply is a best-effort emergency draft, not a guarantee of
# matching the primary call's own ceiling (which on some steps here goes as
# high as 48000). Capping it keeps a fallback attempt bounded in latency and
# cost regardless of what the primary call asked for.
_FALLBACK_MAX_TOKENS_CEILING = 16000
_FALLBACK_HTTP_TIMEOUT = httpx.Timeout(120.0, connect=10.0)

# OpenAI-compatible `finish_reason` values, mapped to the Anthropic
# `stop_reason` vocabulary this codebase's callers actually check --
# `latex_from_document.py` compares `stop_reason == "max_tokens"` directly, so
# this mapping has to be exact, not just readable.
_FINISH_REASON_TO_STOP_REASON: dict[str, str] = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "content_filter": "refusal",
}


class _FallbackUntranslatableError(Exception):
    """Raised when a request can't be expressed to a fallback provider.

    Every retry message built in this codebase is plain text or a list of
    `{"type": "text", ...}` blocks -- except the PDF/image blocks
    `latex_from_document.py` attaches for template extraction. Nothing here
    knows how to turn a PDF into something a text-only DeepSeek endpoint can
    read, and guessing would ship silently wrong output rather than the
    honest failure the caller already handles. Caught in
    `_try_fallback_providers`, which treats it exactly like a provider that
    was unreachable: skip to the next one, or give up.
    """


class _ShimTextBlock:
    """Stands in for one `TextBlock` of an Anthropic `Message.content`."""

    __slots__ = ("text", "type")

    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class _ShimUsage:
    """Stands in for `Message.usage`.

    Only `output_tokens` is real; the rest are `None` because an
    OpenAI-compatible provider's `usage` object has nothing to fill them
    with, and every read of them elsewhere in this codebase goes through
    `getattr(..., None)` (see `response_diagnostics` and
    `tailor._log_prompt_cache`), so `None` is a value those call sites
    already treat as "not reported" rather than an error.
    """

    __slots__ = (
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
        "input_tokens",
        "output_tokens",
    )

    def __init__(self, output_tokens: int | None) -> None:
        self.output_tokens = output_tokens
        self.input_tokens: int | None = None
        self.cache_read_input_tokens: int | None = None
        self.cache_creation_input_tokens: int | None = None


class _ShimMessage:
    """A minimal stand-in for an Anthropic `Message`, built from an
    OpenAI-compatible chat-completions response (OpenRouter).

    Every caller of `create_message` in this codebase reaches the response
    only through `response_text` and `response_diagnostics` -- confirmed by
    grepping every call site for direct field access -- so satisfying those
    two functions is the whole contract. Normalizing here, instead of handing
    callers a raw OpenAI-shaped dict, is what lets every existing caller stay
    unaware a fallback ever happened.
    """

    __slots__ = ("content", "stop_reason", "usage")

    def __init__(self, *, text: str, stop_reason: str, output_tokens: int | None) -> None:
        self.content = [_ShimTextBlock(text)]
        self.stop_reason = stop_reason
        self.usage = _ShimUsage(output_tokens)


def _flatten_content_to_text(content: Any) -> str:
    """Reduce one Anthropic message `content` value to plain text.

    `content` here is always either a plain string, or a list of blocks that
    are all `{"type": "text", "text": ...}` -- except the PDF/image blocks
    `latex_from_document.py` builds, which raise rather than being silently
    dropped or mistranslated.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            block_type = (
                block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
            )
            if block_type != "text":
                raise _FallbackUntranslatableError(
                    f"unsupported content block type: {block_type!r}"
                )
            text = block.get("text") if isinstance(block, dict) else getattr(block, "text", None)
            parts.append(text or "")
        return "".join(parts)
    raise _FallbackUntranslatableError(f"unsupported content shape: {type(content)!r}")


def _to_openai_messages(system: Any, messages: list[Any]) -> list[dict[str, str]]:
    """Translate Anthropic-SDK-shaped `system` + `messages` kwargs into the
    OpenAI chat-completions `messages` array OpenRouter expects."""
    openai_messages: list[dict[str, str]] = []
    if system:
        openai_messages.append({"role": "system", "content": _flatten_content_to_text(system)})
    for entry in messages:
        role = entry["role"] if isinstance(entry, dict) else entry.role
        content = entry["content"] if isinstance(entry, dict) else entry.content
        openai_messages.append({"role": role, "content": _flatten_content_to_text(content)})
    return openai_messages


def _fallback_max_tokens(kwargs: dict[str, Any]) -> int:
    requested = kwargs.get("max_tokens")
    if isinstance(requested, int) and requested > 0:
        return min(requested, _FALLBACK_MAX_TOKENS_CEILING)
    return _FALLBACK_MAX_TOKENS_CEILING


async def _call_openrouter(settings: Settings, kwargs: dict[str, Any]) -> Any | None:
    """One best-effort call to OpenRouter's OpenAI-compatible chat-completions
    endpoint. Returns `None` -- not an error -- when the request can't be
    expressed in that shape at all, which `_try_fallback_providers` treats the
    same as "this provider has nothing to offer, try the next one."
    """
    try:
        openai_messages = _to_openai_messages(kwargs.get("system"), kwargs.get("messages") or [])
    except _FallbackUntranslatableError as exc:
        log.info("llm.fallback_skipped", provider="openrouter", reason=str(exc))
        return None
    payload = {
        "model": _OPENROUTER_MODEL,
        "messages": openai_messages,
        "max_tokens": _fallback_max_tokens(kwargs),
    }
    async with httpx.AsyncClient(timeout=_FALLBACK_HTTP_TIMEOUT) as http_client:
        response = await http_client.post(
            f"{_OPENROUTER_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.openrouter_api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
    response.raise_for_status()
    data = response.json()
    choice = data["choices"][0]
    text = (choice.get("message") or {}).get("content") or ""
    finish_reason = choice.get("finish_reason")
    usage = data.get("usage") or {}
    return _ShimMessage(
        text=text,
        stop_reason=_FINISH_REASON_TO_STOP_REASON.get(finish_reason, finish_reason or "end_turn"),
        output_tokens=usage.get("completion_tokens"),
    )


async def _try_fallback_providers(kwargs: dict[str, Any]) -> Any | None:
    """Ask OpenRouter for the same completion Manifest just exhausted its
    retries on. Returns `None` -- "give up, raise the real error" -- the
    instant there is nothing left to try: no key is configured, the request
    can't be expressed to OpenRouter, or OpenRouter itself failed too. The
    caller re-raises Manifest's own exhausted error in every one of those
    cases, so a genuine failure is never swallowed -- it is either fixed by
    OpenRouter answering, or it surfaces exactly as it always has.
    """
    settings = get_settings()
    if not settings.openrouter_api_key:
        return None
    try:
        result = await _call_openrouter(settings, kwargs)
    except Exception as exc:  # noqa: BLE001 - deliberately broad: any failure here
        # just means the fallback didn't work either, and the caller's own
        # re-raise of the real Manifest error is the failure a reader
        # actually needs to see, not this one.
        log.warning("llm.fallback_provider_failed", provider="openrouter", error=repr(exc)[:200])
        return None
    if result is not None:
        log.warning("llm.fallback_provider_served", provider="openrouter")
    return result


def _retry_after_seconds(exc: anthropic.APIStatusError) -> float | None:
    """The wait the gateway asked for, when it asked for one we can honour."""
    headers = getattr(getattr(exc, "response", None), "headers", None)
    if not headers:
        return None
    raw = headers.get("retry-after")
    if not raw:
        return None
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        # The HTTP-date form of the header. Rare from this gateway, and guessing
        # at a parse is worse than falling back to our own schedule.
        return None
    if seconds <= 0:
        return None
    return min(seconds, _MAX_RETRY_AFTER_SECONDS)


async def create_message(client: Any, **kwargs: Any) -> Any:
    """Send one request and return the assembled Message, streaming it.

    Streaming rather than a plain create, because `max_tokens` has to be large
    enough to hold an extended-thinking block AND the JSON answer, and the SDK
    refuses a non-streaming request whose budget implies more than ten minutes:
    anything over 21333 tokens raises "Streaming is required for operations that
    may take longer than 10 minutes" before the request is even sent. Raising the
    ceiling without switching to streaming took every tailoring call down.

    Retries a rate-limited or overloaded gateway on a schedule long enough to
    outlast it, and retries a connection that breaks part way through a reply.
    Every agent in this codebase goes through here, so a transient 429 or a
    dropped socket stops being the difference between a finished edit and an
    error the user reads as the feature being broken. Nothing else is retried: a
    400 is a bad request that will fail again, a 500 has already been retried by
    the SDK, and a timeout means the gateway stopped answering.

    If Manifest fails outright -- any status code other than a retryable
    429/529, including a 401 like the OAuth-expired M102 error, a 403, or its
    own 500 -- or is still rate-limited or overloaded after that whole retry
    schedule, and `OPENROUTER_API_KEY` is configured, this falls to OpenRouter
    for the same completion (a DeepSeek model, see the constants above) before
    giving up. Unconfigured, this is a no-op and behaviour is exactly what it
    was before this fallback existed: Manifest's own error surfaces.

    The caller gets the same object `messages.create` would have returned --
    or, if a fallback provider answered instead, an object shaped enough like
    one that `response_text` and `response_diagnostics` still work unchanged.
    """
    last_error: Exception | None = None
    for attempt, backoff in enumerate((*_RETRY_BACKOFF_SECONDS, None)):
        try:
            async with client.messages.stream(**kwargs) as stream:
                return await asyncio.wait_for(
                    stream.get_final_message(), timeout=_STREAM_WALL_CLOCK_TIMEOUT_SECONDS
                )
        except _RETRYABLE_TRANSPORT_ERRORS as exc:
            if isinstance(exc, _NON_RETRYABLE_TRANSPORT_ERRORS) or backoff is None:
                raise
            last_error = exc
            jitter = random.uniform(0.8, 1.2)  # noqa: S311
            wait = backoff * jitter
            # `repr`, not `str`: `httpx.ReadError('')` stringifies to nothing at
            # all, which is how the failure this guards against reached a user as
            # a blank error message.
            log.warning(
                "llm.stream_broken_retrying",
                attempt=attempt + 1,
                waiting_seconds=round(wait, 1),
                error=repr(exc)[:200],
            )
            await _sleep(wait)
        except anthropic.APIStatusError as exc:
            if exc.status_code not in _RETRYABLE_STATUSES:
                # Not a capacity blip: Manifest rejected or failed the
                # request outright (auth, a bad gateway, its own 5xx). Give
                # the fallback provider one shot before surfacing this,
                # since it is no less likely to help here than after a
                # sustained 429/529 below -- if anything, more likely, since
                # an OpenRouter key is independent of whatever broke
                # Manifest's own upstream auth.
                fallback = await _try_fallback_providers(kwargs)
                if fallback is not None:
                    return fallback
                raise
            last_error = exc
            if backoff is None:
                # This is the exhaustion point: every attempt in
                # `_RETRY_BACKOFF_SECONDS` has now come back 429/529, so
                # Manifest is not blipping, it is out of capacity. Try the
                # fallback providers before giving up -- `None` from this call
                # means "nothing configured, nothing translatable, or every
                # configured provider also failed," in which case `raise`
                # below surfaces this exact exhausted error exactly as it did
                # before fallback existed.
                fallback = await _try_fallback_providers(kwargs)
                if fallback is not None:
                    return fallback
                raise
            # Jitter so a tailor loop that hits the limit on one pass does not
            # march every later pass into the same window. Spreading retries is
            # not a security decision, so the fast PRNG is the right one.
            jitter = random.uniform(0.8, 1.2)  # noqa: S311
            wait = _retry_after_seconds(exc) or backoff * jitter
            log.warning(
                "llm.gateway_busy_retrying",
                status=exc.status_code,
                attempt=attempt + 1,
                waiting_seconds=round(wait, 1),
                error=str(exc)[:200],
            )
            await _sleep(wait)
    # Unreachable: the final schedule entry is None, which re-raises above.
    raise last_error if last_error else RuntimeError("create_message made no attempt")


def response_text(message: Any) -> str:
    """Concatenate the text blocks of an Anthropic message response."""
    return "".join(
        block.text for block in message.content if getattr(block, "type", "") == "text"
    )


def response_diagnostics(message: Any) -> dict[str, Any]:
    """Why a reply could not be used, in terms a log can act on.

    A run died on two consecutive empty replies with nothing recorded but the
    empty string, which said nothing about whether the model was truncated, spent
    its budget on a thinking block, or came back with no content at all.
    """
    usage = getattr(message, "usage", None)
    return {
        "stop_reason": getattr(message, "stop_reason", None),
        "block_types": [
            getattr(block, "type", "?") for block in (getattr(message, "content", None) or [])
        ],
        "output_tokens": getattr(usage, "output_tokens", None),
    }


def extract_json_object(text: str) -> str:
    """Pull the first complete JSON object out of a model reply.

    Scans for the first balanced brace span, tracking string state so that
    braces and quotes inside string values do not throw off the depth count.
    Returns the input unchanged when there is no object to find, so the
    caller's validation error still reports what the model actually said.
    """
    fenced = _FENCE_RE.search(text)
    stripped = (fenced.group(1) if fenced else text).strip()

    start = stripped.find("{")
    if start == -1:
        return stripped

    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(stripped)):
        char = stripped[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return stripped[start : index + 1]
    # Unbalanced: the reply was truncated mid-object. Hand back what we have so
    # the validation error names the real problem.
    return stripped[start:]


def parse_model_json[M: BaseModel](model: type[M], text: str) -> M:
    """Validate `text` as `model`, tolerating fences and surrounding prose."""
    return model.model_validate_json(extract_json_object(text))
