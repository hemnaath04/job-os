"""Bring `job_postings` back to Postgres, with a 600-char search slice

Revision ID: postings_back_to_pg
Revises: jobs_source_url_key
Create Date: 2026-08-31

WHY THIS EXISTS AT ALL
----------------------
`ingest_index_20260812` created `job_postings`. Commit 4fcf37d then moved the
read and write paths to Appwrite TablesDB, and the Postgres table was dropped by
hand, outside Alembic, to reclaim 467 MB on Neon's free tier. So on the live
database the table is gone while `alembic_version` still says the migration that
created it has run. Nothing Alembic knows about would ever put it back.

Appwrite bills database reads PER ROW ("if you fetch a table of 50 rows with a
single API call, this counts as 50 read operations"). At ~359,000 rows, a search
pool of up to 2,000 rows and a 6-hourly crawl measured at ~1.19M reads a month
against a 1.75M allowance, the project ran out of reads and every Appwrite call
started answering 402 `limit_databases_reads_exceeded` -- including the resume
and tailor paths, which share the quota. Postgres charges for storage instead,
which is a resource this workload can actually bound.

TWO PATHS, BECAUSE THERE ARE TWO REALITIES
------------------------------------------
This migration has to be correct on a database where the table was dropped
(production) and on one where `ingest_index_20260812` created it minutes ago and
nobody dropped anything (CI, a fresh local checkout). An unconditional
`create_table` fails on the second; an unconditional `ALTER` fails on the first.
So it inspects, and each branch says what it is for.

`DROP TABLE IF EXISTS` then recreate would collapse the two, and is rejected:
on any database that still holds crawled rows it silently destroys them to
change one column's expression.

WHAT ACTUALLY CHANGES, ON BOTH PATHS
------------------------------------
`search_vector`'s generated expression reads the first 600 characters of
`jd_clean` rather than the first 8,000. See `db/models/job_posting.py`'s
`FTS_DESCRIPTION_CHARS` for the measured storage arithmetic and for the one
thing this costs (deep-body recall, because the body is already the D-weighted
zone). `jd_clean` itself is untouched and must stay untouched: it is the input
to `job_enrich.enrich_job`.

Postgres cannot rewrite a generated column's expression in place before 17, and
this project cannot assume 17, so the column is dropped and re-added. Dropping
it drops `ix_job_postings_search_vector` with it, so the index is recreated
here rather than left to be missed.

The two paths were diffed with `pg_dump -s` and are identical except for one
thing: on the ALTER path `search_vector` ends up as the LAST column, because
dropping and re-adding it loses its ordinal position. Column definition,
nullability, generation expression, indexes and constraints all match. That
divergence is harmless here because nothing in this codebase writes
`job_postings` positionally -- every INSERT names its columns -- and it is
recorded rather than papered over because an `INSERT ... VALUES` with no column
list would notice.

WHY DOWNGRADE DOES NOT DROP THE TABLE
-------------------------------------
It restores the 8,000-character expression and stops. Dropping the table would
delete however many hundred thousand crawled rows exist in order to undo a
column expression, which is not a reversal, it is data loss. `job_postings`'
creation and destruction belong to `ingest_index_20260812`, which still owns
both. Downgrading then upgrading again round-trips: the second upgrade finds the
table present and takes the ALTER path.

Revision id kept to 19 chars: `alembic_version.version_num` is VARCHAR(32).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "postings_back_to_pg"
down_revision: str | Sequence[str] | None = "jobs_source_url_key"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMBEDDING_DIM = 1536

#: The change. Deliberately spelled out here rather than imported from
#: `db/models/job_posting.py`: a migration has to keep describing the schema it
#: produced even after the model moves on, and an import would silently rewrite
#: this file's meaning the next time that constant changes.
FTS_DESCRIPTION_CHARS = 600
PREVIOUS_FTS_DESCRIPTION_CHARS = 8_000

POSTED_AT_ESTIMATED_SQL = "posted_at_basis IN ('updated', 'first_crawl')"


def _search_vector_sql(description_chars: int) -> str:
    return (
        "setweight(to_tsvector('english', coalesce(title, '')), 'A') || "
        "setweight(to_tsvector('english', coalesce(company_name, '')), 'B') || "
        "setweight(to_tsvector('english', coalesce(location, '')), 'C') || "
        "setweight(to_tsvector('english', "
        f"left(coalesce(jd_clean, ''), {description_chars})), 'D')"
    )


def _table_exists() -> bool:
    return sa.inspect(op.get_bind()).has_table("job_postings")


def _rebuild_search_vector(description_chars: int) -> None:
    """Point `search_vector` at a different slice of `jd_clean`.

    Drop-and-add rather than `ALTER COLUMN ... SET EXPRESSION`, which only
    exists from PostgreSQL 17 and would make this migration refuse to run on
    anything older. The GIN index goes with the column and comes back after it.
    """
    op.drop_column("job_postings", "search_vector")
    op.add_column(
        "job_postings",
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed(_search_vector_sql(description_chars), persisted=True),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_job_postings_search_vector",
        "job_postings",
        ["search_vector"],
        postgresql_using="gin",
    )


def _create_table() -> None:
    """The table as `ingest_index_20260812` built it, with the shorter slice.

    Column-for-column identical to that migration on purpose, so a database
    that took this path and one that took the ALTER path end up the same
    shape. The only difference is `FTS_DESCRIPTION_CHARS`.
    """
    # `job_postings.company_name` is searched with `ILIKE '%acme%'`, which a
    # btree cannot serve. Already installed on any database that ran
    # `ingest_index_20260812`; repeated here because this branch runs on ones
    # where the table is gone and nothing guarantees what else was cleaned up.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "job_postings",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
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
            sa.Computed(_search_vector_sql(FTS_DESCRIPTION_CHARS), persisted=True),
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
        sa.Column(
            "posted_at_basis", sa.String(16), nullable=False, server_default="first_crawl"
        ),
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
    # date, so the index is on the same expression the ranking uses rather than
    # on posted_at alone, which is null for postings whose board gave no date.
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
    # No index for `ingest/hydrate.py::_candidate_rows` (`active AND NOT
    # jd_hydrated AND canonical_id IS NULL ORDER BY last_seen_at DESC`), even
    # though it would help. That query runs in a scheduled pass with no user
    # waiting on it, and every index is storage in a change whose entire
    # purpose is storage. Add it if the hydration pass ever becomes the slow
    # part of a sweep, which it is not today.


def upgrade() -> None:
    if _table_exists():
        # The table survived (CI, a fresh checkout, any database nobody
        # reclaimed space on). Only the slice changes.
        _rebuild_search_vector(FTS_DESCRIPTION_CHARS)
        return
    _create_table()


def downgrade() -> None:
    if not _table_exists():
        # Nothing to undo. Reached only if something dropped the table between
        # this migration and its downgrade, which is the state production was
        # in before this migration existed.
        return
    _rebuild_search_vector(PREVIOUS_FTS_DESCRIPTION_CHARS)
