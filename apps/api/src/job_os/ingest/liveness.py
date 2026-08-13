"""Which tokens are worth fetching, and when to fetch them again.

Measured on this branch, sampling the bundled corpus at concurrency 8:

    provider         sampled   live   empty   missing   reachable
    greenhouse           200    117      16        67       66.5%
    lever                120     47      11        62       48.3%
    ashby                120     82       9        29       75.8%
                         ---    ---     ---       ---       -----
    total                440    246      36       158       64.1%

So a third of the corpus is dead, and Lever's half of it is dead. A crawler that
re-reads the seed file every night spends 36% of its request budget relearning
the same 404s, forever. Recording the answer per token and scheduling from it is
what stops that, and it is also the difference between a sweep that has to run to
completion and one that can be resumed after a crash: the schedule lives in the
database, so a killed sweep loses only the boards it was mid-flight on.

The backoff below is deliberately generous to dead tokens rather than deleting
them. A company that switches ATS vendor, or pauses hiring for a quarter, comes
back, and a corpus that forgets it has to rediscover it from nothing.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import case, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from job_os.db.models.ingest import AtsBoardToken, TokenStatus
from job_os.ingest.corpus import SeedToken, seed_tokens
from job_os.ingest.providers import BoardResult, BoardStatus

log = structlog.get_logger(__name__)

#: How long before a token is due again, by what we last learned about it.
#: A live board is re-read often because its postings are the product. A missing
#: one is re-read rarely because the answer almost never changes.
RECHECK_INTERVALS: dict[str, timedelta] = {
    TokenStatus.UNKNOWN.value: timedelta(0),
    TokenStatus.LIVE.value: timedelta(hours=6),
    TokenStatus.EMPTY.value: timedelta(days=3),
    TokenStatus.MISSING.value: timedelta(days=30),
    TokenStatus.ERROR.value: timedelta(hours=1),
    TokenStatus.RETIRED.value: timedelta(days=180),
}

#: Curated companies are what users actually search for, so they are re-read on a
#: shorter cycle than the bulk corpus.
PRIORITY_INTERVAL = timedelta(hours=2)
PRIORITY_THRESHOLD = 50

#: MISSING this many times in a row and the token is retired. Three rather than
#: one because a vendor outage can 404 a real board, and retiring on a single
#: observation would prune live companies during someone else's bad afternoon.
RETIRE_AFTER_MISSING = 3
#: EMPTY this many times in a row, having never once produced a posting, and the
#: token is retired. This is how the SmartRecruiters trap gets resolved: that
#: provider answers 200 with `totalFound: 0` for a company that does not exist,
#: so death can only be inferred from repetition over time, never from one call.
RETIRE_AFTER_EMPTY = 8
#: Consecutive errors before backing a token off to the MISSING cadence. Keeps a
#: permanently broken host from eating a sweep's retry budget every hour.
ERROR_PATIENCE = 5


async def seed_corpus(
    session: AsyncSession, providers: list[str] | None = None
) -> dict[str, int]:
    """Insert the bundled seed tokens, leaving anything already known alone.

    Idempotent: re-running never resets a token's learned status, counters or
    ETag. Curated names, domains and priority are refreshed, since those come
    from the repo rather than from the crawl.
    """
    seeds: list[SeedToken] = seed_tokens(providers)
    if not seeds:
        return {"seeded": 0, "total": 0}

    inserted = 0
    for start in range(0, len(seeds), 1000):
        batch = seeds[start : start + 1000]
        statement = insert(AtsBoardToken).values(
            [
                {
                    "provider": s.provider,
                    "token": s.token,
                    "company_name": s.company_name,
                    "company_domain": s.company_domain,
                    "priority": s.priority,
                    "status": TokenStatus.UNKNOWN.value,
                    "next_check_after": None,
                }
                for s in batch
            ]
        )
        excluded = statement.excluded
        returning = statement.on_conflict_do_update(
            constraint="uq_ats_board_tokens_pair",
            set_={
                # Repo-owned facts get refreshed; crawl-owned facts do not. A
                # token that has been learned to be dead must stay dead after a
                # re-seed, or the seed file would silently undo the pruning.
                "company_name": func.coalesce(
                    excluded.company_name, AtsBoardToken.company_name
                ),
                "company_domain": func.coalesce(
                    excluded.company_domain, AtsBoardToken.company_domain
                ),
                "priority": func.greatest(excluded.priority, AtsBoardToken.priority),
            },
        ).returning(AtsBoardToken.id)
        result = await session.execute(returning)
        inserted += len(result.all())

    total = await session.scalar(select(func.count()).select_from(AtsBoardToken))
    return {"seeded": inserted, "total": int(total or 0)}


async def due_tokens(
    session: AsyncSession,
    *,
    providers: list[str] | None = None,
    limit: int | None = None,
    include_retired: bool = False,
    now: datetime | None = None,
) -> list[AtsBoardToken]:
    """Tokens whose next check is due, most valuable first.

    Ordering is priority, then never-checked, then longest-overdue. That means a
    fresh corpus crawls the curated companies first and a partial sweep is still
    a useful sweep, which matters because the corpus is far larger than any single
    scheduled run should try to cover.
    """
    at = now or datetime.now(UTC)
    statement = select(AtsBoardToken).where(
        or_(
            AtsBoardToken.next_check_after.is_(None),
            AtsBoardToken.next_check_after <= at,
        )
    )
    if providers:
        statement = statement.where(AtsBoardToken.provider.in_(providers))
    if not include_retired:
        statement = statement.where(AtsBoardToken.status != TokenStatus.RETIRED.value)

    statement = statement.order_by(
        AtsBoardToken.priority.desc(),
        # Never checked sorts ahead of overdue: an unknown token is the only kind
        # that can still add coverage the index does not have.
        case((AtsBoardToken.last_checked_at.is_(None), 0), else_=1),
        AtsBoardToken.next_check_after.asc().nullsfirst(),
        AtsBoardToken.token.asc(),
    )
    if limit:
        statement = statement.limit(limit)
    result = await session.execute(statement)
    return list(result.scalars().all())


def next_status(token: AtsBoardToken, result: BoardResult) -> str:
    """The token's status after this observation."""
    if result.status is BoardStatus.MISSING:
        missing_streak = token.consecutive_failures + 1
        if missing_streak >= RETIRE_AFTER_MISSING:
            return TokenStatus.RETIRED.value
        return TokenStatus.MISSING.value

    if result.status is BoardStatus.LIVE:
        return TokenStatus.LIVE.value

    if result.status is BoardStatus.EMPTY:
        # Never once produced a posting, and empty many times running: this is
        # almost certainly a token that does not exist. A board that has produced
        # postings before is just idle, and stays EMPTY however long that lasts.
        never_produced = not token.last_ok_at
        if never_produced and token.consecutive_empty + 1 >= RETIRE_AFTER_EMPTY:
            return TokenStatus.RETIRED.value
        return TokenStatus.EMPTY.value

    if result.status is BoardStatus.NOT_MODIFIED:
        # Nothing new was learned, so the old verdict stands. A 304 does prove the
        # board is reachable, so an UNKNOWN token becomes LIVE.
        if token.status == TokenStatus.UNKNOWN.value:
            return TokenStatus.LIVE.value
        return token.status

    return TokenStatus.ERROR.value


def next_check_at(
    token: AtsBoardToken, status: str, *, now: datetime, consecutive_failures: int
) -> datetime:
    """When to look at this token again."""
    if status == TokenStatus.ERROR.value and consecutive_failures >= ERROR_PATIENCE:
        # Repeatedly failing is not the same as transiently failing. Back it off
        # to the dead-token cadence rather than retrying hourly forever.
        return now + RECHECK_INTERVALS[TokenStatus.MISSING.value]

    interval = RECHECK_INTERVALS.get(status, RECHECK_INTERVALS[TokenStatus.LIVE.value])
    if status == TokenStatus.LIVE.value and token.priority >= PRIORITY_THRESHOLD:
        interval = min(interval, PRIORITY_INTERVAL)
    return now + interval


def apply_result(
    token: AtsBoardToken, result: BoardResult, *, now: datetime | None = None
) -> None:
    """Fold one crawl observation into the token row, in place."""
    at = now or datetime.now(UTC)
    job_count = len(result.postings)

    token.checks_count += 1
    token.last_checked_at = at
    token.last_http_status = result.http_status
    token.last_error = result.error

    if result.status in (BoardStatus.MISSING, BoardStatus.ERROR):
        token.consecutive_failures += 1
    else:
        token.consecutive_failures = 0

    if result.status is BoardStatus.EMPTY:
        token.consecutive_empty += 1
    elif result.status is BoardStatus.LIVE:
        token.consecutive_empty = 0

    if result.status is BoardStatus.LIVE:
        token.last_ok_at = at
        token.last_job_count = job_count
        token.max_job_count = max(token.max_job_count or 0, job_count)
        # Boards report the employer's name; the corpus mostly does not. Learn it
        # once so dedupe and display stop falling back to a board slug.
        if not token.company_name and result.postings:
            token.company_name = result.postings[0].company_name
    elif result.status is BoardStatus.EMPTY:
        token.last_job_count = 0

    # Only overwrite the ETag when this fetch produced a body. A 304 keeps the
    # existing validator, and an error must not clear it, or the next crawl would
    # download a full payload it could have revalidated for free.
    if result.status in (BoardStatus.LIVE, BoardStatus.EMPTY):
        token.etag = result.etag
        token.last_payload_bytes = result.bytes_fetched

    status = next_status(token, result)
    token.status = status
    token.next_check_after = next_check_at(
        token, status, now=at, consecutive_failures=token.consecutive_failures
    )


async def liveness_summary(session: AsyncSession) -> dict[str, dict[str, int]]:
    """Token counts by provider and status, for the CLI and `/index/stats`."""
    rows = await session.execute(
        select(
            AtsBoardToken.provider,
            AtsBoardToken.status,
            func.count().label("n"),
        ).group_by(AtsBoardToken.provider, AtsBoardToken.status)
    )
    summary: dict[str, dict[str, int]] = {}
    for provider, status, count in rows.all():
        summary.setdefault(provider, {})[status] = int(count)
    for counts in summary.values():
        counts["total"] = sum(v for k, v in counts.items() if k != "total")
    return summary


async def token_map(
    session: AsyncSession, provider_tokens: list[tuple[str, str]]
) -> dict[tuple[str, str], tuple[str | None, str | None]]:
    """(provider, token) -> (company_name, company_domain) for the upsert."""
    if not provider_tokens:
        return {}
    wanted = set(provider_tokens)
    rows = await session.execute(
        select(
            AtsBoardToken.provider,
            AtsBoardToken.token,
            AtsBoardToken.company_name,
            AtsBoardToken.company_domain,
        ).where(AtsBoardToken.provider.in_({p for p, _ in wanted}))
    )
    return {
        (provider, token): (name, domain)
        for provider, token, name, domain in rows.all()
        if (provider, token) in wanted
    }


def token_identity(token: AtsBoardToken) -> tuple[str, str, uuid.UUID]:
    return token.provider, token.token, token.id
