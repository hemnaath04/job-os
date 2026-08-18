"""One crawl sweep, end to end.

    pick due tokens -> fetch each board -> normalize -> upsert
                    -> deactivate what the board stopped listing
                    -> dedupe within the boards we just touched
                    -> record liveness and a run summary

Two properties matter more than throughput.

**A sweep is interruptible.** Tokens are committed in small groups, and each
group's liveness is written as it completes, so a killed process loses at most one
group and the next run resumes where this one stopped. That is what makes a corpus
of 15,874 tokens crawlable at all under a scheduler with a timeout.

**A sweep never destroys data on incomplete information.** Deactivation runs only
for boards whose fetch returned LIVE or EMPTY in this run. A 304, a timeout, a
5xx or a partial paginated read all mean "we did not see the current list", and
on those the existing rows are left exactly as they are.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from job_os.db.models.ingest import AtsBoardToken, CrawlRun, CrawlStatus
from job_os.db.models.job_posting import JobPosting
from job_os.ingest import liveness
from job_os.ingest.dedupe import DedupeCandidate, find_duplicates
from job_os.ingest.fetcher import BoardTiming, PoliteFetcher
from job_os.ingest.providers import BoardResult, BoardStatus, RawPosting, get_provider
from job_os.ingest.upsert import (
    deactivate_missing,
    mark_duplicates,
    upsert_postings,
)
from job_os.services import appwrite_tables

log = structlog.get_logger(__name__)

#: Boards fetched and written per group. A group is one commit, so this is the
#: unit of work a crash can lose. Small enough to lose cheaply, large enough that
#: the commit overhead disappears against the fetch time.
DEFAULT_GROUP_SIZE = 32
DEFAULT_CONCURRENCY = 8
#: Default ceiling for one scheduled run. The full corpus is ~15,900 tokens and a
#: full Greenhouse sweep measured 11.5 minutes, so an unbounded default would put
#: a scheduled job well past most platforms' timeout. Coverage is meant to
#: accumulate across runs, which the liveness schedule already arranges.
DEFAULT_TOKEN_LIMIT = 400


@dataclass(slots=True)
class SweepResult:
    run_id: uuid.UUID
    tokens_attempted: int = 0
    tokens_live: int = 0
    tokens_empty: int = 0
    tokens_missing: int = 0
    tokens_error: int = 0
    tokens_not_modified: int = 0
    postings_seen: int = 0
    postings_inserted: int = 0
    postings_updated: int = 0
    postings_unchanged: int = 0
    postings_deactivated: int = 0
    duplicates_marked: int = 0
    requests_made: int = 0
    bytes_fetched: int = 0
    bytes_saved: int = 0
    duration_s: float = 0.0
    errors: list[str] = field(default_factory=list)

    @property
    def boards_per_second(self) -> float:
        return self.tokens_attempted / self.duration_s if self.duration_s else 0.0

    def as_dict(self) -> dict[str, object]:
        return {
            "run_id": str(self.run_id),
            "tokens_attempted": self.tokens_attempted,
            "tokens_live": self.tokens_live,
            "tokens_empty": self.tokens_empty,
            "tokens_missing": self.tokens_missing,
            "tokens_error": self.tokens_error,
            "tokens_not_modified": self.tokens_not_modified,
            "postings_seen": self.postings_seen,
            "postings_inserted": self.postings_inserted,
            "postings_updated": self.postings_updated,
            "postings_unchanged": self.postings_unchanged,
            "postings_deactivated": self.postings_deactivated,
            "duplicates_marked": self.duplicates_marked,
            "requests_made": self.requests_made,
            "bytes_fetched": self.bytes_fetched,
            "bytes_saved": self.bytes_saved,
            "duration_s": round(self.duration_s, 2),
            "boards_per_second": round(self.boards_per_second, 1),
            "errors": self.errors[:10],
        }


async def run_sweep(
    session: AsyncSession,
    *,
    providers: list[str] | None = None,
    token_limit: int | None = DEFAULT_TOKEN_LIMIT,
    concurrency: int = DEFAULT_CONCURRENCY,
    group_size: int = DEFAULT_GROUP_SIZE,
    include_retired: bool = False,
    seed: bool = True,
    dedupe: bool = True,
    fetcher: PoliteFetcher | None = None,
) -> SweepResult:
    """Crawl the boards that are due and write what changed."""
    if seed:
        seeded = await liveness.seed_corpus(session, providers)
        await session.commit()
        log.info("ingest.seeded", **seeded)

    tokens = await liveness.due_tokens(
        session, providers=providers, limit=token_limit, include_retired=include_retired
    )
    run = CrawlRun(
        status=CrawlStatus.RUNNING.value,
        providers=list(providers) if providers else [],
        token_limit=token_limit,
        started_at=datetime.now(UTC),
    )
    session.add(run)
    await session.flush()
    run_id = run.id
    await session.commit()

    result = SweepResult(run_id=run_id)
    timing = BoardTiming()
    owns_fetcher = fetcher is None
    client = fetcher or PoliteFetcher(
        concurrency=concurrency, per_host_concurrency=concurrency
    )

    try:
        for start in range(0, len(tokens), group_size):
            group = tokens[start : start + group_size]
            await _process_group(session, client, group, run_id=run_id, result=result)
            # One commit per group. This is the resume point: a crash loses this
            # group's work and nothing before it.
            await session.commit()

        if dedupe:
            result.duplicates_marked = await dedupe_recent(session, run_id=run_id)
            await session.commit()
    except Exception as exc:  # noqa: BLE001 - the run row must record the failure
        result.duration_s = timing.stop()
        result.errors.append(f"{type(exc).__name__}: {exc}")
        await _finish_run(session, run_id, result, client, status=CrawlStatus.FAILED)
        await session.commit()
        log.error("ingest.sweep_failed", run_id=str(run_id), error=str(exc))
        raise
    finally:
        if owns_fetcher:
            await client.aclose()

    result.duration_s = timing.stop()
    result.requests_made = client.stats.requests
    result.bytes_fetched = client.stats.bytes_read
    result.bytes_saved = client.stats.bytes_saved_estimate
    await _finish_run(session, run_id, result, client, status=CrawlStatus.COMPLETED)
    await session.commit()
    log.info("ingest.sweep_done", **result.as_dict())
    return result


async def _process_group(
    session: AsyncSession,
    fetcher: PoliteFetcher,
    group: list[AtsBoardToken],
    *,
    run_id: uuid.UUID,
    result: SweepResult,
) -> None:
    now = datetime.now(UTC)
    board_results = await asyncio.gather(
        *(
            get_provider(token.provider).fetch_board(
                fetcher,
                token.token,
                token.etag,
                token.last_payload_bytes or 0,
            )
            for token in group
        ),
        return_exceptions=True,
    )

    postings: list[RawPosting] = []
    authoritative: list[tuple[str, str]] = []
    company_lookup: dict[tuple[str, str], tuple[str | None, str | None]] = {}

    for token, board in zip(group, board_results, strict=True):
        if isinstance(board, BaseException):
            # A provider raising rather than returning an ERROR result is a bug in
            # that provider, but it must not take the sweep down with it.
            board = BoardResult(
                provider=token.provider,
                token=token.token,
                status=BoardStatus.ERROR,
                error=f"{type(board).__name__}: {board}",
            )
        result.tokens_attempted += 1
        match board.status:
            case BoardStatus.LIVE:
                result.tokens_live += 1
            case BoardStatus.EMPTY:
                result.tokens_empty += 1
            case BoardStatus.MISSING:
                result.tokens_missing += 1
            case BoardStatus.NOT_MODIFIED:
                result.tokens_not_modified += 1
            case BoardStatus.ERROR:
                result.tokens_error += 1
                if board.error:
                    result.errors.append(f"{token.provider}/{token.token}: {board.error}")

        if board.usable:
            # Only a board we actually re-read is allowed to close its postings.
            authoritative.append((token.provider, token.token))
        if board.postings:
            postings.extend(board.postings)
            result.postings_seen += len(board.postings)

        company_lookup[(token.provider, token.token)] = (
            token.company_name,
            token.company_domain,
        )
        liveness.apply_result(token, board, now=now)

    if postings:
        stats = await upsert_postings(
            session,
            postings,
            run_id=run_id,
            seen_at=now,
            company_names=company_lookup,
        )
        result.postings_inserted += stats.inserted
        result.postings_updated += stats.updated
        result.postings_unchanged += stats.unchanged

    for source, board_token in authoritative:
        result.postings_deactivated += await deactivate_missing(
            session, source=source, board_token=board_token, run_id=run_id, at=now
        )


async def dedupe_recent(
    session: AsyncSession, *, run_id: uuid.UUID, limit: int = 5_000
) -> int:
    """Dedupe the postings this run touched, against each other.

    Scoped to the run rather than to the whole table because a full pairwise pass
    over the index is quadratic and mostly re-answers questions already answered.
    Cross-run duplicates are caught the next time both rows are re-crawled in the
    same sweep, and the stage-one content hash catches the rest for free on write.

    Reads Appwrite, not Postgres: `upsert_postings` writes this run's postings
    there now, so a query against `JobPosting.last_crawl_run_id == run_id`
    would find nothing for any run after the move -- this candidate fetch has
    to track that write path, not just `mark_duplicates`. `session` itself is
    unused by this function directly, but stays a real parameter (not deleted)
    since it is still passed through to `mark_duplicates` below, which keeps
    the same signature for the same reason.
    """
    records = await appwrite_tables.list_rows(
        filters=[f"last_crawl_run_id={run_id}", "active=true"],
        queries=[{"method": "isNull", "attribute": "canonical_id"}],
        select=[
            "source_posting_id",
            "dedupe_key",
            "content_hash",
            "jd_clean",
            "jd_hydrated",
            "posted_at",
            "posted_at_estimated",
            "first_seen_at",
        ],
        limit=limit,
    )
    if len(records) < 2:
        return 0

    candidates: list[DedupeCandidate] = []
    by_key: dict[str, uuid.UUID] = {}
    for record in records:
        posting_id = uuid.UUID(record["source_posting_id"])
        key = str(posting_id)
        by_key[key] = posting_id
        posted_at = _parse_dt(record.get("posted_at"))
        # `_survivor_rank` requires a real datetime, not Optional -- every row
        # here should carry one, but `datetime.now` is a safer fallback than a
        # crash if a malformed row somehow lacks it.
        first_seen_at = _parse_dt(record.get("first_seen_at")) or datetime.now(UTC)
        candidates.append(
            DedupeCandidate(
                key=key,
                dedupe_key=record["dedupe_key"],
                content_hash=record["content_hash"],
                # An unhydrated body is provider metadata, not a description.
                # Feeding it to TF-IDF would make every posting from one
                # SmartRecruiters board look like every other one. `.get(...)
                # or ""` because Appwrite omits an empty-string attribute from
                # a row payload entirely rather than returning "" for it, so a
                # genuinely hydrated-but-empty body reads back as a missing key.
                description=(record.get("jd_clean") or "") if record.get("jd_hydrated") else "",
                rank=_survivor_rank(posted_at, bool(record.get("posted_at_estimated")), first_seen_at),
            )
        )

    report = find_duplicates(candidates)
    links = [
        (by_key[link.duplicate], by_key[link.canonical], link.reason, link.score)
        for link in report.links
        if link.duplicate in by_key and link.canonical in by_key
    ]
    marked = await mark_duplicates(session, links)
    if marked:
        log.info(
            "ingest.dedupe",
            run_id=str(run_id),
            candidates=len(candidates),
            exact=report.exact_matches,
            similarity=report.similarity_matches,
            similarity_ran=report.similarity_ran,
            comparisons=report.comparisons,
            marked=marked,
        )
    return marked


def _parse_dt(value: Any) -> datetime | None:
    """An Appwrite row's timestamp column, as ISO 8601 text, back into a `datetime`.

    Postgres handed these back already typed; Appwrite's JSON responses do not.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _survivor_rank(
    posted_at: datetime | None, posted_at_estimated: bool, first_seen_at: datetime
) -> float:
    """Which row of a duplicate pair to keep.

    Prefer the one whose posting date we actually know, then the one with the
    earlier first sighting. Keeping the earliest first sighting is the point: if
    the newer copy won, a reposted requisition would reset its own history and the
    honest "first seen three weeks ago" claim would be lost on every merge.
    """
    score = 0.0
    if posted_at is not None:
        score += 2.0
    if not posted_at_estimated:
        score += 1.0
    # Earlier first_seen_at wins, expressed as a small negative on the timestamp
    # so it only ever breaks ties between rows of equal date quality.
    score -= first_seen_at.timestamp() / 1e12
    return score


async def _finish_run(
    session: AsyncSession,
    run_id: uuid.UUID,
    result: SweepResult,
    fetcher: PoliteFetcher,
    *,
    status: CrawlStatus,
) -> None:
    run = await session.get(CrawlRun, run_id)
    if run is None:
        return
    run.status = status.value
    run.finished_at = datetime.now(UTC)
    run.duration_ms = int(result.duration_s * 1000)
    run.tokens_attempted = result.tokens_attempted
    run.tokens_live = result.tokens_live
    run.tokens_empty = result.tokens_empty
    run.tokens_missing = result.tokens_missing
    run.tokens_error = result.tokens_error
    run.tokens_not_modified = result.tokens_not_modified
    run.postings_seen = result.postings_seen
    run.postings_inserted = result.postings_inserted
    run.postings_updated = result.postings_updated
    run.postings_unchanged = result.postings_unchanged
    run.postings_deactivated = result.postings_deactivated
    run.duplicates_marked = result.duplicates_marked
    run.requests_made = fetcher.stats.requests
    run.bytes_fetched = fetcher.stats.bytes_read
    run.bytes_saved = fetcher.stats.bytes_saved_estimate
    run.error = result.errors[0] if result.errors and status is CrawlStatus.FAILED else None
    run.notes = {
        "retries": fetcher.stats.retries,
        "fetch_errors": fetcher.stats.errors,
        "not_modified": fetcher.stats.not_modified,
        "sample_errors": result.errors[:10],
    }
