"""The upsert contract, against an in-memory Appwrite.

`first_seen_at` is the load-bearing column of the whole honest-freshness story. If
a re-crawl overwrites it, every "first seen 3 weeks ago, reposted 1 hour ago" claim
becomes a lie and the product loses the differentiator it was built for. That is
what most of this file is about.

This file used to say "against a real Postgres" and be marked
`requires_appwrite_key`, and both halves of that had stopped being true. The
upsert path moved to Appwrite (`upsert_postings` opens with `del session`;
nothing here touches Postgres), so every assertion that read a `JobPosting` row
back out of the database was reading a table the code no longer writes -- twelve
of the fourteen tests below failed outright the moment the skip was lifted. And
the skip meant they only ever ran on a developer's machine, against the
PRODUCTION `job_postings` table, leaving rows behind: 123 of them were still
there when this was written.

So the assertions now read the row back from `fake_appwrite`, which is the same
row Appwrite would have served. Nothing about what they assert has been
loosened; the storage they assert against is the one the code actually writes.
No Postgres, no credentials, no network. See `tests/_fake_appwrite.py`.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from _fake_appwrite import FakeAppwriteTables
from job_os.ingest.providers import RawPosting
from job_os.ingest.upsert import deactivate_missing, mark_duplicates, upsert_postings

#: `upsert_postings`, `deactivate_missing` and `mark_duplicates` all take a
#: session and immediately `del` it -- the parameter survives only so their
#: callers did not need a signature change. Named here rather than passing a
#: bare `None` at fourteen call sites and leaving the reason to be guessed.
NO_SESSION: Any = None


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


def fetch(appwrite: FakeAppwriteTables, source: str, source_id: str) -> dict[str, Any]:
    """The one stored row for this posting, as Appwrite would serve it.

    `find` insists on exactly one match. That is not pedantry: most of this file
    is about a re-crawl updating a row rather than writing a second one, and an
    assertion that silently read the first of two would pass through precisely
    the failure it exists to catch.
    """
    return appwrite.find(source=source, source_id=source_id)


def when(row: dict[str, Any], column: str) -> datetime | None:
    """A datetime column, parsed. Appwrite serves these as ISO 8601 text."""
    raw = row.get(column)
    return None if raw is None else datetime.fromisoformat(str(raw))


@pytest.fixture
def source() -> str:
    """A source namespace unique to one test, so tests cannot collide."""
    return f"test_{uuid.uuid4().hex[:12]}"


@pytest.fixture
def crawl_run() -> uuid.UUID:
    """A crawl run id.

    This was a real `crawl_runs` row, because `job_postings.last_crawl_run_id`
    was a foreign key and the deactivation rule is only sound when the run it
    names actually happened. Appwrite has no foreign keys and the column is a
    plain 36-character string (see `bootstrap_appwrite_job_postings.py`), so
    the referential half of that guarantee no longer exists to be tested here.
    Manufacturing a Postgres row to obtain a UUID would only have re-introduced
    a database dependency these tests no longer have.
    """
    return uuid.uuid4()


@pytest.fixture
def other_run() -> uuid.UUID:
    return uuid.uuid4()


async def test_same_job_twice_preserves_first_seen_and_bumps_last_seen(
    fake_appwrite: FakeAppwriteTables, source: str
) -> None:
    posting = make_posting(source=source, token="acme")
    first_run = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    second_run = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)

    stats = await upsert_postings(NO_SESSION, [posting], seen_at=first_run)
    assert stats.inserted == 1

    stats = await upsert_postings(NO_SESSION, [posting], seen_at=second_run)
    assert stats.inserted == 0
    # Identical content, so this is a re-sighting rather than an edit.
    assert stats.unchanged == 1
    assert stats.updated == 0

    row = fetch(fake_appwrite, source, "acme:1")
    assert when(row, "first_seen_at") == first_run, "a re-crawl must never re-date a posting"
    assert when(row, "last_seen_at") == second_run


async def test_repeated_upserts_never_duplicate_the_row(
    fake_appwrite: FakeAppwriteTables, source: str
) -> None:
    posting = make_posting(source=source, token="acme")
    for day in range(1, 6):
        await upsert_postings(
            NO_SESSION, [posting], seen_at=datetime(2026, 8, day, tzinfo=UTC)
        )

    all_rows = [row for row in fake_appwrite.all_rows() if row["source"] == source]
    assert len(all_rows) == 1
    assert when(all_rows[0], "first_seen_at") == datetime(2026, 8, 1, tzinfo=UTC)
    assert when(all_rows[0], "last_seen_at") == datetime(2026, 8, 5, tzinfo=UTC)


async def test_edited_posting_updates_values_but_keeps_first_seen(
    fake_appwrite: FakeAppwriteTables, source: str
) -> None:
    first_run = datetime(2026, 7, 1, tzinfo=UTC)
    await upsert_postings(
        NO_SESSION, [make_posting(source=source, token="acme")], seen_at=first_run
    )
    before = fetch(fake_appwrite, source, "acme:1")
    # Postgres called this `updated_at`. The Appwrite table's own `$updatedAt`
    # moves on every write including a pure re-sighting, so the column that
    # carries the old meaning -- "the employer changed it" -- is
    # `content_updated_at`, which `_write_batch` only re-sends when the content
    # hash moved. That is the one asserted on here and in the test below.
    original_updated_at = before["content_updated_at"]

    edited = make_posting(
        source=source,
        token="acme",
        title="Senior Software Engineer",
        description="Now with more responsibility and a different scope entirely.",
    )
    second_run = datetime(2026, 8, 1, tzinfo=UTC)
    stats = await upsert_postings(NO_SESSION, [edited], seen_at=second_run)
    assert stats.updated == 1
    assert stats.unchanged == 0

    row = fetch(fake_appwrite, source, "acme:1")
    assert row["title"] == "Senior Software Engineer"
    assert when(row, "first_seen_at") == first_run
    assert when(row, "last_seen_at") == second_run
    assert row["content_updated_at"] != original_updated_at


async def test_unchanged_recrawl_does_not_move_updated_at(
    fake_appwrite: FakeAppwriteTables, source: str
) -> None:
    """`content_updated_at` must mean "the employer changed it", not "a crawler ran"."""
    posting = make_posting(source=source, token="acme")
    await upsert_postings(NO_SESSION, [posting], seen_at=datetime(2026, 7, 1, tzinfo=UTC))
    original = fetch(fake_appwrite, source, "acme:1")["content_updated_at"]

    await upsert_postings(NO_SESSION, [posting], seen_at=datetime(2026, 8, 1, tzinfo=UTC))
    assert fetch(fake_appwrite, source, "acme:1")["content_updated_at"] == original


async def test_field_cleared_on_the_board_is_cleared_in_the_index(
    fake_appwrite: FakeAppwriteTables, source: str
) -> None:
    """A withdrawn salary band must not survive as a stale value.

    This is the case a `coalesce(new, old)` upsert gets wrong: coalesce keeps the
    old value whenever the new one is NULL, so the index would keep advertising a
    salary the employer has taken down. On Appwrite the same mistake would be
    dropping the null from the update payload instead of sending it.
    """
    with_salary = make_posting(source=source, token="acme")
    with_salary.salary_min = 150_000
    with_salary.salary_max = 200_000
    with_salary.salary_currency = "USD"
    await upsert_postings(NO_SESSION, [with_salary], seen_at=datetime(2026, 7, 1, tzinfo=UTC))
    assert fetch(fake_appwrite, source, "acme:1")["salary_min"] == 150_000

    without_salary = make_posting(
        source=source, token="acme", description="Body changed so the hash changes too."
    )
    await upsert_postings(
        NO_SESSION, [without_salary], seen_at=datetime(2026, 8, 1, tzinfo=UTC)
    )

    row = fetch(fake_appwrite, source, "acme:1")
    assert row["salary_min"] is None
    assert row["salary_max"] is None


async def test_deactivate_only_touches_boards_this_run_read(
    fake_appwrite: FakeAppwriteTables, source: str, crawl_run: uuid.UUID, other_run: uuid.UUID
) -> None:
    run_one = crawl_run
    run_two = other_run
    postings = [
        make_posting(source=source, token="acme", external_id="1"),
        make_posting(source=source, token="acme", external_id="2", title="Data Engineer"),
        make_posting(source=source, token="other", external_id="9", title="Designer"),
    ]
    await upsert_postings(
        NO_SESSION, postings, run_id=run_one, seen_at=datetime(2026, 7, 1, tzinfo=UTC)
    )

    # Second crawl: acme now lists only posting 1. `other` was not fetched at all.
    await upsert_postings(
        NO_SESSION,
        [make_posting(source=source, token="acme", external_id="1")],
        run_id=run_two,
        seen_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    deactivated = await deactivate_missing(
        NO_SESSION, source=source, board_token="acme", run_id=run_two
    )
    assert deactivated == 1

    still_listed = fetch(fake_appwrite, source, "acme:1")
    dropped = fetch(fake_appwrite, source, "acme:2")
    untouched_board = fetch(fake_appwrite, source, "other:9")

    assert still_listed["active"] is True
    assert dropped["active"] is False
    assert dropped["inactive_since"] is not None
    # The board we never fetched must be left alone. Deactivating it would close a
    # company's whole board because of a request that did not happen.
    assert untouched_board["active"] is True


async def test_deactivation_never_deletes(
    fake_appwrite: FakeAppwriteTables, source: str, crawl_run: uuid.UUID, other_run: uuid.UUID
) -> None:
    await upsert_postings(
        NO_SESSION,
        [make_posting(source=source, token="acme", external_id="1")],
        run_id=crawl_run,
        seen_at=datetime(2026, 7, 1, tzinfo=UTC),
    )
    await deactivate_missing(
        NO_SESSION, source=source, board_token="acme", run_id=other_run
    )

    row = fetch(fake_appwrite, source, "acme:1")
    assert row["active"] is False
    # The history survives, so a closure can be shown honestly and a later repost
    # can still be recognised as the same posting.
    assert when(row, "first_seen_at") == datetime(2026, 7, 1, tzinfo=UTC)


async def test_a_row_no_crawl_ever_stamped_is_left_alone(
    fake_appwrite: FakeAppwriteTables, source: str, crawl_run: uuid.UUID
) -> None:
    """`last_crawl_run_id!=<run>` does not reach a row where that column is NULL.

    SQL three-valued logic: `col != 'x'` is NULL, not true, when col is NULL, so
    the row is not returned and not deactivated. Every posting the sweep writes
    carries a run id (`ingest/worker.py` always passes one, and `to_row` always
    stamps it), so this is out-of-contract rather than a live path -- but the
    rows migrated in from Postgres are a real population that could carry a
    null here, and "the sweep silently never closes them" is the shape that
    would produce. Pinned so the behaviour is a decision rather than a
    discovery.

    UNVERIFIED against the live service: this is what MariaDB does with the
    comparison Appwrite's adapter emits, modelled in `tests/_fake_appwrite.py`,
    not something measured against the real table.
    """
    await upsert_postings(
        NO_SESSION,
        [make_posting(source=source, token="acme", external_id="1")],
        seen_at=datetime(2026, 7, 1, tzinfo=UTC),
    )
    assert fetch(fake_appwrite, source, "acme:1")["last_crawl_run_id"] is None

    deactivated = await deactivate_missing(
        NO_SESSION, source=source, board_token="acme", run_id=crawl_run
    )

    assert deactivated == 0
    assert fetch(fake_appwrite, source, "acme:1")["active"] is True


async def test_repost_reactivates_and_counts_without_resetting_history(
    fake_appwrite: FakeAppwriteTables, source: str, crawl_run: uuid.UUID, other_run: uuid.UUID
) -> None:
    posting = make_posting(source=source, token="acme")
    first_seen = datetime(2026, 6, 1, tzinfo=UTC)
    await upsert_postings(NO_SESSION, [posting], run_id=crawl_run, seen_at=first_seen)
    await deactivate_missing(
        NO_SESSION, source=source, board_token="acme", run_id=other_run
    )
    assert fetch(fake_appwrite, source, "acme:1")["active"] is False

    reposted_at = datetime(2026, 8, 1, tzinfo=UTC)
    await upsert_postings(NO_SESSION, [posting], run_id=other_run, seen_at=reposted_at)

    row = fetch(fake_appwrite, source, "acme:1")
    assert row["active"] is True
    assert row["inactive_since"] is None
    assert row["repost_count"] == 1
    # The whole point: a reposted role does not get to look brand new.
    assert when(row, "first_seen_at") == first_seen
    assert when(row, "last_seen_at") == reposted_at


async def test_posted_at_estimated_is_derived_from_basis(
    fake_appwrite: FakeAppwriteTables, source: str
) -> None:
    """The flag can never disagree with the basis it came from.

    It was a generated column in Postgres, which made that free. Appwrite has no
    generated columns, so `to_row` computes it at write time instead -- the same
    guarantee, now resting on one line of Python rather than on the database,
    which is exactly why it is worth a test.
    """
    published = make_posting(
        source=source, token="acme", external_id="1", basis="published"
    )
    from_updated = make_posting(
        source=source, token="acme", external_id="2", basis="updated", title="Analyst"
    )
    never_dated = make_posting(
        source=source, token="acme", external_id="3", basis="first_crawl", title="Chef"
    )
    await upsert_postings(NO_SESSION, [published, from_updated, never_dated])

    assert fetch(fake_appwrite, source, "acme:1")["posted_at_estimated"] is False
    assert fetch(fake_appwrite, source, "acme:2")["posted_at_estimated"] is True
    assert fetch(fake_appwrite, source, "acme:3")["posted_at_estimated"] is True


async def test_duplicate_ids_in_one_batch_are_skipped_not_fatal(
    fake_appwrite: FakeAppwriteTables, source: str
) -> None:
    """A board listing one posting id twice must not take the whole batch down.

    The fake enforces `uq_source_pair`, the real UNIQUE index on
    (source, source_id), so a regression here is a 409 rather than a quietly
    doubled table.
    """
    posting = make_posting(source=source, token="acme")
    stats = await upsert_postings(NO_SESSION, [posting, posting])
    assert stats.inserted == 1
    assert stats.skipped == 1
    assert len(fake_appwrite.all_rows()) == 1


async def test_marked_duplicate_keeps_its_row(
    fake_appwrite: FakeAppwriteTables, source: str
) -> None:
    a = make_posting(source=source, token="acme", external_id="1")
    b = make_posting(
        source=source, token="acme", external_id="2", location="New York, NY"
    )
    await upsert_postings(NO_SESSION, [a, b])
    # `mark_duplicates` keys on `source_posting_id`, the stable non-Appwrite
    # identity `to_row` mints, not on Appwrite's own `$id`.
    canonical = uuid.UUID(fetch(fake_appwrite, source, "acme:1")["source_posting_id"])
    duplicate = uuid.UUID(fetch(fake_appwrite, source, "acme:2")["source_posting_id"])

    marked = await mark_duplicates(NO_SESSION, [(duplicate, canonical, "exact_key", None)])
    assert marked == 1

    refreshed = fetch(fake_appwrite, source, "acme:2")
    assert refreshed["canonical_id"] == str(canonical)
    assert refreshed["duplicate_reason"] == "exact_key"
    # Marked, not deleted: the duplicate's own URL still resolves and a wrong
    # merge stays reversible.
    assert refreshed["source_url"]


async def test_a_second_merge_cannot_repoint_an_already_merged_row(
    fake_appwrite: FakeAppwriteTables, source: str
) -> None:
    """The `isNull(canonical_id)` guard, which is why `mark_duplicates` needs
    `queries` at all -- a plain `attribute=value` filter string cannot express
    a null check, and `canonical_id=null` becoming `equal` with a null value is
    rejected by Appwrite outright."""
    await upsert_postings(
        NO_SESSION,
        [
            make_posting(source=source, token="acme", external_id="1"),
            make_posting(source=source, token="acme", external_id="2", location="NY"),
            make_posting(source=source, token="acme", external_id="3", location="LA"),
        ],
    )
    first = uuid.UUID(fetch(fake_appwrite, source, "acme:1")["source_posting_id"])
    second = uuid.UUID(fetch(fake_appwrite, source, "acme:3")["source_posting_id"])
    duplicate = uuid.UUID(fetch(fake_appwrite, source, "acme:2")["source_posting_id"])

    assert await mark_duplicates(NO_SESSION, [(duplicate, first, "exact_key", None)]) == 1
    assert await mark_duplicates(NO_SESSION, [(duplicate, second, "exact_key", None)]) == 0

    assert fetch(fake_appwrite, source, "acme:2")["canonical_id"] == str(first)


async def test_content_hash_ignores_a_trailing_boilerplate_edit(
    fake_appwrite: FakeAppwriteTables, source: str
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
    await upsert_postings(NO_SESSION, [original], seen_at=datetime(2026, 7, 1, tzinfo=UTC))

    tail_edited = make_posting(
        source=source, token="acme", description=body + "Footer v2, legal text changed."
    )
    stats = await upsert_postings(
        NO_SESSION, [tail_edited], seen_at=datetime(2026, 8, 1, tzinfo=UTC)
    )
    assert stats.unchanged == 1
    assert stats.updated == 0


async def test_upsert_is_scoped_per_source(fake_appwrite: FakeAppwriteTables) -> None:
    """Two providers can use the same board token without colliding.

    Worth more here than it was on Postgres: the lookup that decides
    insert-versus-update queries `source_id` alone and only then keys the result
    by `(source, source_id)`. A version that keyed on `source_id` alone would
    turn these two postings into one row, and `uq_source_pair` would not stop it.
    """
    left = f"test_{uuid.uuid4().hex[:12]}"
    right = f"test_{uuid.uuid4().hex[:12]}"
    await upsert_postings(
        NO_SESSION,
        [
            make_posting(source=left, token="acme", external_id="1"),
            make_posting(source=right, token="acme", external_id="1"),
        ],
    )
    assert fetch(fake_appwrite, left, "acme:1")["source"] == left
    assert fetch(fake_appwrite, right, "acme:1")["source"] == right


async def test_last_seen_moves_forward_across_many_crawls(
    fake_appwrite: FakeAppwriteTables, source: str
) -> None:
    posting = make_posting(source=source, token="acme")
    base = datetime(2026, 8, 1, tzinfo=UTC)
    for hours in (0, 6, 12, 18):
        await upsert_postings(
            NO_SESSION, [posting], seen_at=base + timedelta(hours=hours)
        )
    row = fetch(fake_appwrite, source, "acme:1")
    assert when(row, "first_seen_at") == base
    assert when(row, "last_seen_at") == base + timedelta(hours=18)
