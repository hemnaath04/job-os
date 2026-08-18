"""Copy every `job_postings` row from Neon Postgres into the new Appwrite table.

    .venv/bin/python -m job_os.scripts.migrate_job_postings_to_appwrite

**Not destructive.** This only reads from Postgres and writes to Appwrite; the
Postgres `job_postings` table and its data are left exactly as they are. Do
not drop or truncate them here -- that is a separate, human decision once the
Appwrite path has been verified in production.

**Idempotent and resumable.** Every Appwrite row is created with `$id` set to
the Postgres row's own UUID string (a UUID is a valid Appwrite id: 36
characters, first char alphanumeric, matches `VALID_APPWRITE_ID`), and writes
go through `upsert_rows`, not `create_rows` -- so re-running this script after
an interruption re-sends already-migrated rows as no-op upserts instead of
failing on a duplicate id.

Batched at 100 rows per call, Appwrite's bulk-write ceiling on this plan.

Requires `bootstrap_appwrite_job_postings.ensure_job_postings_table` to have
already been run against a real project -- this script does not create the
table itself, it only fills it.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any

from appwrite.services.tables_db import TablesDB
from sqlalchemy import func, select

from job_os.db.models.job_posting import JobPosting
from job_os.db.session import async_session
from job_os.scripts.appwrite_common import AppwriteAdminConfig

#: Appwrite's bulk-write ceiling on the Education/Pro-equivalent plan.
BATCH_SIZE = 100

#: Columns that exist on the Postgres model but are deliberately not migrated:
#: `jd_embedding` is confirmed unpopulated on every row and unused anywhere in
#: the codebase; `search_vector` is a Postgres-generated tsvector with no
#: Appwrite equivalent, replaced at read time by fulltext columns + Python
#: scoring; `posted_at_estimated` is a generated column, cheaply re-derived
#: from `posted_at_basis` rather than stored twice.
_SKIP_COLUMNS = {"jd_embedding", "search_vector", "posted_at_estimated"}


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def row_to_document(posting: JobPosting) -> dict[str, Any]:
    """One Postgres row -> one Appwrite row dict, keyed by the same UUID."""
    return {
        "$id": str(posting.id),
        "source": posting.source,
        "source_id": posting.source_id,
        "board_token": posting.board_token,
        "external_id": posting.external_id,
        "source_url": posting.source_url,
        "company_name": posting.company_name,
        "company_domain": posting.company_domain,
        "company_id": str(posting.company_id) if posting.company_id else None,
        "title": posting.title,
        "location": posting.location,
        "country_code": posting.country_code,
        "remote": posting.remote,
        "anywhere": posting.anywhere,
        "workplace_type": posting.workplace_type,
        "employment_type": posting.employment_type,
        "department": posting.department,
        "level": posting.level,
        "function": posting.function,
        "salary_min": posting.salary_min,
        "salary_max": posting.salary_max,
        "salary_currency": posting.salary_currency,
        "salary_interval": posting.salary_interval,
        "jd_raw": posting.jd_raw,
        "jd_clean": posting.jd_clean or "",
        "jd_hydrated": posting.jd_hydrated,
        "jd_parsed": json.dumps(posting.jd_parsed or {}),
        "content_hash": posting.content_hash,
        "dedupe_key": posting.dedupe_key,
        "canonical_id": str(posting.canonical_id) if posting.canonical_id else None,
        "duplicate_reason": posting.duplicate_reason,
        "duplicate_score": posting.duplicate_score,
        "posted_at": _iso(posting.posted_at),
        "posted_at_basis": posting.posted_at_basis,
        "closes_at": _iso(posting.closes_at),
        "first_seen_at": _iso(posting.first_seen_at),
        "last_seen_at": _iso(posting.last_seen_at),
        "active": posting.active,
        "inactive_since": _iso(posting.inactive_since),
        "repost_count": posting.repost_count,
        "last_crawl_run_id": (
            str(posting.last_crawl_run_id) if posting.last_crawl_run_id else None
        ),
    }


async def migrate(tables: TablesDB, config: AppwriteAdminConfig) -> tuple[int, int]:
    """Returns (postgres_row_count, appwrite_rows_written)."""
    written = 0
    async with async_session() as session:
        total_count = await session.scalar(select(func.count()).select_from(JobPosting))

        result = await session.stream_scalars(select(JobPosting).order_by(JobPosting.id))
        batch: list[dict[str, Any]] = []
        async for posting in result:
            batch.append(row_to_document(posting))
            if len(batch) >= BATCH_SIZE:
                tables.upsert_rows(config.database_id, config.job_postings_table_id, batch)
                written += len(batch)
                print(f"  ...{written} rows written")
                batch = []
        if batch:
            tables.upsert_rows(config.database_id, config.job_postings_table_id, batch)
            written += len(batch)
    return int(total_count or 0), written


async def main() -> None:
    config = AppwriteAdminConfig.from_environment()
    tables = TablesDB(config.client())
    total_count, written = await migrate(tables, config)
    print(f"Postgres job_postings rows: {total_count}")
    print(f"Appwrite rows written:      {written}")
    if written != total_count:
        print("MISMATCH -- investigate before trusting the Appwrite copy.")
    print("Postgres job_postings left untouched -- this script only reads from it.")


if __name__ == "__main__":
    asyncio.run(main())
