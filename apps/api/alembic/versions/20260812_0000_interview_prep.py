"""Add interview prep packs and their questions

Revision ID: interview_prep_20260812
Revises: 0006_resume_engine
Create Date: 2026-08-12

From branch feat/interview-prep.

Deliberately NOT numbered "0007". Four feature branches were cut off
0006_resume_engine at the same time and three siblings had already claimed the
string "0007", so a fourth would have given alembic two revisions with one id:
the loader would either raise on a duplicate or silently take whichever file it
walked last, and the branch that lost would have its tables missing in
production with a migration history that looks clean. A name rather than a
number costs nothing here, since alembic orders by `down_revision` and never by
the id's spelling. When the siblings merge, the four of them become a real
branch point off 0006 and whoever merges last writes the merge revision.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "interview_prep_20260812"
# The head this branch was cut from. Left as-is rather than re-pointed at a
# sibling: the four branches are independent and none of them may assume it
# lands first.
down_revision: str | Sequence[str] | None = "0006_resume_engine"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "interview_preps",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("application_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("resume_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("readiness_score", sa.Numeric(4, 1), nullable=True),
        sa.Column(
            "readiness_report",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("model_estimate", sa.Integer(), nullable=True),
        sa.Column("note", sa.Text(), server_default="", nullable=False),
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
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["application_id"], ["applications.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["resume_version_id"], ["resume_versions.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # Every read is "the newest pack for this application, for this user", so the
    # index carries the ordering column too.
    op.create_index(
        "ix_interview_preps_user_application",
        "interview_preps",
        ["user_id", "application_id", "created_at"],
    )

    op.create_table(
        "interview_questions",
        sa.Column("prep_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("topic", sa.String(), nullable=True),
        sa.Column("difficulty", sa.String(), server_default="core", nullable=False),
        sa.Column("why_asked", sa.Text(), server_default="", nullable=False),
        sa.Column("scaffold", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("gap", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("gap_note", sa.Text(), nullable=True),
        sa.Column(
            "removed_claims",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("flagged", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("confidence", sa.String(), nullable=True),
        sa.Column("times_reviewed", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_review_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(["prep_id"], ["interview_preps.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_interview_questions_prep",
        "interview_questions",
        ["prep_id", "category", "position"],
    )
    # The spaced-review queue asks for flagged questions that are due, so it is
    # served by a partial index rather than a scan over every question ever
    # generated.
    op.create_index(
        "ix_interview_questions_due",
        "interview_questions",
        ["next_review_at"],
        postgresql_where=sa.text("next_review_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_interview_questions_due", table_name="interview_questions")
    op.drop_index("ix_interview_questions_prep", table_name="interview_questions")
    op.drop_table("interview_questions")
    op.drop_index("ix_interview_preps_user_application", table_name="interview_preps")
    op.drop_table("interview_preps")
