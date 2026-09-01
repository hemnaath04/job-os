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
            "bytes_before": self.bytes_before,
            "bytes_after": self.bytes_after,
            "mb_reclaimed": round((self.bytes_before - self.bytes_after) / 1e6, 1),
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
    result.bytes_after = await _bytes(session)
    result.duration_s = time.monotonic() - started
    log.info("ingest.prune_done", **result.as_dict())
    return result
