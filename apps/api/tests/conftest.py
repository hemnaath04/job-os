"""Shared test setup.

The gateway retry in `llm_json.create_message` waits real seconds between
attempts, which is right in production and wrong in a test suite: the two
tailor tests that simulate a 429 would each sit through the whole backoff
schedule, adding minutes to a run and testing nothing but `asyncio.sleep`.

Every test gets an instant, recording sleep instead. Tests that care how long
the retry would have waited take the `gateway_waits` fixture and assert on the
recorded values, which keeps the schedule itself covered.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://job_os:job_os@localhost/job_os"
)

from job_os.services import llm_json  # noqa: E402


@pytest.fixture(autouse=True)
def gateway_waits(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record the gateway backoff waits instead of serving them.

    Patches `llm_json._sleep`, not `asyncio.sleep`. The module-level indirection
    exists for this: replacing `asyncio.sleep` would reach every coroutine in the
    process, which is far more than "do not really wait forty-five seconds".
    """
    waits: list[float] = []

    async def instant_sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(llm_json, "_sleep", instant_sleep)
    return waits
