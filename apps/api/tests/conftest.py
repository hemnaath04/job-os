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
from collections.abc import AsyncIterator

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


# ---------------------------------------------------------------------------
# Database-backed tests
# ---------------------------------------------------------------------------
# The ingest upsert contract is about what Postgres does: ON CONFLICT, generated
# columns, xmax, partial indexes. Faking that would test the fake, so these tests
# want a real database and skip cleanly without one. CI already provides it (the
# `api` job runs a pgvector service and `alembic upgrade head` before pytest), so
# they run there; locally, point DATABASE_URL at a migrated database.
#
# Every test runs inside a transaction that is rolled back, so a suite run leaves
# no rows behind even when pointed at a database that has real data in it.


@pytest.fixture
async def db_session() -> AsyncIterator[object]:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    from job_os.settings import get_settings

    engine = create_async_engine(get_settings().database_url, poolclass=None)
    try:
        connection = await engine.connect()
    except Exception as exc:  # noqa: BLE001 - any connection failure means skip
        await engine.dispose()
        pytest.skip(f"no database available: {type(exc).__name__}: {exc}")

    try:
        # Begin before any statement runs. A bare execute() autobegins its own
        # transaction, and begin() then refuses because one is already open.
        transaction = await connection.begin()
        exists = await connection.scalar(
            text("SELECT to_regclass('public.job_postings') IS NOT NULL")
        )
        if not exists:
            pytest.skip("job_postings is missing; run `alembic upgrade head` first")

        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            # A commit inside the code under test becomes a savepoint release
            # rather than a real commit, so the outer rollback below still undoes
            # everything. Without this, anything that commits would escape.
            join_transaction_mode="create_savepoint",
        )
        try:
            yield session
        finally:
            await session.close()
            await transaction.rollback()
    finally:
        await connection.close()
        await engine.dispose()
