"""Keep the index a useful size instead of an exhaustive one.

WHY THIS EXISTS
    The crawl was written to maximise coverage and had no opposing force, so
    the table grew at roughly 47 MB an hour with nothing to stop it. That is
    how a search index becomes a storage bill: 55,340 rows and 243 MB inside a
    day, against a projected 629,000 rows and 2.76 GB for the full corpus.

WHAT A CAP IS NOT
    The obvious cap, filtering on title, was measured and rejected. Keeping
    only titles matching intern-and-engineering left 278 rows of 55,340, and
    the survivors were New Zealand electrical engineering co-ops: precision
    without relevance. Deciding what a posting is worth from its title alone
    throws away the rows a search would actually have ranked.

WHAT WORKS INSTEAD, measured per provider on the live index:

    source          rows    swe+intern   rows per useful posting
    oracle_cloud   35,948           27                     1,331
    greenhouse     11,810           96                       123
    ashby           4,488           29                       155
    lever           3,094           69                        45

    Oracle is 65% of the index and answers 27 relevant postings. It is not
    that its rows are bad; a handful of very large tenants dominate it, and
    they are the wrong kind of employer for this user. The lever that pays is
    which boards get crawled, not which rows get kept, and that lives in the
    scheduler's `--providers`, not here.

    What this module does is the part that cannot live in provider selection:
    a posting that has aged out is dead whatever board it came from.

TWO RULES, deliberately dull
    1. AGE. A posting older than the horizon is delisted in practice. 12,145
       rows are already past 60 days.
    2. CEILING. A hard maximum, oldest first, so that a bad crawl or a new
       provider cannot silently do what the last one did. This is the rule
       that exists because of what already happened, not because of what the
       data says today.

Deletes rather than deactivates. `active=false` still costs the bytes, and
bytes are the entire problem being solved.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from job_os.db.session import engine as app_engine

log = structlog.get_logger(__name__)

#: Postings older than this are gone.
#:
#: Thirty rather than sixty, and what made that affordable is dropping
#: oracle_cloud. With three providers the whole corpus at a 30-day horizon
#: projects to 76,460 rows, about 0.43 GB, so the horizon is no longer trading
#: coverage against storage the way it was when one vendor supplied 65% of the
#: table.
#:
#: The risk a longer horizon hedged against is real but small: some boards
#: backdate `posted_at`, so a live posting can look older than it is. Against
#: that, a stale row in a job search is worse than a missing one, because it
#: costs an application rather than an impression.
DEFAULT_MAX_AGE_DAYS = 30

#: The backstop, sized against the failure rather than the forecast.
#:
#: Full coverage projects to 76,460 rows, so this should never bind. It sits at
#: 100,000 because bytes per row is not a constant: 4,392 at 35% hydration,
#: 5,627 an hour later, and a fully hydrated row runs closer to 8,500. At that
#: upper figure 100,000 rows is about 850 MB, which still fits an Essential-0
#: database. A ceiling that only holds while the rows stay thin is not a
#: ceiling.
DEFAULT_MAX_ROWS = 100_000

# Never delete a posting a user is actually tracking, whatever its age. The
# join is to `jobs`, which is where an imported posting lands.
#
# These are written out in full rather than composed from a shared fragment.
# An f-string that builds SQL is indistinguishable, to a reader and to ruff,
# from one that interpolates a value, and this file deletes rows: the version
# that cannot be misread is worth the repetition.
_COUNT_AGED_OUT = """
    select count(*) from job_postings
    where posted_at < now() - make_interval(days => :days)
      and not exists (
          select 1 from jobs j
          where j.source_url is not null
            and j.source_url = job_postings.source_url
      )
"""

_COUNT_PROTECTED = """
    select count(*) from job_postings
    where posted_at < now() - make_interval(days => :days)
      and exists (
          select 1 from jobs j
          where j.source_url is not null
            and j.source_url = job_postings.source_url
      )
"""

_DELETE_AGED_OUT = """
    delete from job_postings
    where posted_at < now() - make_interval(days => :days)
      and not exists (
          select 1 from jobs j
          where j.source_url is not null
            and j.source_url = job_postings.source_url
      )
"""

_DELETE_OVER_CEILING = """
    delete from job_postings where id in (
        select id from job_postings
        where not exists (
            select 1 from jobs j
            where j.source_url is not null
              and j.source_url = job_postings.source_url
        )
        order by posted_at asc nulls first
        limit :excess
    )
"""


@dataclass(slots=True)
class PruneResult:
    aged_out: int = 0
    over_ceiling: int = 0
    protected_kept: int = 0
    rows_before: int = 0
    rows_after: int = 0
    bytes_before: int = 0
    bytes_after: int = 0
    dead_before: int = 0
    dead_after: int = 0
    vacuumed: bool = False
    duration_s: float = 0.0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "aged_out": self.aged_out,
            "over_ceiling": self.over_ceiling,
            "protected_kept": self.protected_kept,
            "rows_before": self.rows_before,
            "rows_after": self.rows_after,
            "deleted": self.rows_before - self.rows_after,
            "mb_on_disk": round(self.bytes_after / 1e6, 1),
            # NOT "space reclaimed". A plain VACUUM returns pages to the table's
            # own free space map, not to the filesystem, so on a table that is
            # also being inserted into this is normally about zero, and that is
            # the system working: the hour's deletes are refilled by the hour's
            # inserts and the file stops growing. Only VACUUM FULL shrinks the
            # file, and it takes an exclusive lock, so it is not run here.
            #
            # Reported because an earlier version called this `mb_reclaimed`,
            # read 0.0 every run, and was taken as evidence of unreclaimed
            # bloat. It was not: dead tuples were 10% and autovacuum had run
            # ten times. `dead_after` is the field to watch for that.
            "mb_on_disk_change": round((self.bytes_after - self.bytes_before) / 1e6, 1),
            "dead_before": self.dead_before,
            "dead_after": self.dead_after,
            "vacuumed": self.vacuumed,
            "duration_s": round(self.duration_s, 2),
            "errors": self.errors[:10],
        }


async def _count(session: AsyncSession) -> int:
    return int((await session.execute(text("select count(*) from job_postings"))).scalar_one())


async def _bytes(session: AsyncSession) -> int:
    return int(
        (
            await session.execute(text("select pg_total_relation_size('job_postings')"))
        ).scalar_one()
    )


async def _dead_tuples(session: AsyncSession) -> int:
    """Rows deleted but not yet reusable. This is the real bloat signal."""
    value = (
        await session.execute(
            text(
                "select coalesce(n_dead_tup, 0) from pg_stat_user_tables "
                "where relname = 'job_postings'"
            )
        )
    ).scalar()
    return int(value or 0)


async def _vacuum(session: AsyncSession) -> None:
    """Plain VACUUM ANALYZE, never FULL.

    Two jobs. It returns this pass's deleted rows to the free space map now,
    rather than whenever autovacuum next decides to look, so the next hour's
    inserts refill the file instead of extending it. And it refreshes planner
    statistics immediately after several thousand rows leave, which otherwise
    leaves the planner estimating against a table shape that no longer exists.

    FULL is deliberately not used. It would shrink the file, but it takes an
    ACCESS EXCLUSIVE lock and rewrites the whole table, which is not something
    to do hourly underneath a live search. When the file genuinely needs to
    shrink, that is a manual operation in a quiet window.

    VACUUM cannot run inside a transaction block, and a session's connection is
    always inside one. Reusing it raises `InFailedSQLTransactionError` and
    poisons the caller's transaction, which is worse than not vacuuming at all,
    so this takes its own AUTOCOMMIT connection from the application engine.

    Deliberately the module engine rather than `session.get_bind()`. A bind can
    be a Connection rather than an Engine, which is exactly what the test suite
    does when it wraps each test in a rolled-back transaction, and calling
    `.connect()` on one raises `AttributeError`. Reaching for the engine
    directly also means the vacuum never joins the caller's transaction by
    accident.
    """
    del session  # accepted for symmetry with the other helpers here
    async with app_engine.connect() as connection:
        await connection.execution_options(isolation_level="AUTOCOMMIT")
        await connection.exec_driver_sql("vacuum (analyze) job_postings")


async def prune_index(
    session: AsyncSession,
    *,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    max_rows: int = DEFAULT_MAX_ROWS,
    dry_run: bool = False,
) -> PruneResult:
    """Delete aged-out postings, then anything above the ceiling, oldest first."""
    started = time.monotonic()
    result = PruneResult()
    result.rows_before = await _count(session)
    result.bytes_before = await _bytes(session)
    result.dead_before = await _dead_tuples(session)

    # How many rows the age rule would take, and how many it spares because a
    # user is tracking them. Reported separately so a surprising number is
    # visible before it is also permanent.
    result.aged_out = int(
        (
            await session.execute(text(_COUNT_AGED_OUT), {"days": max_age_days})
        ).scalar_one()
    )
    result.protected_kept = int(
        (
            await session.execute(text(_COUNT_PROTECTED), {"days": max_age_days})
        ).scalar_one()
    )

    if not dry_run and result.aged_out:
        await session.execute(text(_DELETE_AGED_OUT), {"days": max_age_days})
        await session.commit()

    # The ceiling runs after the age rule, on what survives it, so the two do
    # not both charge for the same row.
    remaining = await _count(session)
    if remaining > max_rows:
        excess = remaining - max_rows
        result.over_ceiling = excess
        if not dry_run:
            await session.execute(
                text(_DELETE_OVER_CEILING), {"excess": excess}
            )
            await session.commit()

    result.rows_after = await _count(session)

    deleted = result.rows_before - result.rows_after
    if not dry_run and deleted:
        await session.commit()
        try:
            await _vacuum(session)
            result.vacuumed = True
        except Exception as exc:  # noqa: BLE001 - hygiene must not fail the prune
            # A failed vacuum leaves the deletes in place and autovacuum will
            # get there on its own schedule. Reporting the prune as failed
            # because its housekeeping failed would be the wrong way round.
            result.errors.append(f"vacuum: {type(exc).__name__}: {exc}")

    result.bytes_after = await _bytes(session)
    result.dead_after = await _dead_tuples(session)
    result.duration_s = time.monotonic() - started
    log.info("ingest.prune_done", **result.as_dict())
    return result
