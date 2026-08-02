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
import structlog
from pydantic import BaseModel

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
# Indirected so a test can shorten the wait by patching this name alone. Patching
# `asyncio.sleep` itself would reach every coroutine in the process, which is a
# much bigger blast radius than "do not really wait forty-five seconds".
_sleep = asyncio.sleep


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
    outlast it. Every agent in this codebase goes through here, so a transient
    429 stops being the difference between a finished edit and an error the user
    reads as the feature being broken. Nothing else is retried: a 400 is a bad
    request that will fail again, and a 500 has already been retried by the SDK.

    The caller gets the same object `messages.create` would have returned, so
    `response_text` and `response_diagnostics` work unchanged.
    """
    last_error: anthropic.APIStatusError | None = None
    for attempt, backoff in enumerate((*_RETRY_BACKOFF_SECONDS, None)):
        try:
            async with client.messages.stream(**kwargs) as stream:
                return await stream.get_final_message()
        except anthropic.APIStatusError as exc:
            if exc.status_code not in _RETRYABLE_STATUSES or backoff is None:
                raise
            last_error = exc
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
