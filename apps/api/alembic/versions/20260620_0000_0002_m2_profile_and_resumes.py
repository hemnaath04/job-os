"""M2 — profile facts, fact bullets, resumes, resume versions

Revision ID: 0002_m2_profile_and_resumes
Revises: 0001_initial_m1
Create Date: 2026-06-20

Adds the candidate knowledge base + resume version store. fact_bullets get
an HNSW vector(1536) index for similarity search by the M3 tailoring agent.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0002_m2_profile_and_resumes"
down_revision: str | Sequence[str] | None = "0001_initial_m1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMBEDDING_DIM = 1536


def upgrade() -> None:
    op.create_table(
        "profile_facts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String, nullable=False),
        sa.Column("title", sa.String, nullable=False),
        sa.Column("org", sa.String, nullable=True),
        sa.Column("start_date", sa.Date, nullable=True),
        sa.Column("end_date", sa.Date, nullable=True),
        sa.Column("location", sa.String, nullable=True),
        sa.Column("payload", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("verified", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("source_url", sa.String, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_profile_facts_user_kind", "profile_facts", ["user_id", "kind"])

    op.create_table(
        "fact_bullets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("fact_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("profile_facts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("target_role", sa.String, nullable=True),
        sa.Column("metric_verified", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_fact_bullets_fact", "fact_bullets", ["fact_id"])
    op.execute(
        "CREATE INDEX ix_fact_bullets_embedding_hnsw ON fact_bullets "
        "USING hnsw (embedding vector_cosine_ops)"
    )

    op.create_table(
        "resumes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("base_role", sa.String, nullable=True),
        sa.Column("is_master", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("user_id", "name", name="uq_resumes_user_name"),
    )

    op.create_table(
        "resume_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("resume_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("resumes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("json_resume", postgresql.JSONB, nullable=False),
        sa.Column("spawned_from_application_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("applications.id", ondelete="SET NULL"), nullable=True),
        sa.Column("spawned_from_job_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("provenance", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("ats_score", sa.Numeric(4, 1), nullable=True),
        sa.Column("ats_report", postgresql.JSONB, nullable=True),
        sa.Column("approved_by_user", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("pdf_r2_key", sa.String, nullable=True),
        sa.Column("docx_r2_key", sa.String, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_resume_versions_resume", "resume_versions", ["resume_id", sa.text("created_at DESC")])


def downgrade() -> None:
    op.drop_index("ix_resume_versions_resume", table_name="resume_versions")
    op.drop_table("resume_versions")
    op.drop_table("resumes")
    op.execute("DROP INDEX IF EXISTS ix_fact_bullets_embedding_hnsw")
    op.drop_index("ix_fact_bullets_fact", table_name="fact_bullets")
    op.drop_table("fact_bullets")
    op.drop_index("ix_profile_facts_user_kind", table_name="profile_facts")
    op.drop_table("profile_facts")
