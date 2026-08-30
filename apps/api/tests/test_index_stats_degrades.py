"""A diagnostic must not fail the work it reports on.

`index_stats` makes six sequential Appwrite reads. Each can draw a cold cache
and time out: a cold `last_seen_at` sort measured 24s against 1.75s warm, and
the fulltext index behaves the same way. All-or-nothing meant any one of those
lost the whole report, and because the workflow step exits non-zero on it, a
sweep that had crawled 1,958 postings successfully was reported as a failure.

Five counters and a null is a useful answer. An exception is not.
"""
from __future__ import annotations

import os
from typing import Any

import pytest

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://job_os:job_os@localhost/job_os"
)

from job_os.services import appwrite_tables, job_index  # noqa: E402

pytestmark = pytest.mark.asyncio


async def test_one_unavailable_counter_does_not_lose_the_others(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The failure that marked a good sweep bad."""
    calls = 0

    async def _count(**_kwargs: Any) -> int:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise appwrite_tables.AppwriteTablesError(408, "Database timed out.")
        return 42

    async def _list(**_kwargs: Any) -> list[dict[str, Any]]:
        return [{"last_seen_at": "2026-08-30T18:53:36.603+00:00"}]

    monkeypatch.setattr(appwrite_tables, "count_rows", _count)
    monkeypatch.setattr(appwrite_tables, "list_rows", _list)

    stats = await job_index.index_stats()

    assert stats["postings_total"] == 42, "the counters that answered are kept"
    assert stats["duplicates_marked"] is None, "the one that did not is null, not absent"
    assert stats["last_crawl_seen_at"] == "2026-08-30T18:53:36.603+00:00"


async def test_the_sorted_read_failing_does_not_lose_the_counters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The slowest of the six, and the one that actually failed in production.

    `sort_desc="last_seen_at"` on a cold cache is the read that raised, so it
    is the one most likely to take the report down and the one whose loss
    matters least.
    """

    async def _count(**_kwargs: Any) -> int:
        return 7

    async def _list(**_kwargs: Any) -> list[dict[str, Any]]:
        raise appwrite_tables.AppwriteTablesError(504, "appwrite read timed out")

    monkeypatch.setattr(appwrite_tables, "count_rows", _count)
    monkeypatch.setattr(appwrite_tables, "list_rows", _list)

    stats = await job_index.index_stats()

    assert stats["last_crawl_seen_at"] is None
    assert stats["postings_total"] == 7


async def test_a_real_error_is_still_reported_not_swallowed_into_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """None and 0 mean different things and must not be confused.

    Reporting an unavailable counter as 0 would say the index is empty, which
    is the one reading that would send somebody to re-run a crawl that had
    worked.
    """

    async def _count(**_kwargs: Any) -> int:
        raise appwrite_tables.AppwriteTablesError(408, "Database timed out.")

    async def _list(**_kwargs: Any) -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(appwrite_tables, "count_rows", _count)
    monkeypatch.setattr(appwrite_tables, "list_rows", _list)

    stats = await job_index.index_stats()

    assert stats["postings_total"] is None
    assert stats["postings_total"] != 0
