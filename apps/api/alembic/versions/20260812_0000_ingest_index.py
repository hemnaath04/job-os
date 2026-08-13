"""Ingest index: job_postings, ats_board_tokens, crawl_runs

The crawl index lives in its own table rather than in `jobs`. The reasoning is in
the module docstring of `db/models/job_posting.py`; the short version is that
`discovery._annotate_already_imported` matches on `(source, source_id)` against
`jobs`, so writing the crawl there would mark every discovery result as already
imported, and `jobs.list_jobs` has no user scope so the tracker list would become
the whole internet.

Revision ID: ingest_index_20260812
Revises: 0006_resume_engine

BRANCH OF ORIGIN: feat/ingest-index.

The revision id deliberately does not claim a sequence number. Four branches were
open against `0006_resume_engine` at once -- feat/ingest-index, feat/job-alerts,
feat/cover-letters and feat/interview-prep -- and all four wrote a migration
called `0007_*`. Numbering only works when one person allocates the numbers, and
that was not the situation, so this revision is named after what it does instead.

`down_revision` stays at the head that existed when the branch was cut. Merging
two of these siblings produces two alembic heads, which is expected and is
resolved at merge time by whoever merges second, either by re-pointing their
`down_revision` at the revision that landed first or by adding a merge revision
(`alembic merge`). The tables here do not touch any table the siblings create, so
the chain order between them does not matter; only that a single chain exists.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "ingest_index_20260812"
down_revision: str | Sequence[str] | None = "0006_resume_engine"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMBEDDING_DIM = 1536
FTS_DESCRIPTION_CHARS = 8_000

SEARCH_VECTOR_SQL = (
    "setweight(to_tsvector('english', coalesce(title, '')), 'A') || "
    "setweight(to_tsvector('english', coalesce(company_name, '')), 'B') || "
    "setweight(to_tsvector('english', coalesce(location, '')), 'C') || "
    f"setweight(to_tsvector('english', left(coalesce(jd_clean, ''), {FTS_DESCRIPTION_CHARS})), 'D')"
)
POSTED_AT_ESTIMATED_SQL = "posted_at_basis IN ('updated', 'first_crawl')"


def upgrade() -> None:
    # pg_trgm backs substring matching on company_name. A btree cannot serve
    # `ILIKE '%acme%'`, and users type fragments of company names.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # --- crawl_runs ---------------------------------------------------------
    # First, because job_postings references it. One row per sweep; the run id is
    # what scopes deactivation safely to boards this run actually re-read.
    op.create_table(
        "crawl_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="running"),
        sa.Column("providers", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("token_limit", sa.Integer(), nullable=True),
        sa.Column("tokens_attempted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_live", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_empty", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_missing", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_error", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_not_modified", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("postings_seen", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("postings_inserted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("postings_updated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("postings_unchanged", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("postings_deactivated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("postings_reactivated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duplicates_marked", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("requests_made", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("bytes_fetched", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("bytes_saved", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("notes", postgresql.JSONB(), nullable=False, server_default="{}"),
    )
    op.create_index(
        "ix_crawl_runs_started", "crawl_runs", [sa.text("started_at DESC")]
    )

    # --- ats_board_tokens ---------------------------------------------------
    op.create_table(
        "ats_board_tokens",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("token", sa.String(), nullable=False),
        sa.Column("company_name", sa.String(), nullable=True),
        sa.Column("company_domain", sa.String(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_ok_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_check_after", sa.DateTime(timezone=True), nullable=True),
        sa.Column("checks_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("consecutive_empty", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_http_status", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_job_count", sa.Integer(), nullable=True),
        sa.Column("max_job_count", sa.Integer(), nullable=True),
        sa.Column("etag", sa.String(), nullable=True),
        sa.Column("last_payload_bytes", sa.BigInteger(), nullable=True),
        sa.UniqueConstraint("provider", "token", name="uq_ats_board_tokens_pair"),
    )
    # The scheduler's only query: what is due, best first. A partial index that
    # excludes retired tokens keeps the hot set small as the dead third of the
    # corpus accumulates.
    op.create_index(
        "ix_ats_board_tokens_due",
        "ats_board_tokens",
        [sa.text("priority DESC"), sa.text("next_check_after ASC NULLS FIRST")],
        postgresql_where=sa.text("status <> 'retired'"),
    )
    op.create_index("ix_ats_board_tokens_status", "ats_board_tokens", ["provider", "status"])

    # --- job_postings -------------------------------------------------------
    op.create_table(
        "job_postings",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("source_id", sa.String(), nullable=False),
        sa.Column("board_token", sa.String(), nullable=False),
        sa.Column("external_id", sa.String(), nullable=False),
        sa.Column("source_url", sa.String(), nullable=False),
        sa.Column("company_name", sa.String(), nullable=False),
        sa.Column("company_domain", sa.String(), nullable=True),
        sa.Column(
            "company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("location", sa.String(), nullable=True),
        sa.Column("country_code", sa.String(2), nullable=True),
        sa.Column("remote", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("anywhere", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("workplace_type", sa.String(32), nullable=True),
        sa.Column("employment_type", sa.String(64), nullable=True),
        sa.Column("department", sa.String(), nullable=True),
        sa.Column("level", sa.String(32), nullable=True),
        sa.Column("function", sa.String(64), nullable=True),
        sa.Column("salary_min", sa.Integer(), nullable=True),
        sa.Column("salary_max", sa.Integer(), nullable=True),
        sa.Column("salary_currency", sa.String(3), nullable=True),
        sa.Column("salary_interval", sa.String(16), nullable=True),
        sa.Column("jd_raw", sa.Text(), nullable=True),
        sa.Column("jd_clean", sa.Text(), nullable=False, server_default=""),
        sa.Column("jd_hydrated", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("jd_parsed", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("jd_embedding", Vector(EMBEDDING_DIM), nullable=True),
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed(SEARCH_VECTOR_SQL, persisted=True),
            nullable=False,
        ),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("dedupe_key", sa.String(), nullable=False),
        sa.Column(
            "canonical_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("job_postings.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("duplicate_reason", sa.String(32), nullable=True),
        sa.Column("duplicate_score", sa.Float(), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("posted_at_basis", sa.String(16), nullable=False, server_default="first_crawl"),
        sa.Column(
            "posted_at_estimated",
            sa.Boolean(),
            sa.Computed(POSTED_AT_ESTIMATED_SQL, persisted=True),
            nullable=False,
        ),
        sa.Column("closes_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("inactive_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("repost_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "last_crawl_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("crawl_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.UniqueConstraint("source", "source_id", name="uq_job_postings_source_pair"),
    )

    # Full-text. GIN over the weighted vector is what makes a keyword search an
    # index scan instead of a sequential read of the whole table.
    op.create_index(
        "ix_job_postings_search_vector",
        "job_postings",
        ["search_vector"],
        postgresql_using="gin",
    )

    # The default browse and the ranking tie-break both sort on the effective
    # date, so the index is on the same expression the ranking uses rather than on
    # posted_at alone, which is null for postings whose board gave no date.
    op.create_index(
        "ix_job_postings_effective_date",
        "job_postings",
        [sa.text("coalesce(posted_at, first_seen_at) DESC")],
        postgresql_where=sa.text("active AND canonical_id IS NULL"),
    )
    op.create_index(
        "ix_job_postings_country_date",
        "job_postings",
        ["country_code", sa.text("coalesce(posted_at, first_seen_at) DESC")],
        postgresql_where=sa.text("active AND canonical_id IS NULL"),
    )
    op.create_index(
        "ix_job_postings_remote_date",
        "job_postings",
        [sa.text("coalesce(posted_at, first_seen_at) DESC")],
        postgresql_where=sa.text("active AND canonical_id IS NULL AND remote"),
    )
    # Substring company search. btree cannot serve `ILIKE '%acme%'`; trigram can.
    op.create_index(
        "ix_job_postings_company_trgm",
        "job_postings",
        ["company_name"],
        postgresql_using="gin",
        postgresql_ops={"company_name": "gin_trgm_ops"},
    )
    # Dedupe lookups, scoped to the rows dedupe can still act on.
    op.create_index(
        "ix_job_postings_dedupe_key",
        "job_postings",
        ["dedupe_key"],
        postgresql_where=sa.text("active AND canonical_id IS NULL"),
    )
    op.create_index("ix_job_postings_content_hash", "job_postings", ["content_hash"])
    # Per-board work: deactivation, and re-reading one company's rows.
    op.create_index("ix_job_postings_board", "job_postings", ["source", "board_token"])
    op.create_index("ix_job_postings_run", "job_postings", ["last_crawl_run_id"])
    # "What changed recently", for incremental exports and for the stats endpoint.
    op.create_index(
        "ix_job_postings_last_seen", "job_postings", [sa.text("last_seen_at DESC")]
    )

    # No HNSW index on jd_embedding yet, deliberately. Nothing populates the
    # column until the enrichment stage lands, and an empty vector index still has
    # to be maintained through every bulk sweep. Create it alongside the first
    # backfill, concurrently so ingest is not blocked:
    #
    #   CREATE INDEX CONCURRENTLY ix_job_postings_embedding_hnsw
    #     ON job_postings USING hnsw (jd_embedding vector_cosine_ops);
    #
    # `jobs` already carries the equivalent index (ix_jobs_jd_embedding_hnsw), so
    # the promotion path is unaffected by its absence here.


def downgrade() -> None:
    op.drop_table("job_postings")
    op.drop_index("ix_ats_board_tokens_status", table_name="ats_board_tokens")
    op.drop_index("ix_ats_board_tokens_due", table_name="ats_board_tokens")
    op.drop_table("ats_board_tokens")
    op.drop_index("ix_crawl_runs_started", table_name="crawl_runs")
    op.drop_table("crawl_runs")
    # pg_trgm is left installed: another migration or a hand-built index may rely
    # on it, and dropping an extension to undo one table is not a safe trade.
