"""initial M1 schema — users, companies, jobs, applications

Revision ID: 0001_initial_m1
Revises:
Create Date: 2026-06-19

Sets up the M1 tracker tables plus pgcrypto + pgvector extensions and the
app_status enum. Resume, profile, and agent tables come in later migrations.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial_m1"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_STATUS_VALUES = (
    "wishlist",
    "ready_to_apply",
    "applied",
    "oa_received",
    "interview_scheduled",
    "offer",
    "accepted",
    "rejected",
    "withdrawn",
    "ghosted",
)
EMBEDDING_DIM = 1536  # text-embedding-3-large truncated (HNSW max is 2000 dims)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    app_status = postgresql.ENUM(*APP_STATUS_VALUES, name="app_status", create_type=False)
    app_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("clerk_id", sa.String, nullable=False, unique=True),
        sa.Column("email", sa.String, nullable=False, unique=True),
        sa.Column("display_name", sa.String, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )

    op.create_table(
        "companies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("domain", sa.String, nullable=True),
        sa.Column("linkedin_url", sa.String, nullable=True),
        sa.Column("industry", sa.String, nullable=True),
        sa.Column("size_bucket", sa.String, nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("research", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index(
        "uq_companies_name_domain",
        "companies",
        [sa.text("lower(name)"), sa.text("coalesce(domain, '')")],
        unique=True,
    )

    op.create_table(
        "jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("companies.id", ondelete="SET NULL"), nullable=True),
        sa.Column("title", sa.String, nullable=False),
        sa.Column("level", sa.String, nullable=True),
        sa.Column("function", sa.String, nullable=True),
        sa.Column("location", sa.String, nullable=True),
        sa.Column("remote", sa.String, nullable=True),
        sa.Column("salary_min", sa.Integer, nullable=True),
        sa.Column("salary_max", sa.Integer, nullable=True),
        sa.Column("salary_currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("jd_raw", sa.Text, nullable=False),
        sa.Column("jd_clean", sa.Text, nullable=False),
        sa.Column("jd_embedding", Vector(EMBEDDING_DIM), nullable=True),
        sa.Column("jd_parsed", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("source", sa.String, nullable=False),
        sa.Column("source_id", sa.String, nullable=True),
        sa.Column("source_url", sa.String, nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closes_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("source", "source_id", name="uq_jobs_source_pair"),
    )
    op.create_index("ix_jobs_active_posted", "jobs", ["active", sa.text("posted_at DESC")])
    op.execute(
        "CREATE INDEX ix_jobs_jd_embedding_hnsw ON jobs "
        "USING hnsw (jd_embedding vector_cosine_ops)"
    )

    op.create_table(
        "applications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(*APP_STATUS_VALUES, name="app_status", create_type=False),
            nullable=False,
            server_default="wishlist",
        ),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recruiter_name", sa.String, nullable=True),
        sa.Column("recruiter_email", sa.String, nullable=True),
        sa.Column("recruiter_linkedin", sa.String, nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("next_action_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_action_label", sa.String, nullable=True),
        sa.Column("archived", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("user_id", "job_id", name="uq_applications_user_job"),
    )
    op.create_index("ix_applications_user_status", "applications", ["user_id", "status"])

    op.create_table(
        "application_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("application_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("applications.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String, nullable=False),
        sa.Column(
            "from_status",
            postgresql.ENUM(*APP_STATUS_VALUES, name="app_status", create_type=False),
            nullable=True,
        ),
        sa.Column(
            "to_status",
            postgresql.ENUM(*APP_STATUS_VALUES, name="app_status", create_type=False),
            nullable=True,
        ),
        sa.Column("payload", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index("ix_application_events_app", "application_events", ["application_id"])


def downgrade() -> None:
    op.drop_index("ix_application_events_app", table_name="application_events")
    op.drop_table("application_events")
    op.drop_index("ix_applications_user_status", table_name="applications")
    op.drop_table("applications")
    op.execute("DROP INDEX IF EXISTS ix_jobs_jd_embedding_hnsw")
    op.drop_index("ix_jobs_active_posted", table_name="jobs")
    op.drop_table("jobs")
    op.drop_index("uq_companies_name_domain", table_name="companies")
    op.drop_table("companies")
    op.drop_table("users")
    op.execute("DROP TYPE IF EXISTS app_status")
