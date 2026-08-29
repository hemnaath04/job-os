"""Read again the postings whose stored parse named nothing scoreable.

`_extracted_nothing` used to ask whether the extraction returned any field at
all, and a title always comes back because it is handed in as `title_hint`. So
a reply carrying a title, sometimes a seniority and a location, and not one
skill, technology, qualification or responsibility was recorded as a successful
parse. Twelve postings in one workspace are stored that way. Four are not job
pages at all (a Disney error page, a Greenhouse applications dashboard, a
139-character stub); the rest include NVIDIA's and Millennium's real postings
with 7KB and 15KB of description sitting unread in `jd_clean`.

The parser no longer accepts that shape, so nothing new lands here. This is the
one-off for the rows already stored. It hands each one to the same
`complete_job_parse` the reparse endpoint uses, which re-reads the posting and
writes the result, including an honest `parse_incomplete` when the page really
does not carry a job description.

    uv run python -m job_os.scripts.reparse_unscoreable_jobs --owner user_xxx --dry-run
    uv run python -m job_os.scripts.reparse_unscoreable_jobs --owner user_xxx --apply

`--job-ids` re-reads named rows instead of searching for unscoreable ones. A
posting can need re-reading while still naming plenty: Microsoft's three rows
each carried about 498,000 characters of Eightfold app shell and still parsed
to twenty-nine or more fields, because the extractor was handed the first 18KB
of navigation and found something in it. Nothing about those rows says
"unscoreable", and they were still wrong.

    uv run python -m job_os.scripts.reparse_unscoreable_jobs \
        --owner user_xxx --job-ids <uuid> <uuid> --apply

Owner-scoped and required, not optional. `jobs` carries no owner column of its
own -- a posting is shared, and users reach it through `applications` -- so the
scoping is a join rather than a filter, and it is the reason this takes an
owner at all. Re-reading a public posting is not a cross-tenant read in the way
an unfiltered `application_cards` query was, but the blast radius of a bad run
still belongs to whoever asked for it, and a job nobody in this account saved
is not this account's to re-read.

Sequential, with a pause between rows. Each reparse is a page fetch plus a model
call, and the point of this script is to repair a backlog rather than to see how
much of it can be issued at once.
"""
from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from job_os.db.models import Application, Job, User
from job_os.db.session import async_session
from job_os.services.jd_ingest import complete_job_parse

# The fields that carry something a resume can be measured against. Mirrors
# `jd_parse._REQUIREMENT_BEARING_FIELDS`; imported rather than redefined would
# couple a one-off script to a module it has no other reason to touch.
SCOREABLE_FIELDS = (
    "required_skills",
    "preferred_skills",
    "technologies",
    "responsibilities",
    "qualifications",
    "keywords",
)

# Between reparses. Long enough that a backlog does not arrive at the gateway
# as a burst, short enough that a dozen rows finish inside a coffee.
PAUSE_SECONDS = 3.0


def names_nothing_scoreable(jd_parsed: dict[str, object] | None) -> bool:
    if not jd_parsed:
        return True
    return not any(jd_parsed.get(field) for field in SCOREABLE_FIELDS)


async def _candidates(owner_id: str, only: set[str] | None = None) -> list[Job]:
    async with async_session() as session:
        user = (
            await session.execute(select(User).where(User.clerk_id == owner_id))
        ).scalar_one_or_none()
        if user is None:
            raise SystemExit(f"No user with clerk_id {owner_id!r}.")
        result = await session.execute(
            select(Job)
            .options(joinedload(Job.company))
            .join(Application, Application.job_id == Job.id)
            .where(Application.user_id == user.id)
            .order_by(Job.created_at.desc())
        )
        jobs = result.unique().scalars().all()
    if only is not None:
        # Named explicitly, so the scoreable test does not apply: the caller has
        # already decided these need re-reading. Still filtered through the same
        # owner join, so naming a job somebody else saved finds nothing.
        return [job for job in jobs if str(job.id) in only]
    return [job for job in jobs if names_nothing_scoreable(job.jd_parsed)]


async def reparse(owner_id: str, *, apply: bool, only: set[str] | None = None) -> int:
    jobs = await _candidates(owner_id, only)
    if only is not None:
        missing = only - {str(job.id) for job in jobs}
        if missing:
            print(f"not found under this owner: {sorted(missing)}")
    label = "named" if only is not None else "whose stored parse names nothing scoreable"
    print(f"{len(jobs)} posting(s) {label}\n")
    for job in jobs:
        chars = len(job.jd_clean or "")
        print(f"  {job.id}  jd_clean={chars:>7} chars  {(job.title or '')[:52]}")
        print(f"      {(job.source_url or '(no url)')[:96]}")
    if not apply:
        print("\nDry run. Nothing was re-read.")
        return len(jobs)

    print()
    repaired = 0
    for index, job in enumerate(jobs, start=1):
        print(f"[{index}/{len(jobs)}] re-reading {job.id} ...", flush=True)
        try:
            await complete_job_parse(job.id, owner_id)
        except Exception as exc:  # noqa: BLE001 - one bad row must not end the run
            print(f"      failed: {str(exc)[:160]}")
            continue
        repaired += 1
        if index < len(jobs):
            await asyncio.sleep(PAUSE_SECONDS)

    # Re-read the rows rather than trusting the loop: `complete_job_parse`
    # writes on every path including failure, so "it did not raise" is not the
    # same as "this posting is scoreable now".
    #
    # Always the scoreable test, never the `--job-ids` filter. Reusing the
    # filter here reported every named row as still broken no matter how well
    # it had just been re-read, because naming a row is what put it in that
    # list in the first place.
    verified = await _candidates(owner_id)
    named = {str(job.id) for job in jobs}
    still_empty = [job for job in verified if str(job.id) in named]
    print(f"\nre-read {repaired} of {len(jobs)}")
    print(f"still naming nothing scoreable: {len(still_empty)}")
    for job in still_empty:
        print(f"  {(job.title or '')[:56]}  <- {(job.source_url or '')[:66]}")
    return repaired


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner", required=True, help="Clerk user id of the owner")
    parser.add_argument(
        "--job-ids",
        nargs="+",
        metavar="UUID",
        help="Re-read these rows instead of searching for unscoreable ones",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="List what would be re-read")
    group.add_argument("--apply", action="store_true", help="Re-read them")
    args = parser.parse_args()
    asyncio.run(
        reparse(
            args.owner,
            apply=args.apply,
            only=set(args.job_ids) if args.job_ids else None,
        )
    )


if __name__ == "__main__":
    main()
