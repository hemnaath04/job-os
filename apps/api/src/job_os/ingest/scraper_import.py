"""Pull from the standalone job-scraper's bulk export and write into
`job_postings`, reusing this package's own upsert/dedup machinery rather than
crawling BambooHR/Workday/iCIMS a second time from job.os's own infra.

The scraper (a separate personal project, deliberately outside this repo - see
its own README) already crawls Greenhouse/Lever/Ashby/SmartRecruiters/
BambooHR/Workday/iCIMS nightly on its own schedule and exposes a paginated,
shared-secret-gated export at GET {SCRAPER_EXPORT_URL}/export/jobs. This
module is the pull side: no crawling happens here, only normalize + upsert.

    uv run python -m job_os.ingest.cli import-scraper
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from job_os.db.models.ingest import CrawlRun, CrawlStatus
from job_os.ingest.providers import RawPosting
from job_os.ingest.upsert import UpsertStats, deactivate_missing, upsert_postings
from job_os.settings import get_settings

log = structlog.get_logger(__name__)

PAGE_LIMIT = 1000


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


async def _fetch_page(client: httpx.AsyncClient, base_url: str, key: str, since_id: int) -> dict:
    r = await client.get(
        f"{base_url}/export/jobs",
        params={"since_id": since_id, "limit": PAGE_LIMIT},
        headers={"x-scraper-key": key},
        timeout=30.0,
    )
    r.raise_for_status()
    return r.json()


async def run_import(session: AsyncSession) -> ImportResult:
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

    try:
        async with httpx.AsyncClient() as client:
            cursor = 0
            while True:
                page = await _fetch_page(
                    client, settings.scraper_export_url, settings.scraper_export_key, cursor
                )
                rows = page.get("jobs", [])
                result.pages_fetched += 1
                result.rows_seen += len(rows)
                if rows:
                    postings = [_row_to_posting(r) for r in rows]
                    stats = await upsert_postings(session, postings, run_id=run_id, seen_at=seen_at)
                    result.upsert.merge(stats)
                    await session.commit()
                    result.boards_touched.update((p.source, p.board_token) for p in postings)
                cursor = page.get("next_cursor")
                if cursor is None:
                    break

        # Only boards this run actually saw a current list for get to close out
        # rows that disappeared - same safety rule the direct-crawl worker uses.
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
