"""Idempotent writes into `job_postings`.

The contract, which the tests in `tests/test_ingest_upsert.py` pin:

  * Upserting the same posting twice **preserves `first_seen_at`** and **bumps
    `last_seen_at`**. Those two columns are the honest-freshness feature, and a
    naive `ON CONFLICT DO UPDATE SET first_seen_at = now()` would destroy the
    first one and turn every re-crawl into a fake new posting. That is precisely
    the behaviour competitors were caught doing.
  * A posting whose `content_hash` is unchanged keeps every stored value except
    `last_seen_at` and the run id, and its `updated_at` does not move, so
    `updated_at` keeps meaning "the employer edited this". Postgres still writes
    a new tuple version for any UPDATE, but because no indexed column changes it
    can take the heap-only path and skip GIN and HNSW index maintenance, which at
    a few hundred thousand rows a sweep is most of the cost.
  * A posting that vanishes from its board is **deactivated, not deleted**, and
    only when the board was genuinely re-read (see `deactivate_missing`).
  * A deactivated posting that comes back is reactivated with `repost_count`
    incremented, so a perpetually reposted role is visible as one.

**Back on Postgres.** Between 2026-08-18 and 2026-08-31 this module wrote to
Appwrite TablesDB instead. Two things came back with it, and both are contract,
not cleanup:

  * `ON CONFLICT DO UPDATE` makes read-decide-write one atomic statement again.
    The Appwrite version had to look a batch up, decide in Python, then write,
    with a real gap in the middle that two concurrent crawlers could race
    through. That gap is closed rather than merely documented now.
  * `search_text` is gone as a stored column. Postgres computes `search_vector`
    as a STORED generated column from `title`/`company_name`/`location`/
    `jd_clean` (see `db/models/job_posting.py`), so no writer has to remember to
    keep a derived copy in step, and a second copy of the body is not stored.
    `ingest/hydrate.py` used to have to reproduce `search_text` byte-for-byte
    when it replaced a description; it no longer can get that wrong.

Appwrite bills reads per row and the crawl exhausted the quota; see the model's
own docstring for the measured numbers.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import structlog
from sqlalchemy import and_, case, literal_column, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from job_os.db.models.job_posting import JobPosting
from job_os.ingest import normalize
from job_os.ingest.providers import RawPosting

log = structlog.get_logger(__name__)

#: Rows per INSERT statement. Large enough that the round trips disappear, small
#: enough that one bad batch is cheap to retry and the statement stays under any
#: parameter ceiling.
BATCH_SIZE = 500


@dataclass(slots=True)
class UpsertStats:
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    reactivated: int = 0
    deactivated: int = 0
    skipped: int = 0

    def merge(self, other: UpsertStats) -> None:
        self.inserted += other.inserted
        self.updated += other.updated
        self.unchanged += other.unchanged
        self.reactivated += other.reactivated
        self.deactivated += other.deactivated
        self.skipped += other.skipped


def to_row(
    posting: RawPosting,
    *,
    run_id: uuid.UUID | None,
    seen_at: datetime,
    company_name: str | None = None,
    company_domain: str | None = None,
) -> dict[str, object]:
    """Flatten a `RawPosting` into an insertable row.

    `company_name` / `company_domain` come from the token's curated entry when
    there is one. Lever and Ashby do not report the employer's name at all, so
    without that the board token is the best available answer, which is why the
    curated list matters for dedupe quality rather than only for display.

    Two columns the Appwrite version of this function had to fill are absent,
    because Postgres derives them and no writer can now get them wrong:
    `posted_at_estimated` (generated from `posted_at_basis`) and `search_text`
    (which does not exist; `search_vector` is generated from the row).
    """
    name = company_name or posting.company_name
    domain = company_domain or posting.company_domain
    description = posting.jd_clean

    return {
        "source": posting.source,
        "source_id": posting.source_id,
        "board_token": posting.board_token,
        # Not truncated. The Appwrite version cut this to 255 characters because
        # that was the column's cap there; Postgres's `external_id` is an
        # unbounded `String`, and the scraper's URL-shaped ids are worth keeping
        # whole since `ingest/hydrate.py` rebuilds a `RawPosting` from them.
        "external_id": posting.external_id,
        "source_url": posting.source_url,
        "company_name": name,
        "company_domain": domain,
        "title": posting.title,
        "location": posting.location,
        "country_code": posting.country_code,
        "remote": posting.remote,
        "anywhere": posting.anywhere,
        "workplace_type": posting.workplace_type,
        "employment_type": posting.employment_type,
        "department": posting.department,
        "salary_min": posting.salary_min,
        "salary_max": posting.salary_max,
        "salary_currency": posting.salary_currency,
        "salary_interval": posting.salary_interval,
        "jd_raw": posting.jd_raw or None,
        "jd_clean": description,
        "jd_hydrated": posting.jd_hydrated,
        "jd_parsed": posting.extra or {},
        "content_hash": normalize.content_hash(
            name, posting.title, posting.location, description, domain=domain
        ),
        "dedupe_key": normalize.dedupe_key(
            name, posting.title, posting.location, domain=domain
        ),
        "posted_at": posting.posted_at,
        "posted_at_basis": posting.posted_at_basis,
        "closes_at": posting.closes_at,
        "active": True,
        "first_seen_at": seen_at,
        "last_seen_at": seen_at,
        "last_crawl_run_id": run_id,
    }


#: Columns a re-crawl is allowed to overwrite. `first_seen_at` is absent by
#: design: it is the one fact a later crawl can never improve on, and every other
#: honest-freshness claim rests on it.
_MUTABLE_COLUMNS = (
    "source_url",
    "company_name",
    "company_domain",
    "title",
    "location",
    "country_code",
    "remote",
    "anywhere",
    "workplace_type",
    "employment_type",
    "department",
    "salary_min",
    "salary_max",
    "salary_currency",
    "salary_interval",
    "jd_raw",
    "jd_clean",
    "jd_hydrated",
    "jd_parsed",
    "content_hash",
    "dedupe_key",
    "posted_at",
    "posted_at_basis",
    "closes_at",
)


async def upsert_postings(
    session: AsyncSession,
    postings: list[RawPosting],
    *,
    run_id: uuid.UUID | None = None,
    seen_at: datetime | None = None,
    company_names: dict[tuple[str, str], tuple[str | None, str | None]] | None = None,
) -> UpsertStats:
    """Write postings, preserving history. Returns what actually changed."""
    stats = UpsertStats()
    if not postings:
        return stats

    now = seen_at or datetime.now(UTC)
    lookup = company_names or {}

    rows: list[dict[str, object]] = []
    seen_ids: set[tuple[str, str]] = set()
    for posting in postings:
        identity = (posting.source, posting.source_id)
        if identity in seen_ids:
            # One board listing the same posting id twice would make the INSERT
            # fail with "cannot affect row a second time", so the duplicate is
            # dropped here rather than taking the whole batch down.
            stats.skipped += 1
            continue
        seen_ids.add(identity)
        name, domain = lookup.get((posting.source, posting.board_token), (None, None))
        rows.append(
            to_row(
                posting,
                run_id=run_id,
                seen_at=now,
                company_name=name,
                company_domain=domain,
            )
        )

    for start in range(0, len(rows), BATCH_SIZE):
        batch = rows[start : start + BATCH_SIZE]
        stats.merge(await _write_batch(session, batch, now=now))
    return stats


async def _write_batch(
    session: AsyncSession, rows: list[dict[str, object]], *, now: datetime
) -> UpsertStats:
    stats = UpsertStats()
    statement = insert(JobPosting).values(rows)
    excluded = statement.excluded

    # Which of this batch's postings are currently inactive, read as a CTE of
    # the very statement that reactivates them.
    #
    # `reactivated` cannot come out of the upsert's own RETURNING clause:
    # RETURNING sees the row AFTER the update, where `active` has already been
    # set to True and `repost_count` has already been incremented, and
    # PostgreSQL has no `RETURNING OLD` before 18. A separate SELECT before the
    # INSERT would answer it, at the cost of a second statement with a second
    # snapshot -- so the count could disagree with the write it describes. A CTE
    # is read from the snapshot the statement started with, so this is the
    # before-state of exactly the rows this statement is about to change, with
    # no extra round trip and no second snapshot.
    keys = [(row["source"], row["source_id"]) for row in rows]
    was_inactive = (
        select(JobPosting.source, JobPosting.source_id)
        .where(
            tuple_(JobPosting.source, JobPosting.source_id).in_(keys),
            JobPosting.active.is_(False),
        )
        .cte("was_inactive")
    )
    statement = statement.add_cte(was_inactive)

    # Postgres decides per row whether the posting actually changed, so the
    # unchanged case costs no extra round trip and cannot lose a race the way a
    # read-then-write in Python would.
    changed = JobPosting.content_hash.is_distinct_from(excluded.content_hash)

    # `case(..., else_=<current value>)` rather than `coalesce`. Coalesce looks
    # equivalent and is not: a field that legitimately becomes NULL (a salary
    # band withdrawn, a deadline removed) would fall through to the old value and
    # the row would keep asserting something the board no longer says.
    mutable = {
        column: case((changed, getattr(excluded, column)), else_=getattr(JobPosting, column))
        for column in _MUTABLE_COLUMNS
    }

    returning = statement.on_conflict_do_update(
        constraint="uq_job_postings_source_pair",
        set_={
            # Always. This crawl saw it, whether or not anything changed, and
            # that is the whole point of last_seen_at.
            "last_seen_at": excluded.last_seen_at,
            "last_crawl_run_id": excluded.last_crawl_run_id,
            "active": True,
            "inactive_since": None,
            # Back on a board after having been dropped is a repost. Counting it
            # is how a role that has been "new" nine times becomes visible as one.
            "repost_count": JobPosting.repost_count
            + case((JobPosting.active.is_(False), 1), else_=0),
            **mutable,
            # Only moves when the content moved, so `updated_at` keeps meaning
            # "the posting changed" rather than "a crawler ran".
            "updated_at": case((changed, now), else_=JobPosting.updated_at),
        },
    ).returning(
        # `xmax = 0` is the standard Postgres test for a row this statement
        # inserted rather than updated. It is the only way to tell the two apart
        # from a single upsert, and it beats comparing timestamps because it does
        # not depend on a value round-tripping through the driver unchanged.
        literal_column("xmax").op("=")(literal_column("0")).label("was_inserted"),
        JobPosting.updated_at,
        # An IN over the CTE rather than a correlated EXISTS, and the
        # difference is a bug that was actually written here first. Inside a
        # RETURNING clause SQLAlchemy has no enclosing SELECT to correlate
        # `JobPosting` against, so `EXISTS (SELECT 1 FROM was_inactive WHERE
        # was_inactive.source = job_postings.source ...)` compiled to `FROM
        # was_inactive, job_postings` -- a cross join answering "was ANY row of
        # this batch inactive", which reported every posting in a batch as
        # reactivated as soon as one of them was. `.correlate(JobPosting)` did
        # not fix it. An uncorrelated IN needs no correlation to be right.
        tuple_(JobPosting.source, JobPosting.source_id)
        .in_(select(was_inactive.c.source, was_inactive.c.source_id))
        .label("was_reactivated"),
    )

    result = await session.execute(returning)
    for was_inserted, updated_at, was_reactivated in result.all():
        if was_inserted:
            stats.inserted += 1
        elif updated_at == now:
            # `updated_at` only moves when the content hash moved, so this is the
            # honest count of postings the employer actually edited.
            stats.updated += 1
        else:
            stats.unchanged += 1
        if was_reactivated:
            # Not exclusive with the three above: a posting can come back
            # unchanged, or come back edited. `repost_count` on the row is the
            # durable version of this; the counter is the per-run report.
            stats.reactivated += 1
    return stats


async def deactivate_missing(
    session: AsyncSession,
    *,
    source: str,
    board_token: str,
    run_id: uuid.UUID,
    at: datetime | None = None,
) -> int:
    """Mark postings this board no longer lists as inactive.

    Scoped to one board and one run on purpose. "Absent from the crawl" is only
    evidence of closure if that board's current list was actually read, so the
    caller must only call this for a board whose fetch returned LIVE or EMPTY. A
    304 or a timeout means we did not see the list, and deactivating on either
    would close a company's whole board because one request was slow.

    Rows are never deleted. A closed posting is a fact worth showing, and keeping
    it means `first_seen_at` survives if the role is reposted later.
    """
    now = at or datetime.now(UTC)
    statement = (
        update(JobPosting)
        .where(
            and_(
                JobPosting.source == source,
                JobPosting.board_token == board_token,
                JobPosting.active.is_(True),
                # Anything this run touched has the run id on it. Everything else
                # under this board was not in the list we just read.
                (JobPosting.last_crawl_run_id.is_distinct_from(run_id)),
            )
        )
        .values(active=False, inactive_since=now, updated_at=now)
        .returning(JobPosting.id)
    )
    result = await session.execute(statement)
    return len(result.all())


async def mark_duplicates(
    session: AsyncSession,
    links: list[tuple[uuid.UUID, uuid.UUID, str, float | None]],
) -> int:
    """Point duplicate rows at their canonical row.

    The duplicate keeps its own row: its URL still resolves, the merge is
    reversible if it was wrong, and the read path filters on `canonical_id IS
    NULL` rather than relying on a delete having been correct.

    `duplicate_id`/`canonical_id` are `job_postings.id` values again, the
    table's own primary key. The Appwrite version keyed on a separate
    `source_posting_id` column that existed only because Appwrite mints its own
    opaque `$id`.
    """
    if not links:
        return 0
    marked = 0
    for duplicate_id, canonical_id, reason, score in links:
        if duplicate_id == canonical_id:
            continue
        result = await session.execute(
            update(JobPosting)
            .where(JobPosting.id == duplicate_id, JobPosting.canonical_id.is_(None))
            .values(canonical_id=canonical_id, duplicate_reason=reason, duplicate_score=score)
            .returning(JobPosting.id)
        )
        marked += len(result.all())
    return marked
