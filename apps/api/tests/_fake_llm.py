"""Shared test double for the Anthropic client.

Production streams every request, because `max_tokens` has to hold an
extended-thinking block as well as the answer and the SDK refuses a non-streaming
request that large. A fake that only implements `create` would therefore pass while
production was broken, so the fakes speak the streaming API too and define their
per-test behaviour in `create`.
"""
from __future__ import annotations

from typing import Any


class _FakeStreamManager:
    def __init__(self, create: Any, kwargs: dict[str, Any]) -> None:
        self._create = create
        self._kwargs = kwargs
        self._message: Any = None

    async def __aenter__(self) -> _FakeStreamManager:
        self._message = await self._create(**self._kwargs)
        return self

    async def __aexit__(self, *_exc: Any) -> bool:
        return False

    async def get_final_message(self) -> Any:
        return self._message


class StreamingFakeMessages:
    """Mix in to give a fake `create` the streaming entry point production uses."""

    def stream(self, **kwargs: Any) -> _FakeStreamManager:
        return _FakeStreamManager(self.create, kwargs)  # type: ignore[attr-defined]
