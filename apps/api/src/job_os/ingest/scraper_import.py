"""Pull from the standalone job-scraper's bulk export and write into
`job_postings`, reusing this package's own upsert/dedup machinery rather than
crawling BambooHR/Workday/iCIMS a second time from job.os's own infra.

The scraper (a separate personal project, deliberately outside this repo - see
its own README) already crawls Greenhouse/Lever/Ashby/SmartRecruiters/
BambooHR/Workday/iCIMS nightly on its own schedule and exposes a paginated,
shared-secret-gated export at GET {SCRAPER_EXPORT_URL}/export/jobs. This
module is the pull side: no crawling happens here, only normalize + upsert.

`upsert_postings`/`deactivate_missing` write to Postgres. They spent two weeks
writing to Appwrite instead and kept the same signatures throughout, so this
module's calls to them have never changed; the `AsyncSession` it passes them is
load-bearing again rather than only being there for the `CrawlRun` bookkeeping.

Cursor is `(since, since_id)`, persisted in `scraper_import_cursor` (one row,
id="scraper_import") between separate runs - a Heroku Scheduler invocation is
a fresh process with no memory of the last one, so without this every run
would restart at the export's beginning and (with a large enough backlog and
a time-boxed run) never progress past whatever the first run's budget
covered. Read once at the start of a run, updated after every page so a
mid-run crash keeps whatever progress was actually committed rather than
losing it back to the last full run's end.

    uv run python -m job_os.ingest.cli import-scraper
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from job_os.db.models.ingest import CrawlRun, CrawlStatus, ScraperImportCursor
from job_os.ingest.providers import RawPosting
from job_os.ingest.upsert import UpsertStats, deactivate_missing, upsert_postings
from job_os.settings import get_settings

log = structlog.get_logger(__name__)

CURSOR_ID = "scraper_import"
PAGE_LIMIT = 1000
#: Safety cap, not a tuning knob for the current daily schedule - Heroku
#: Scheduler gives a job runtime up to its own frequency (a daily job gets up
#: to 24h), so this isn't close to binding today. It exists so a stalled
#: connection or an unexpectedly large backlog can't run away silently.
DEFAULT_MAX_SECONDS = 1200


@dataclass(slots=True)
class ImportResult:
    run_id: uuid.UUID
    pages_fetched: int = 0
    rows_seen: int = 0
    boards_touched: set[tuple[str, str]] = field(default_factory=set)
    upsert: UpsertStats = field(default_factory=UpsertStats)
    deactivated: int = 0
    duration_s: float = 0.0
    error: str | None = None
    #: Hit the time budget before exhausting the export. Deactivation is
    #: skipped whenever this is true (see run_import) - the same board can
    #: legitimately span more than one export page, so stopping mid-way means
    #: this run never actually saw any board's complete current list, and
    #: deactivating on that would risk closing postings that are still live.
    truncated: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "run_id": str(self.run_id),
            "pages_fetched": self.pages_fetched,
            "rows_seen": self.rows_seen,
            "boards_touched": len(self.boards_touched),
            "inserted": self.upsert.inserted,
            "updated": self.upsert.updated,
            "unchanged": self.upsert.unchanged,
            "reactivated": self.upsert.reactivated,
            "deactivated": self.deactivated,
            "skipped": self.upsert.skipped,
            "duration_s": round(self.duration_s, 2),
            "truncated": self.truncated,
            "error": self.error,
        }


def _row_to_posting(row: dict) -> RawPosting:
    return RawPosting(
        source=row["ats"],
        board_token=row["company_slug"],
        external_id=row["ats_job_id"] or row["url"],
        title=row["title"],
        company_name=row["company_name"] or row["company_slug"],
        source_url=row["url"],
        jd_clean=row.get("description") or "",
        jd_hydrated=bool(row.get("description")),
        location=row.get("location"),
        remote=bool(row.get("remote")),
        # The scraper never derived an original-publish date (most of these
        # boards only give an updated-at or nothing) - honest as "we don't
        # actually know", matching this schema's own posted_at_basis contract.
        posted_at=None,
        posted_at_basis="first_crawl",
    )


async def _fetch_page(
    client: httpx.AsyncClient, base_url: str, key: str, since: str, since_id: int
) -> dict:
    r = await client.get(
        f"{base_url}/export/jobs",
        params={"since": since, "since_id": since_id, "limit": PAGE_LIMIT},
        headers={"x-scraper-key": key},
        timeout=30.0,
    )
    r.raise_for_status()
    return r.json()


async def _load_cursor(session: AsyncSession) -> ScraperImportCursor:
    cursor = await session.get(ScraperImportCursor, CURSOR_ID)
    if cursor is None:
        cursor = ScraperImportCursor(
            id=CURSOR_ID, since=datetime.fromtimestamp(0, tz=UTC), since_id=0
        )
        session.add(cursor)
        await session.flush()
    return cursor


async def run_import(session: AsyncSession, *, max_seconds: int = DEFAULT_MAX_SECONDS) -> ImportResult:
    settings = get_settings()
    if not settings.scraper_export_url or not settings.scraper_export_key:
        raise RuntimeError("SCRAPER_EXPORT_URL / SCRAPER_EXPORT_KEY not configured")

    run = CrawlRun(
        status=CrawlStatus.RUNNING.value,
        providers=["scraper_import"],
        started_at=datetime.now(UTC),
    )
    session.add(run)
    await session.flush()
    run_id = run.id
    await session.commit()

    result = ImportResult(run_id=run_id)
    started = datetime.now(UTC)
    seen_at = started
    cursor_row = await _load_cursor(session)

    try:
        async with httpx.AsyncClient() as client:
            while True:
                if (datetime.now(UTC) - started).total_seconds() > max_seconds:
                    result.truncated = True
                    log.warning(
                        "ingest.scraper_import_truncated", run_id=str(run_id),
                        since=cursor_row.since.isoformat(), since_id=cursor_row.since_id,
                    )
                    break
                page = await _fetch_page(
                    client, settings.scraper_export_url, settings.scraper_export_key,
                    cursor_row.since.isoformat(), cursor_row.since_id,
                )
                rows = page.get("jobs", [])
                result.pages_fetched += 1
                result.rows_seen += len(rows)
                if rows:
                    postings = [_row_to_posting(r) for r in rows]
                    stats = await upsert_postings(session, postings, run_id=run_id, seen_at=seen_at)
                    result.upsert.merge(stats)
                    result.boards_touched.update((p.source, p.board_token) for p in postings)
                next_cursor = page.get("next_cursor")
                # Advance the durable cursor to the LAST ROW OF THIS PAGE regardless
                # of whether the export says there's a next page - even on the final
                # (short) page, its rows have been committed and must not be re-sent
                # next run. `next_cursor` is only which value to request AS THE
                # FOLLOWING page's params, not "did this page's progress count".
                if rows:
                    last = rows[-1]
                    cursor_row.since = datetime.fromisoformat(last["last_seen_at"])
                    cursor_row.since_id = last["id"]
                await session.commit()
                if next_cursor is None:
                    break

        # Only when the export was exhausted, not time-boxed out: a board's
        # postings can span more than one page, so stopping early means no
        # board's list is provably complete for this run, and deactivating on
        # that would risk closing postings that are still live (the same rule
        # deactivate_missing's own docstring states for the direct-crawl worker).
        if not result.truncated:
            for source, board_token in result.boards_touched:
                result.deactivated += await deactivate_missing(
                    session, source=source, board_token=board_token, run_id=run_id, at=seen_at
                )
            await session.commit()

        run.status = CrawlStatus.COMPLETED.value
    except Exception as exc:  # noqa: BLE001 - the run row must record the failure
        result.error = f"{type(exc).__name__}: {exc}"
        run.status = CrawlStatus.FAILED.value
        log.error("ingest.scraper_import_failed", run_id=str(run_id), error=result.error)
    finally:
        result.duration_s = (datetime.now(UTC) - started).total_seconds()
        run.finished_at = datetime.now(UTC)
        run.tokens_attempted = len(result.boards_touched)
        await session.commit()

    log.info("ingest.scraper_import_done", **result.as_dict())
    if result.error:
        raise RuntimeError(result.error)
    return result
