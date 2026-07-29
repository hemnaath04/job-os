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

import re
from typing import Any

from pydantic import BaseModel

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
