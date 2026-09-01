"""The index has to stop growing somewhere, and it has to stop safely."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from job_os.ingest.providers import RawPosting
from job_os.ingest.prune import prune_index
from job_os.ingest.upsert import upsert_postings

pytestmark = pytest.mark.asyncio

NOW = datetime.now(UTC)


async def _insert(session: AsyncSession, external_id: str, *, age_days: int) -> None:
    """Insert through the real upsert, so the row has whatever shape the
    schema actually has rather than whatever this test remembers of it."""
    posting = RawPosting(
        source="greenhouse",
        board_token="acme",
        external_id=external_id,
        title=f"Engineer {external_id}",
        company_name="Acme",
        source_url=f"https://boards.greenhouse.io/acme/{external_id}",
        jd_clean=f"Engineer {external_id}",
        posted_at=NOW - timedelta(days=age_days),
        posted_at_basis="published",
    )
    await upsert_postings(session, [posting], seen_at=NOW)
    await session.commit()


async def test_old_postings_go_and_fresh_ones_stay(db_session: AsyncSession) -> None:
    await _insert(db_session, "old", age_days=90)
    await _insert(db_session, "fresh", age_days=3)

    result = await prune_index(db_session, max_age_days=60, max_rows=1_000_000)

    assert result.aged_out == 1
    remaining = (
        await db_session.execute(text("select external_id from job_postings"))
    ).scalars().all()
    assert remaining == ["fresh"]


async def test_a_dry_run_reports_without_deleting(db_session: AsyncSession) -> None:
    """The count has to be inspectable before it is also irreversible."""
    await _insert(db_session, "old", age_days=90)

    result = await prune_index(
        db_session, max_age_days=60, max_rows=1_000_000, dry_run=True
    )

    assert result.aged_out == 1
    assert result.rows_after == result.rows_before, "nothing was deleted"


async def test_a_posting_the_user_tracks_survives_its_own_age(
    db_session: AsyncSession,
) -> None:
    """Age is a proxy for "nobody wants this", and tracking is direct evidence
    against it. A posting someone applied through must not vanish from under
    the application that points at it, however old the board says it is.
    """
    await _insert(db_session, "tracked", age_days=200)
    url = "https://boards.greenhouse.io/acme/tracked"
    await db_session.execute(
        text(
            "insert into companies (id, name, created_at, updated_at) "
            "values (:cid, 'Acme', :now, :now)"
        ),
        {"cid": "11111111-1111-1111-1111-111111111111", "now": NOW},
    )
    await db_session.execute(
        text(
            """
            insert into jobs
                (id, company_id, title, source_url, jd_raw, jd_clean,
                 source, created_at, updated_at)
            values (gen_random_uuid(), :cid, 'Engineer', :url, '', '', 'manual', :now, :now)
            """
        ),
        {"cid": "11111111-1111-1111-1111-111111111111", "url": url, "now": NOW},
    )
    await db_session.commit()

    result = await prune_index(db_session, max_age_days=60, max_rows=1_000_000)

    assert result.aged_out == 0
    assert result.protected_kept == 1
    survived = (
        await db_session.execute(text("select count(*) from job_postings"))
    ).scalar_one()
    assert survived == 1


async def test_the_ceiling_takes_the_oldest_first(db_session: AsyncSession) -> None:
    """The backstop exists because provider selection is a judgement call and
    this is not: whatever gets crawled, the table has a maximum size.
    """
    for i, age in enumerate([50, 40, 30, 20, 10]):
        await _insert(db_session, f"p{i}", age_days=age)

    result = await prune_index(db_session, max_age_days=365, max_rows=2)

    assert result.aged_out == 0, "nothing was old enough for the age rule"
    assert result.over_ceiling == 3
    kept = (
        await db_session.execute(
            text("select external_id from job_postings order by posted_at")
        )
    ).scalars().all()
    assert kept == ["p3", "p4"], "the two freshest survived"
