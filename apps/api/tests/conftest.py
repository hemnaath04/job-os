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
from collections.abc import AsyncIterator, Iterator
from urllib.parse import urlsplit

import pytest

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://job_os:job_os@localhost/job_os"
)

from _fake_appwrite import FakeAppwriteTables  # noqa: E402
from job_os.services import appwrite_tables, llm_json  # noqa: E402

# ---------------------------------------------------------------------------
# Appwrite
# ---------------------------------------------------------------------------
# `requires_appwrite_key` used to live here: a collection-time skipif on
# `get_settings().appwrite_api_key` that guarded the ingest-upsert and
# job-index-ranking suites. Its stated reason was that faking the one thing
# under test defeats the point, the same argument `db_session` makes for
# wanting a real Postgres. The argument did not survive contact with what it
# actually produced:
#
#   * CI sets no Appwrite key, so all 35 of those tests skipped on every run.
#     The last run before this change reported 1992 passed, 115 skipped.
#   * Locally, with a key set, they wrote to the PRODUCTION `job_postings`
#     table and never cleaned up. 123 rows with a `source` beginning `rank_`
#     were still sitting in the live table, left by earlier runs.
#   * And 18 of them failed anyway, because the assertions still read Postgres
#     rows for code that had moved to Appwrite.
#
# So the coverage over `upsert_postings`, `deactivate_missing` and
# `search_index` was dead in both directions, which is how three production
# bugs shipped through that exact path in one day.
#
# `fake_appwrite` below replaces it. See `tests/_fake_appwrite.py` for what the
# fake models faithfully, what it deliberately does not, and why the seam is
# `appwrite_tables._request` rather than the five public functions.

#: Hostnames a test is allowed to build an Appwrite URL for. An allow-list
#: rather than a block-list of the production endpoint: a self-hosted or
#: staging endpoint set in someone's environment is just as much "not a fake",
#: and a block-list would not catch it.
_ALLOWED_APPWRITE_HOSTS = ("localhost", "127.0.0.1", "::1")
_ALLOWED_APPWRITE_SUFFIXES = (".test", ".invalid", ".example", ".localhost")


@pytest.fixture(autouse=True)
def no_real_appwrite(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make it impossible for a test to reach a real Appwrite, key or no key.

    Every TablesDB call goes through `_base_url` to get its URL and its
    `X-Appwrite-Key` header, so wrapping that one function catches all of them,
    before any I/O rather than after. Wrapping rather than replacing keeps the
    existing behaviour intact for `test_appwrite_tables.py`, which points
    `get_settings` at `https://appwrite.test/v1` and drives the real `_request`
    against its own fake httpx client -- that host is allowed and those tests
    are unaffected.

    This is the guard requirement 3 asks for: a future test that quietly starts
    talking to the live table fails here with a message naming the host, rather
    than leaving rows behind in production for somebody to find later.
    """
    real_base_url = appwrite_tables._base_url

    def guarded(table_id: str | None = None) -> tuple[str, dict[str, str]]:
        url, headers = real_base_url(table_id)
        host = urlsplit(url).hostname or ""
        allowed = host in _ALLOWED_APPWRITE_HOSTS or host.endswith(_ALLOWED_APPWRITE_SUFFIXES)
        if not allowed:
            raise RuntimeError(
                f"a test tried to reach a real Appwrite at {host!r}. Tests must use the "
                "`fake_appwrite` fixture (tests/_fake_appwrite.py). Earlier runs of the "
                "job_postings suites against the live table left 123 rows behind."
            )
        return url, headers

    monkeypatch.setattr(appwrite_tables, "_base_url", guarded)


@pytest.fixture
def fake_appwrite(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeAppwriteTables]:
    """An in-memory `job_postings` table, under `appwrite_tables._request`.

    Everything above that seam is the real thing: filter parsing, both query
    transports, batching, and the public functions the code under test calls.
    Nothing below it runs, and nothing leaves the process.
    """
    fake = FakeAppwriteTables()
    fake.install(monkeypatch)
    yield fake


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


@pytest.fixture
def background_session(monkeypatch: pytest.MonkeyPatch, db_session):
    """Point jd_ingest's deferred parse at the test's own session.

    complete_job_parse opens its own session because the request that
    scheduled it has long since closed one. In a test the row lives inside the
    fixture's outer transaction and no other connection can see it, so the
    background task has to be handed the one session that can.
    """
    from contextlib import asynccontextmanager

    from job_os.services import jd_ingest

    @asynccontextmanager
    async def _factory():
        yield db_session

    monkeypatch.setattr(jd_ingest, "async_session", _factory)
    return db_session

