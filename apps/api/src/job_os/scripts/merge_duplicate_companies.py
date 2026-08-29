"""Collapse companies that are the same employer recorded twice.

`upsert_company` used to write the employer twice during one import: the
URL-slug guess called it with no domain, and the background parse called it
again once it knew one. The uniqueness index is on
`(lower(name), coalesce(domain, ''))`, so `(Acme, NULL)` and `(Acme, acme.com)`
are two legal rows and both were created. Reading them back then raised
MultipleResultsFound, which is Sentry 96dd0d61.

That is fixed at the source: a domain now fills in the row we already had
rather than inserting beside it. This is the one-off for the pairs already
written, which the fix leaves readable but does not merge.

    uv run python -m job_os.scripts.merge_duplicate_companies --dry-run
    uv run python -m job_os.scripts.merge_duplicate_companies --apply

Not owner-scoped, unlike the other repair scripts here, and the difference is
worth stating rather than leaving as an omission: `companies` has no owner
column and is not multi-tenant. A company is a fact about the world that every
user's jobs point at, so there is no per-user slice of it to take.

The survivor is chosen the same way `upsert_company` now chooses one, so this
script and the running code agree about which row is the real one: prefer a row
that carries a domain, then the oldest. Jobs are repointed before anything is
deleted, so a crash midway leaves rows that are merged-but-not-yet-tidied
rather than jobs pointing at a company that no longer exists.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict

from sqlalchemy import func, select, update

from job_os.db.models import Company, Job
from job_os.db.session import async_session


def survivor_of(rows: list[Company]) -> Company:
    """The row the others fold into.

    Mirrors `upsert_company`'s domain-less lookup: the best-known row wins, and
    `created_at` breaks the tie so the choice cannot change between runs.
    """
    return sorted(rows, key=lambda row: (row.domain is None, row.created_at, str(row.id)))[0]


async def merge(*, apply: bool) -> int:
    merged = 0
    async with async_session() as session:
        result = await session.execute(select(Company))
        by_name: dict[str, list[Company]] = defaultdict(list)
        for company in result.scalars().all():
            by_name[company.name.strip().lower()].append(company)

        groups = {name: rows for name, rows in by_name.items() if len(rows) > 1}
        if not groups:
            print("no duplicate companies")
            return 0

        print(f"{len(groups)} duplicated name(s)\n")
        for name, rows in sorted(groups.items()):
            keep = survivor_of(rows)
            losers = [row for row in rows if row.id != keep.id]
            print(f"  {name}")
            print(f"    keep  {keep.id}  domain={keep.domain!r}")
            for loser in losers:
                count = await session.scalar(
                    select(func.count()).select_from(Job).where(Job.company_id == loser.id)
                )
                print(f"    fold  {loser.id}  domain={loser.domain!r}  jobs={count}")
                if not apply:
                    continue
                if count:
                    await session.execute(
                        update(Job)
                        .where(Job.company_id == loser.id)
                        .values(company_id=keep.id)
                    )
                # Flushed before the delete so the repoint is already in the
                # transaction: deleting a row jobs still reference would fail
                # on the foreign key, which is the right failure but a worse
                # one to debug than never getting there.
                await session.flush()
                await session.delete(loser)
                merged += 1
            # The survivor may be the domain-less row if no sibling had one.
            # Take a domain from a loser rather than losing what was known.
            if keep.domain is None:
                donor = next((row.domain for row in losers if row.domain), None)
                if donor and apply:
                    keep.domain = donor
                    print(f"    kept the domain {donor!r} from a folded row")

        if apply:
            await session.commit()
            print(f"\nmerged {merged} row(s)")
        else:
            print("\nDry run. Nothing was written.")
    return merged


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="Report what would merge")
    group.add_argument("--apply", action="store_true", help="Merge them")
    args = parser.parse_args()
    asyncio.run(merge(apply=args.apply))


if __name__ == "__main__":
    main()
