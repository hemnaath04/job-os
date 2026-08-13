"""The upsert contract, against a real Postgres.

`first_seen_at` is the load-bearing column of the whole honest-freshness story. If
a re-crawl overwrites it, every "first seen 3 weeks ago, reposted 1 hour ago" claim
becomes a lie and the product loses the differentiator it was built for. That is
what most of this file is about.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from job_os.db.models.job_posting import JobPosting
from job_os.ingest.providers import RawPosting
from job_os.ingest.upsert import deactivate_missing, mark_duplicates, upsert_postings


def make_posting(
    *,
    source: str,
    token: str,
    external_id: str = "1",
    title: str = "Software Engineer",
    location: str | None = "San Francisco, CA",
    description: str = "Build things. Ship them. Learn from users.",
    posted_at: datetime | None = None,
    basis: str = "published",
) -> RawPosting:
    return RawPosting(
        source=source,
        board_token=token,
        external_id=external_id,
        title=title,
        company_name="Acme",
        company_domain="acme.test",
        source_url=f"https://example.test/{token}/{external_id}",
        jd_clean=description,
        location=location,
        country_code="US",
        posted_at=posted_at or datetime(2026, 8, 1, tzinfo=UTC),
        posted_at_basis=basis,
    )


async def fetch(session: AsyncSession, source: str, source_id: str) -> JobPosting:
    # The upsert runs as a Core statement, so the ORM identity map does not learn
    # that the row changed and a plain re-read hands back the stale Python object,
    # making the assertions test nothing. `populate_existing` overwrites this row's
    # attributes from the database. `expire_all()` would also work but expires
    # every other loaded object too, and touching one of those afterwards triggers
    # a lazy refresh outside the async context.
    row = await session.execute(
        select(JobPosting)
        .where(JobPosting.source == source, JobPosting.source_id == source_id)
        .execution_options(populate_existing=True)
    )
    return row.scalar_one()


@pytest.fixture
def source() -> str:
    """A source namespace unique to one test, so tests cannot collide."""
    return f"test_{uuid.uuid4().hex[:12]}"


@pytest.fixture
async def crawl_run(db_session: AsyncSession) -> uuid.UUID:
    """A real crawl_runs row.

    `job_postings.last_crawl_run_id` is a foreign key, which is deliberate: the
    deactivation rule is only sound when the run it names actually happened.
    """
    from job_os.db.models.ingest import CrawlRun

    run = CrawlRun(status="running", providers=["test"])
    db_session.add(run)
    await db_session.flush()
    return run.id


@pytest.fixture
async def other_run(db_session: AsyncSession) -> uuid.UUID:
    from job_os.db.models.ingest import CrawlRun

    run = CrawlRun(status="running", providers=["test"])
    db_session.add(run)
    await db_session.flush()
    return run.id


async def test_same_job_twice_preserves_first_seen_and_bumps_last_seen(
    db_session: AsyncSession, source: str
) -> None:
    posting = make_posting(source=source, token="acme")
    first_run = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    second_run = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)

    stats = await upsert_postings(db_session, [posting], seen_at=first_run)
    assert stats.inserted == 1

    stats = await upsert_postings(db_session, [posting], seen_at=second_run)
    assert stats.inserted == 0
    # Identical content, so this is a re-sighting rather than an edit.
    assert stats.unchanged == 1
    assert stats.updated == 0

    row = await fetch(db_session, source, "acme:1")
    assert row.first_seen_at == first_run, "a re-crawl must never re-date a posting"
    assert row.last_seen_at == second_run


async def test_repeated_upserts_never_duplicate_the_row(
    db_session: AsyncSession, source: str
) -> None:
    posting = make_posting(source=source, token="acme")
    for day in range(1, 6):
        await upsert_postings(
            db_session, [posting], seen_at=datetime(2026, 8, day, tzinfo=UTC)
        )

    rows = await db_session.execute(
        select(JobPosting).where(JobPosting.source == source)
    )
    all_rows = list(rows.scalars().all())
    assert len(all_rows) == 1
    assert all_rows[0].first_seen_at == datetime(2026, 8, 1, tzinfo=UTC)
    assert all_rows[0].last_seen_at == datetime(2026, 8, 5, tzinfo=UTC)


async def test_edited_posting_updates_values_but_keeps_first_seen(
    db_session: AsyncSession, source: str
) -> None:
    first_run = datetime(2026, 7, 1, tzinfo=UTC)
    await upsert_postings(
        db_session, [make_posting(source=source, token="acme")], seen_at=first_run
    )
    before = await fetch(db_session, source, "acme:1")
    original_updated_at = before.updated_at

    edited = make_posting(
        source=source,
        token="acme",
        title="Senior Software Engineer",
        description="Now with more responsibility and a different scope entirely.",
    )
    second_run = datetime(2026, 8, 1, tzinfo=UTC)
    stats = await upsert_postings(db_session, [edited], seen_at=second_run)
    assert stats.updated == 1
    assert stats.unchanged == 0

    row = await fetch(db_session, source, "acme:1")
    assert row.title == "Senior Software Engineer"
    assert row.first_seen_at == first_run
    assert row.last_seen_at == second_run
    assert row.updated_at != original_updated_at


async def test_unchanged_recrawl_does_not_move_updated_at(
    db_session: AsyncSession, source: str
) -> None:
    """`updated_at` must mean "the employer changed it", not "a crawler ran"."""
    posting = make_posting(source=source, token="acme")
    await upsert_postings(db_session, [posting], seen_at=datetime(2026, 7, 1, tzinfo=UTC))
    original = (await fetch(db_session, source, "acme:1")).updated_at

    await upsert_postings(db_session, [posting], seen_at=datetime(2026, 8, 1, tzinfo=UTC))
    assert (await fetch(db_session, source, "acme:1")).updated_at == original


async def test_field_cleared_on_the_board_is_cleared_in_the_index(
    db_session: AsyncSession, source: str
) -> None:
    """A withdrawn salary band must not survive as a stale value.

    This is the case a `coalesce(new, old)` upsert gets wrong: coalesce keeps the
    old value whenever the new one is NULL, so the index would keep advertising a
    salary the employer has taken down.
    """
    with_salary = make_posting(source=source, token="acme")
    with_salary.salary_min = 150_000
    with_salary.salary_max = 200_000
    with_salary.salary_currency = "USD"
    await upsert_postings(db_session, [with_salary], seen_at=datetime(2026, 7, 1, tzinfo=UTC))
    assert (await fetch(db_session, source, "acme:1")).salary_min == 150_000

    without_salary = make_posting(
        source=source, token="acme", description="Body changed so the hash changes too."
    )
    await upsert_postings(
        db_session, [without_salary], seen_at=datetime(2026, 8, 1, tzinfo=UTC)
    )

    row = await fetch(db_session, source, "acme:1")
    assert row.salary_min is None
    assert row.salary_max is None


async def test_deactivate_only_touches_boards_this_run_read(
    db_session: AsyncSession, source: str, crawl_run: uuid.UUID, other_run: uuid.UUID
) -> None:
    run_one = crawl_run
    run_two = other_run
    postings = [
        make_posting(source=source, token="acme", external_id="1"),
        make_posting(source=source, token="acme", external_id="2", title="Data Engineer"),
        make_posting(source=source, token="other", external_id="9", title="Designer"),
    ]
    await upsert_postings(
        db_session, postings, run_id=run_one, seen_at=datetime(2026, 7, 1, tzinfo=UTC)
    )

    # Second crawl: acme now lists only posting 1. `other` was not fetched at all.
    await upsert_postings(
        db_session,
        [make_posting(source=source, token="acme", external_id="1")],
        run_id=run_two,
        seen_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    deactivated = await deactivate_missing(
        db_session, source=source, board_token="acme", run_id=run_two
    )
    assert deactivated == 1

    still_listed = await fetch(db_session, source, "acme:1")
    dropped = await fetch(db_session, source, "acme:2")
    untouched_board = await fetch(db_session, source, "other:9")

    assert still_listed.active is True
    assert dropped.active is False
    assert dropped.inactive_since is not None
    # The board we never fetched must be left alone. Deactivating it would close a
    # company's whole board because of a request that did not happen.
    assert untouched_board.active is True


async def test_deactivation_never_deletes(
    db_session: AsyncSession, source: str, crawl_run: uuid.UUID, other_run: uuid.UUID
) -> None:
    await upsert_postings(
        db_session,
        [make_posting(source=source, token="acme", external_id="1")],
        run_id=crawl_run,
        seen_at=datetime(2026, 7, 1, tzinfo=UTC),
    )
    await deactivate_missing(
        db_session, source=source, board_token="acme", run_id=other_run
    )

    row = await fetch(db_session, source, "acme:1")
    assert row.active is False
    # The history survives, so a closure can be shown honestly and a later repost
    # can still be recognised as the same posting.
    assert row.first_seen_at == datetime(2026, 7, 1, tzinfo=UTC)


async def test_repost_reactivates_and_counts_without_resetting_history(
    db_session: AsyncSession, source: str, crawl_run: uuid.UUID, other_run: uuid.UUID
) -> None:
    posting = make_posting(source=source, token="acme")
    first_seen = datetime(2026, 6, 1, tzinfo=UTC)
    await upsert_postings(db_session, [posting], run_id=crawl_run, seen_at=first_seen)
    await deactivate_missing(
        db_session, source=source, board_token="acme", run_id=other_run
    )
    assert (await fetch(db_session, source, "acme:1")).active is False

    reposted_at = datetime(2026, 8, 1, tzinfo=UTC)
    await upsert_postings(db_session, [posting], run_id=other_run, seen_at=reposted_at)

    row = await fetch(db_session, source, "acme:1")
    assert row.active is True
    assert row.inactive_since is None
    assert row.repost_count == 1
    # The whole point: a reposted role does not get to look brand new.
    assert row.first_seen_at == first_seen
    assert row.last_seen_at == reposted_at


async def test_posted_at_estimated_is_derived_from_basis(
    db_session: AsyncSession, source: str
) -> None:
    """The flag is a generated column, so it can never disagree with the basis."""
    published = make_posting(
        source=source, token="acme", external_id="1", basis="published"
    )
    from_updated = make_posting(
        source=source, token="acme", external_id="2", basis="updated", title="Analyst"
    )
    never_dated = make_posting(
        source=source, token="acme", external_id="3", basis="first_crawl", title="Chef"
    )
    await upsert_postings(db_session, [published, from_updated, never_dated])

    assert (await fetch(db_session, source, "acme:1")).posted_at_estimated is False
    assert (await fetch(db_session, source, "acme:2")).posted_at_estimated is True
    assert (await fetch(db_session, source, "acme:3")).posted_at_estimated is True


async def test_duplicate_ids_in_one_batch_are_skipped_not_fatal(
    db_session: AsyncSession, source: str
) -> None:
    """A board listing one posting id twice must not take the whole batch down."""
    posting = make_posting(source=source, token="acme")
    stats = await upsert_postings(db_session, [posting, posting])
    assert stats.inserted == 1
    assert stats.skipped == 1


async def test_marked_duplicate_keeps_its_row(
    db_session: AsyncSession, source: str
) -> None:
    a = make_posting(source=source, token="acme", external_id="1")
    b = make_posting(
        source=source, token="acme", external_id="2", location="New York, NY"
    )
    await upsert_postings(db_session, [a, b])
    canonical = await fetch(db_session, source, "acme:1")
    duplicate = await fetch(db_session, source, "acme:2")

    marked = await mark_duplicates(
        db_session, [(duplicate.id, canonical.id, "exact_key", None)]
    )
    assert marked == 1

    refreshed = await fetch(db_session, source, "acme:2")
    assert refreshed.canonical_id == canonical.id
    assert refreshed.duplicate_reason == "exact_key"
    # Marked, not deleted: the duplicate's own URL still resolves and a wrong
    # merge stays reversible.
    assert refreshed.source_url


async def test_content_hash_ignores_a_trailing_boilerplate_edit(
    db_session: AsyncSession, source: str
) -> None:
    """Only the first HASH_DESCRIPTION_CHARS of the description feed the hash.

    A company editing its EEO footer across 400 postings should not read as 400
    edited jobs, which would then all look freshly touched. The body here is
    deliberately longer than the hash window so the footer falls outside it.
    """
    from job_os.ingest.normalize import HASH_DESCRIPTION_CHARS

    body = "Real role content. " * 400
    assert len(body) > HASH_DESCRIPTION_CHARS
    original = make_posting(source=source, token="acme", description=body + "Footer v1.")
    await upsert_postings(db_session, [original], seen_at=datetime(2026, 7, 1, tzinfo=UTC))

    tail_edited = make_posting(
        source=source, token="acme", description=body + "Footer v2, legal text changed."
    )
    stats = await upsert_postings(
        db_session, [tail_edited], seen_at=datetime(2026, 8, 1, tzinfo=UTC)
    )
    assert stats.unchanged == 1
    assert stats.updated == 0


async def test_upsert_is_scoped_per_source(db_session: AsyncSession) -> None:
    """Two providers can use the same board token without colliding."""
    left = f"test_{uuid.uuid4().hex[:12]}"
    right = f"test_{uuid.uuid4().hex[:12]}"
    await upsert_postings(
        db_session,
        [
            make_posting(source=left, token="acme", external_id="1"),
            make_posting(source=right, token="acme", external_id="1"),
        ],
    )
    assert (await fetch(db_session, left, "acme:1")).source == left
    assert (await fetch(db_session, right, "acme:1")).source == right


async def test_last_seen_moves_forward_across_many_crawls(
    db_session: AsyncSession, source: str
) -> None:
    posting = make_posting(source=source, token="acme")
    base = datetime(2026, 8, 1, tzinfo=UTC)
    for hours in (0, 6, 12, 18):
        await upsert_postings(
            db_session, [posting], seen_at=base + timedelta(hours=hours)
        )
    row = await fetch(db_session, source, "acme:1")
    assert row.first_seen_at == base
    assert row.last_seen_at == base + timedelta(hours=18)
