"""Shared test doubles for `llm_client.LLMClient`.

Production calls `client.complete(tier=..., max_tokens=..., messages=...,
system=..., effort=..., json_object=...)` through `llm_json.create_message`.
Most fakes here predate that and define their per-test behaviour as an async
`messages.create(**kwargs)` returning an object with `content` text blocks;
`FakeLLMClient` keeps that shape working by routing `complete` to it, so the
recorded kwargs are exactly what production sent.
"""

from __future__ import annotations

from typing import Any


class StreamingFakeMessages:
    """Base for a fake `messages` object. Subclasses define `create`."""

    async def create(self, **kwargs: Any) -> Any:  # pragma: no cover - overridden
        raise NotImplementedError


class FakeLLMClient:
    """Stands in for `llm_client.LLMClient`.

    Either pass `messages=` (an object with an async `create`) or subclass and
    set `self.messages` in `__init__`. Accepts and ignores the real client's
    constructor arguments, so it can be patched in for `llm_client.LLMClient`.
    """

    messages: Any

    def __init__(self, *_args: Any, messages: Any = None, **_kwargs: Any) -> None:
        if messages is not None:
            self.messages = messages

    async def complete(self, **kwargs: Any) -> Any:
        return await self.messages.create(**kwargs)
