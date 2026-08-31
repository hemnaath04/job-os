"""What the ops view counts, and that the numbers are real.

This file replaces `test_index_stats_degrades.py`, which existed because
`index_stats` had two failure modes that only Appwrite had:

  * Every counter could time out on its own (a cold `last_seen_at` sort
    measured 24s against 1.75s warm), so each had to degrade to None
    independently or one slow read lost the whole report. A sweep that had
    crawled 1,958 postings successfully was once reported as failed for
    exactly that reason.
  * The cheap count saturated at 5,000, so on a 359,416-row table every
    counter read 5,000 and said nothing. The accurate one walked the table
    with a cursor, and because Appwrite bills reads PER ROW, three of those
    walks during development spent roughly ten times the monthly quota and
    took the whole project's database offline. `counts_exact` in the payload
    existed to say which of the two a reader was looking at.

Neither is a thing on Postgres. `COUNT(*)` is exact, one statement, and free of
per-row billing, so there is no cheap-versus-accurate choice left to report and
no partial answer to assemble. What is worth testing instead is that the
counters mean what they say, which is what this file does.

`counts_exact` is still in the payload and is still asserted, because a
consumer that learned to check it during those two weeks should keep working
rather than silently reading a missing key as falsy.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from job_os.ingest.providers import RawPosting
from job_os.ingest.upsert import deactivate_missing, mark_duplicates, upsert_postings
from job_os.services.job_index import index_stats

pytestmark = pytest.mark.asyncio

NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


@pytest.fixture
def source() -> str:
    return f"stats_{uuid.uuid4().hex[:12]}"


def posting(
    *,
    source: str,
    external_id: str,
    company: str = "Acme",
    basis: str = "published",
    hydrated: bool = True,
) -> RawPosting:
    return RawPosting(
        source=source,
        board_token="board",
        external_id=external_id,
        title="Engineer",
        company_name=company,
        company_domain=f"{company.lower()}.test",
        source_url=f"https://example.test/{external_id}",
        jd_clean="Build and operate the service.",
        jd_hydrated=hydrated,
        location="Boston, MA",
        posted_at=NOW if basis != "first_crawl" else None,
        posted_at_basis=basis,
    )


async def test_the_counters_count_what_they_are_named_after(
    db_session: AsyncSession, source: str
) -> None:
    """One row of each kind, then every counter checked against it.

    The table is shared, so this reads the deltas rather than the absolutes:
    an assertion on the total would be answered partly by whatever else the
    database already holds.
    """
    before = await index_stats(db_session)

    await upsert_postings(
        db_session,
        [
            posting(source=source, external_id="1"),
            posting(source=source, external_id="2", company="Other"),
            posting(source=source, external_id="3", basis="first_crawl"),
            posting(source=source, external_id="4", hydrated=False),
        ],
        seen_at=NOW,
    )
    after = await index_stats(db_session)

    def delta(key: str) -> int:
        return int(after[key]) - int(before[key])  # type: ignore[arg-type]

    assert delta("postings_total") == 4
    assert delta("postings_active") == 4
    assert delta("companies_active") == 2, "Acme and Other, folded by lower()"
    assert delta("posted_at_estimated") == 1, "the first_crawl row, and only it"
    assert delta("descriptions_missing") == 1
    assert after["by_source"][source] == 4  # type: ignore[index]


async def test_a_deactivated_posting_leaves_the_active_count_but_not_the_total(
    db_session: AsyncSession, source: str
) -> None:
    """Deactivation is not deletion, and the two counters have to disagree.

    A closed posting is a fact the UI can show; if `postings_total` fell with
    `postings_active` there would be nothing left to distinguish "closed" from
    "never crawled".
    """
    from job_os.db.models.ingest import CrawlRun

    crawled = CrawlRun(status="running", providers=["test"])
    other = CrawlRun(status="running", providers=["test"])
    db_session.add_all([crawled, other])
    await db_session.flush()

    await upsert_postings(
        db_session, [posting(source=source, external_id="1")], run_id=crawled.id, seen_at=NOW
    )
    before = await index_stats(db_session)
    await deactivate_missing(db_session, source=source, board_token="board", run_id=other.id)
    after = await index_stats(db_session)

    assert int(after["postings_total"]) == int(before["postings_total"])  # type: ignore[arg-type]
    assert int(after["postings_active"]) == int(before["postings_active"]) - 1  # type: ignore[arg-type]


async def test_a_marked_duplicate_moves_from_active_to_duplicates(
    db_session: AsyncSession, source: str
) -> None:
    from sqlalchemy import select

    from job_os.db.models.job_posting import JobPosting

    await upsert_postings(
        db_session,
        [
            posting(source=source, external_id="canonical"),
            posting(source=source, external_id="dupe"),
        ],
        seen_at=NOW,
    )
    rows = (
        await db_session.execute(select(JobPosting).where(JobPosting.source == source))
    ).scalars()
    by_external = {row.external_id: row.id for row in rows}
    before = await index_stats(db_session)

    await mark_duplicates(
        db_session, [(by_external["dupe"], by_external["canonical"], "exact_key", None)]
    )
    after = await index_stats(db_session)

    assert int(after["duplicates_marked"]) == int(before["duplicates_marked"]) + 1  # type: ignore[arg-type]
    assert int(after["postings_active"]) == int(before["postings_active"]) - 1  # type: ignore[arg-type]


async def test_the_payload_still_says_its_counts_are_exact(
    db_session: AsyncSession,
) -> None:
    """Kept as a real assertion rather than dropped as obviously true.

    `counts_exact` is the field that told a reader whether a number was 5,000
    because there were 5,000 rows or because Appwrite stopped counting. It is
    unconditionally True here, and a future store that cannot say that has to
    change this line rather than quietly drop the key.
    """
    stats = await index_stats(db_session)

    assert stats["counts_exact"] is True
