"""Collapse job rows that are the same posting saved more than once.

`create_from_url` deduplicated on the raw `source_url` string, scoped to
`source == "url"`. Both halves leak. A link with a `?gh_src=` on it, a trailing
slash, a `www.`, or one that had already arrived through discovery under a
different `source` all produced a second row for one posting -- with its own
card, its own tailoring runs and its own application history.

That is fixed at the source: `source_url_key` holds the comparable form of the
URL and `find_job_by_url` matches on it across every source. This is the
one-off for the rows already written, which the fix leaves readable but does
not merge.

    uv run python -m job_os.scripts.merge_duplicate_jobs --dry-run
    uv run python -m job_os.scripts.merge_duplicate_jobs --apply

Run the dry run first and read it. Unlike the company merge, the rows here are
things the user made: applications, tailored resumes, cover letters and
interview notes hang off a job, and a wrong merge would attach one job's
history to another job's description. Everything is repointed before anything
is deleted, so a crash midway leaves rows that are merged-but-not-yet-tidied
rather than an application pointing at a job that is gone.

Not owner-scoped, for the same reason `merge_duplicate_companies` is not:
`jobs` has no owner column. A posting is a fact about the world that every
user's applications point at. That makes the survivor rule matter more, not
less, which is why it is the same rule the running code uses.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict

from sqlalchemy import func, select, update

from job_os.db.models import Job
from job_os.db.models.application import Application
from job_os.db.models.cover_letter import CoverLetter, CoverLetterVersion
from job_os.db.models.interview import InterviewPrep
from job_os.db.models.resume import ResumeVersion
from job_os.db.session import Base, async_session
from job_os.services.job_identity import canonical_url

# Every table that points at a job. Listed rather than discovered so that a new
# one added later fails loudly here (nothing repoints it, the delete hits the
# foreign key) instead of silently orphaning rows.
_REFERENCES: tuple[tuple[type[Base], str], ...] = (
    (Application, "job_id"),
    (CoverLetter, "job_id"),
    (InterviewPrep, "job_id"),
    # These two name the column differently. Repointed all the same: a resume
    # written for this posting is still written for this posting.
    (ResumeVersion, "spawned_from_job_id"),
    (CoverLetterVersion, "spawned_from_job_id"),
)


def survivor_of(rows: list[Job]) -> Job:
    """The row the others fold into.

    Mirrors `find_job_by_url`, so this script and the running code agree about
    which row is the real one: oldest first, because that is the one the user's
    history is attached to. `id` breaks the tie so the choice cannot change
    between runs.
    """
    return sorted(rows, key=lambda row: (row.created_at, str(row.id)))[0]


async def merge(*, apply: bool) -> int:
    merged = 0
    async with async_session() as session:
        result = await session.execute(select(Job).where(Job.source_url.is_not(None)))
        by_key: dict[str, list[Job]] = defaultdict(list)
        for job in result.unique().scalars().all():
            # Recomputed rather than read off the column: this has to work on a
            # database whose backfill predates a change to the normaliser.
            key = canonical_url(job.source_url)
            if key:
                by_key[key].append(job)

        groups = {key: rows for key, rows in by_key.items() if len(rows) > 1}
        if not groups:
            print("no duplicate jobs")
            return 0

        print(f"{len(groups)} posting(s) saved more than once\n")
        for key, rows in sorted(groups.items()):
            keep = survivor_of(rows)
            losers = [row for row in rows if row.id != keep.id]
            print(f"  {key}")
            print(f"    keep  {keep.id}  source={keep.source}  {keep.title[:48]}")
            for loser in losers:
                attached = []
                for model, column in _REFERENCES:
                    count = await session.scalar(
                        select(func.count())
                        .select_from(model)
                        .where(getattr(model, column) == loser.id)
                    )
                    if count:
                        attached.append(f"{model.__tablename__}={count}")
                print(
                    f"    fold  {loser.id}  source={loser.source}  "
                    f"{loser.title[:40]}  {' '.join(attached) or 'nothing attached'}"
                )
                if not apply:
                    continue
                for model, column in _REFERENCES:
                    await session.execute(
                        update(model)
                        .where(getattr(model, column) == loser.id)
                        .values(**{column: keep.id})
                    )
                # Flushed before the delete so the repoint is already in the
                # transaction: deleting a row an application still references
                # would fail on the foreign key, which is the right failure but
                # a worse one to debug than never getting there.
                await session.flush()
                await session.delete(loser)
                merged += 1

            # The survivor is the oldest, which is not always the best read. A
            # duplicate created later may have parsed when the first one did
            # not, so take the description rather than lose it.
            if not (keep.jd_clean or "").strip():
                donor = next((row for row in losers if (row.jd_clean or "").strip()), None)
                if donor and apply:
                    keep.jd_raw = donor.jd_raw
                    keep.jd_clean = donor.jd_clean
                    keep.jd_parsed = donor.jd_parsed
                    if keep.title in ("", "Untitled") and donor.title:
                        keep.title = donor.title
                    print("    took the parsed description from a folded row")

        if apply:
            await session.commit()
            print(f"\nmerged {merged} row(s)")
        else:
            print("\nDry run. Nothing was written.")
    return merged


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="report, write nothing")
    group.add_argument("--apply", action="store_true", help="merge the duplicates")
    args = parser.parse_args()
    await merge(apply=args.apply)


if __name__ == "__main__":
    asyncio.run(main())
