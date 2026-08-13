"""Crawl bookkeeping: which tokens are worth fetching, and what each sweep did.

`AtsBoardToken` is what turns a downloaded token dump into a corpus that gets
better over time. Roughly four tokens in ten are dead, so a crawler that re-reads
a flat file every night spends 38% of its request budget relearning the same
404s. Recording the answer per token, and scheduling the next check from it, is
what makes the sweep both resumable and cheaper each time it runs.

`CrawlRun` exists for the deactivation rule. "A posting absent from a re-crawl is
no longer listed" is only true if the re-crawl actually saw that board's current
list, so deactivation is scoped to boards whose fetch succeeded in a specific run
and is keyed on that run's id. Without a run identity the safe version of that
rule cannot be written, and the unsafe version deactivates a company's entire
board because one request timed out.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import BigInteger, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from job_os.db.models._mixins import UUIDPK, Timestamped
from job_os.db.session import Base


class TokenStatus(StrEnum):
    """Stored as a plain string, not a Postgres ENUM.

    A new ATS vendor or a new failure mode should not need a migration that
    rewrites a type, and nothing joins on this column.
    """

    UNKNOWN = "unknown"
    LIVE = "live"
    #: Answered 200 but listed nothing. Real for a company between hiring rounds,
    #: and the only answer SmartRecruiters ever gives for a token that does not
    #: exist, which is why EMPTY is not treated as proof of death.
    EMPTY = "empty"
    #: 404 or equivalent. Proof the token is wrong.
    MISSING = "missing"
    #: Network or 5xx. Says nothing about the token, only about the attempt.
    ERROR = "error"
    #: Given up on after repeated MISSING or a long run of EMPTY. Not crawled
    #: again until a deliberate revival, which keeps the sweep cheap without
    #: throwing the token away.
    RETIRED = "retired"


class AtsBoardToken(UUIDPK, Timestamped, Base):
    __tablename__ = "ats_board_tokens"
    __table_args__ = (
        UniqueConstraint("provider", "token", name="uq_ats_board_tokens_pair"),
    )

    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    token: Mapped[str] = mapped_column(String, nullable=False)

    #: Known only for curated entries and for Greenhouse, which reports
    #: `company_name` in its payload. Learned from the board where possible.
    company_name: Mapped[str | None] = mapped_column(String, nullable=True)
    company_domain: Mapped[str | None] = mapped_column(String, nullable=True)
    #: Higher goes first and gets re-checked sooner. Curated companies sit at 100.
    priority: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="unknown")
    last_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Last time the board answered with at least one posting. The strongest
    #: single signal that a token is real.
    last_ok_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: The scheduler's only input: crawl tokens whose next check is due. Backing
    #: this off per status is what makes the corpus cheaper each sweep, and
    #: writing it after every check is what makes a sweep resumable after a crash.
    next_check_after: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    checks_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    consecutive_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    consecutive_empty: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    last_http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    last_job_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_job_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    #: The `If-None-Match` value for the next fetch. This column is the whole
    #: conditional-GET saving: measured on this branch, an unchanged Greenhouse
    #: board answers 304 with 0 bytes instead of 843,618.
    etag: Mapped[str | None] = mapped_column(String, nullable=True)
    last_payload_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class CrawlStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class CrawlRun(UUIDPK, Base):
    """One sweep. Rows are small and few, so they are kept indefinitely."""

    __tablename__ = "crawl_runs"

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="running")
    #: Which providers this sweep covered, and the token ceiling it was given.
    #: Needed to read the counters below: 400 tokens attempted is a healthy
    #: incremental sweep or an alarmingly truncated full one depending on this.
    providers: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default="[]")
    token_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)

    tokens_attempted: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    tokens_live: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    tokens_empty: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    tokens_missing: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    tokens_error: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    tokens_not_modified: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )

    postings_seen: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    postings_inserted: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    postings_updated: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    #: Seen again with an identical content hash. The write was skipped and only
    #: `last_seen_at` moved, which is the common case and worth measuring.
    postings_unchanged: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    postings_deactivated: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    postings_reactivated: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    duplicates_marked: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    requests_made: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    bytes_fetched: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    #: Bytes conditional GET avoided, from each board's last known payload size.
    bytes_saved: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")

    def as_summary(self) -> dict[str, object]:
        """Flat dict for logs and the CLI. Keys match the column names."""
        return {
            "run_id": str(self.id),
            "status": self.status,
            "providers": self.providers,
            "duration_ms": self.duration_ms,
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
            "postings_reactivated": self.postings_reactivated,
            "duplicates_marked": self.duplicates_marked,
            "requests_made": self.requests_made,
            "bytes_fetched": self.bytes_fetched,
            "bytes_saved": self.bytes_saved,
        }


#: Placeholder run id for code paths that upsert outside a sweep (tests, one-off
#: backfills). Kept as a module constant so the intent is greppable.
NO_RUN: uuid.UUID | None = None
