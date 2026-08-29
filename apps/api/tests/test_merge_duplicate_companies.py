"""Folding the company pairs an earlier `upsert_company` wrote.

The read is fixed and no new pairs form, but the ones already written stay
until something merges them. The risk in a merge is not the delete, it is the
repoint: a job left pointing at a row that has gone is a broken board.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://job_os:job_os@localhost/job_os"
)

from sqlalchemy import func, select  # noqa: E402

from job_os.db.models import Company, Job  # noqa: E402
from job_os.scripts.merge_duplicate_companies import merge, survivor_of  # noqa: E402


def test_the_survivor_is_the_row_that_knows_the_most() -> None:
    """The same rule `upsert_company` uses, so the two cannot disagree about
    which row is the real company."""
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    bare = Company(name="Acme", domain=None)
    bare.created_at = now - timedelta(days=2)
    bare.id = "00000000-0000-0000-0000-000000000001"
    known = Company(name="Acme", domain="acme.com")
    known.created_at = now
    known.id = "00000000-0000-0000-0000-000000000002"

    # Older, but knows less.
    assert survivor_of([bare, known]) is known
    assert survivor_of([known, bare]) is known


@pytest.mark.asyncio
async def test_jobs_are_repointed_before_the_row_is_deleted(db_session, monkeypatch) -> None:
    """The failure worth protecting against: a job orphaned by the cleanup."""
    import job_os.scripts.merge_duplicate_companies as script

    bare = Company(name="Mergetest", domain=None)
    known = Company(name="Mergetest", domain="mergetest.com")
    db_session.add_all([bare, known])
    await db_session.flush()

    job = Job(
        company_id=bare.id,
        title="Engineer",
        jd_raw="",
        jd_clean="",
        jd_parsed={},
        source="text",
        active=True,
    )
    db_session.add(job)
    await db_session.flush()

    def _session():
        class _Ctx:
            async def __aenter__(self):
                return db_session

            async def __aexit__(self, *_exc):
                return False

        return _Ctx()

    monkeypatch.setattr(script, "async_session", _session)
    await merge(apply=True)

    await db_session.refresh(job)
    assert job.company_id == known.id, "the job followed the survivor"
    remaining = await db_session.execute(
        select(func.count()).select_from(Company).where(
            func.lower(Company.name) == "mergetest"
        )
    )
    assert remaining.scalar() == 1


@pytest.mark.asyncio
async def test_a_dry_run_writes_nothing(db_session, monkeypatch) -> None:
    import job_os.scripts.merge_duplicate_companies as script

    db_session.add_all(
        [Company(name="Drytest", domain=None), Company(name="Drytest", domain="drytest.com")]
    )
    await db_session.flush()

    def _session():
        class _Ctx:
            async def __aenter__(self):
                return db_session

            async def __aexit__(self, *_exc):
                return False

        return _Ctx()

    monkeypatch.setattr(script, "async_session", _session)
    await merge(apply=False)

    count = await db_session.execute(
        select(func.count()).select_from(Company).where(func.lower(Company.name) == "drytest")
    )
    assert count.scalar() == 2, "both rows are still there"
