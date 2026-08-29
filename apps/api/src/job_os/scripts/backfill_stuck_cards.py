"""Refresh board cards left showing a parse that has already finished.

The deferred JD parse writes Postgres and, since the card-sync change, the
Appwrite card too. Cards created before that change kept their insert-time
snapshot: "Still reading this posting", no title, and a company guessed from
the URL. Six of them sat on the live board that way while every one of those
jobs was fully parsed in Postgres.

This is the one-off for those. It reads the real job, writes it onto the card
the same way `jd_ingest.sync_job_into_cards` does, and touches nothing else.

    uv run python -m job_os.scripts.backfill_stuck_cards --owner user_xxx --dry-run
    uv run python -m job_os.scripts.backfill_stuck_cards --owner user_xxx --apply

Owner-scoped and required, not optional: `application_cards` is multi-tenant
and the API key bypasses row permissions, so an unfiltered run would read and
rewrite other people's boards. That has happened here before.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from job_os.db.models import Job
from job_os.db.session import async_session
from job_os.services import appwrite_tables
from job_os.services.jd_ingest import _card_job_view
from job_os.settings import get_settings


async def _jobs_by_id(ids: set[str]) -> dict[str, Job]:
    if not ids:
        return {}
    async with async_session() as session:
        result = await session.execute(
            select(Job).options(joinedload(Job.company)).where(Job.id.in_([UUID(i) for i in ids]))
        )
        return {str(job.id): job for job in result.unique().scalars().all()}


async def backfill(owner_id: str, *, apply: bool) -> int:
    table = get_settings().appwrite_application_cards_table_id
    rows = await appwrite_tables.list_rows(
        # `attribute=value`, which is what _parse_filter accepts. Appwrite's own
            # Query JSON is not a filter expression here and raises.
            filters=[f"owner_id={owner_id}", "archived=false"],
        limit=500,
        table_id=table,
    )

    stuck: list[tuple[dict[str, Any], dict[str, Any], str]] = []
    for row in rows:
        try:
            snapshot = json.loads(row.get("snapshot") or "{}")
        except ValueError:
            continue
        job = snapshot.get("job") or {}
        if not (job.get("jd_parsed") or {}).get("parse_pending"):
            continue
        if job.get("id"):
            stuck.append((row, snapshot, str(job["id"])))

    jobs = await _jobs_by_id({job_id for _r, _s, job_id in stuck})
    changed = 0
    for row, snapshot, job_id in stuck:
        job = jobs.get(job_id)
        if job is None:
            print(f"  skip  card {row['$id'][:12]}  job {job_id[:8]} is gone from Postgres")
            continue
        if (job.jd_parsed or {}).get("parse_pending"):
            # Genuinely still running. Leave it: the card is telling the truth.
            print(f"  skip  card {row['$id'][:12]}  job {job_id[:8]} really is still parsing")
            continue
        fresh = _card_job_view(job)
        company = fresh["company"]["name"] if fresh["company"] else "?"
        print(f"  fix   card {row['$id'][:12]}  {company} / {fresh['title']}")
        if apply:
            snapshot["job"] = {**(snapshot.get("job") or {}), **fresh}
            await appwrite_tables.update_rows(
                row_id=row["$id"], data={"snapshot": json.dumps(snapshot)}, table_id=table
            )
        changed += 1
    return changed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner", required=True, help="Appwrite owner_id (Clerk user id)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="Report what would change")
    group.add_argument("--apply", action="store_true", help="Write the changes")
    args = parser.parse_args()

    count = asyncio.run(backfill(args.owner, apply=args.apply))
    verb = "updated" if args.apply else "would update"
    print(f"{verb} {count} card(s)")


if __name__ == "__main__":
    main()
