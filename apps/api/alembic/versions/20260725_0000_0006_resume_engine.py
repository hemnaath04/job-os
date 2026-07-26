"""Add verified resume-engine lifecycle and revision chat

Revision ID: 0006_resume_engine
Revises: 0005_resume_pdf_cache
Create Date: 2026-07-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_resume_engine"
down_revision: str | Sequence[str] | None = "0005_resume_pdf_cache"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("resumes", sa.Column("source_kind", sa.String(), nullable=True))
    op.add_column("resumes", sa.Column("source_label", sa.String(), nullable=True))

    op.add_column(
        "resume_versions",
        sa.Column("status", sa.String(), server_default="draft", nullable=False),
    )
    op.add_column("resume_versions", sa.Column("review_score", sa.Numeric(4, 1), nullable=True))
    op.add_column(
        "resume_versions",
        sa.Column("review_report", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "resume_versions",
        sa.Column("parent_version_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column("resume_versions", sa.Column("source_filename", sa.String(), nullable=True))
    op.add_column("resume_versions", sa.Column("revision_note", sa.Text(), nullable=True))
    op.add_column("resume_versions", sa.Column("latex_source", sa.Text(), nullable=True))
    op.add_column(
        "resume_versions",
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_resume_versions_parent",
        "resume_versions",
        "resume_versions",
        ["parent_version_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "resume_revision_messages",
        sa.Column("resume_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "suggestions",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("applied", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["resume_version_id"],
            ["resume_versions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("resume_revision_messages")
    op.drop_constraint("fk_resume_versions_parent", "resume_versions", type_="foreignkey")
    op.drop_column("resume_versions", "finalized_at")
    op.drop_column("resume_versions", "latex_source")
    op.drop_column("resume_versions", "revision_note")
    op.drop_column("resume_versions", "source_filename")
    op.drop_column("resume_versions", "parent_version_id")
    op.drop_column("resume_versions", "review_report")
    op.drop_column("resume_versions", "review_score")
    op.drop_column("resume_versions", "status")
    op.drop_column("resumes", "source_label")
    op.drop_column("resumes", "source_kind")
