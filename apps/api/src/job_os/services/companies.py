from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from job_os.db.models import Company


async def upsert_company(
    session: AsyncSession, *, name: str, domain: str | None = None
) -> Company:
    """Find the company by case-insensitive name, or insert it.

    Importing a job creates the company twice, and then reading it raised.

    The two writes come from different points in the same flow. Adding a job
    from a URL guesses the employer from the link slug and calls this with no
    domain, because nothing has been fetched yet. The background parse finishes
    a minute later, learns the real domain, and calls this again. The uniqueness
    index is on `(lower(name), coalesce(domain, ''))`, so those are two distinct
    keys and both rows are allowed: production holds `(GlossGenius, NULL)`
    alongside `(GlossGenius, glossgenius.com)`, and the same for Workiva.

    The lookup then only constrained the domain when one was passed, so the next
    domain-less call matched both rows and `scalar_one_or_none` raised
    MultipleResultsFound. That is Sentry 96dd0d61 on POST /jobs/from-url: an
    import failing outright because an earlier import had succeeded.

    So this no longer treats a missing domain as a different company:

    * With a domain, an exact match wins. Failing that, a domain-less row of the
      same name is the same employer with less known about it, so it is filled
      in rather than duplicated. That is what stops the pair forming at all.
    * Without a domain, the best-known row wins: one carrying a domain is
      preferred over one that is not, and the oldest breaks any remaining tie so
      that two calls in a row cannot disagree.

    Deterministic on purpose. Returning whichever row the database happened to
    yield first would have fixed the exception and left two companies that drift
    apart in research, notes and job counts depending on which one each caller
    got.
    """
    cleaned = name.strip()
    rows = (
        (
            await session.execute(
                select(Company)
                .where(func.lower(Company.name) == cleaned.lower())
                # `created_at` alone left ties unbroken for rows written in the
                # same transaction, which is how the duplicate pairs were made.
                .order_by(Company.created_at.asc(), Company.id.asc())
            )
        )
        .scalars()
        .all()
    )

    if domain:
        for row in rows:
            if row.domain == domain:
                return row
        for row in rows:
            if row.domain is None:
                # The employer we already knew, now with its domain. Filled in
                # rather than inserted alongside.
                #
                # Inside a savepoint, because a concurrent request can commit
                # `(name, domain)` between the check above and this write, and
                # the unique index would then reject it. A bare `rollback()`
                # here would discard the caller's whole transaction: this runs
                # mid-import, with the job row already staged.
                try:
                    async with session.begin_nested():
                        row.domain = domain
                        await session.flush()
                except IntegrityError:
                    # Someone else got there first, and their row is as good as
                    # this one would have been. Take theirs.
                    session.expire(row)
                    winner = (
                        await session.execute(
                            select(Company).where(
                                func.lower(Company.name) == cleaned.lower(),
                                Company.domain == domain,
                            )
                        )
                    ).scalars().first()
                    if winner is not None:
                        return winner
                    return row
                return row
    elif rows:
        # Prefer the row that knows the most. `sorted` is stable, so the
        # created_at ordering above still breaks ties among equals.
        return sorted(rows, key=lambda row: row.domain is None)[0]

    company = Company(name=cleaned, domain=domain)
    session.add(company)
    await session.flush()
    return company
