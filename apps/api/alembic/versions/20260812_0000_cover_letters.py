"""Add cover letters and their versions

Revision ID: cover_letters_20260812
Revises: 0006_resume_engine
Create Date: 2026-08-12

From branch `feat/cover-letters`.

WHY THIS REVISION ID IS NOT `0007_cover_letters`. Four feature branches came off
`0006_resume_engine` at once and every one of them minted a revision numbered
0007. Alembic keys a revision by its id and by nothing else, so four rows
claiming `0007_...` are only distinct if the suffixes differ, and two branches
that picked the same short suffix would produce a migration directory that
cannot be loaded at all: `alembic` raises on a duplicate revision id before it
runs anything. Naming this one after the branch that wrote it, and the date,
makes the collision impossible rather than unlikely.

CORRECTION 2026-08-13: the first attempt at that name was
`0007_cover_letters_feat_cover_letters`, 37 characters. Alembic's own
`alembic_version.version_num` column is `VARCHAR(32)` by default, so that id
upgraded cleanly in every local and CI run (sqlite and a freshly-created
Postgres both happened not to enforce the width) and then failed on the first
real deploy with `StringDataRightTruncationError`, rolling back the entire
multi-branch `upgrade head` as one transaction. Collision-proofing a revision
id is worth nothing if the id does not fit in the column that stores it; stay
well under 32 characters, not just unique.

`down_revision` deliberately stays at the current head. These branches are
genuine siblings: each adds its own tables and none reads another's, so they are
four independent heads by nature and not by accident. Whoever merges them second
runs `alembic merge` to join the heads, which is the operation designed for
exactly this and is a no-op migration. Rewriting this branch's parent to point at
a sibling would instead invent an ordering nothing requires, and would break the
moment the branches merge in a different order than the one guessed here.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "cover_letters_20260812"
down_revision: str | Sequence[str] | None = "0006_resume_engine"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cover_letters",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "name", name="uq_cover_letters_user_name"),
    )
    op.create_index(
        "ix_cover_letters_user_job", "cover_letters", ["user_id", "job_id"]
    )

    op.create_table(
        "cover_letter_versions",
        sa.Column("cover_letter_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "provenance",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column(
            "gap_questions",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column(
            "refused",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column(
            "quality_flags",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("tone", sa.String(), server_default="plain", nullable=False),
        sa.Column("template_key", sa.String(), nullable=True),
        sa.Column("word_count", sa.Integer(), nullable=True),
        sa.Column("agent_note", sa.Text(), nullable=True),
        sa.Column("spawned_from_job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "spawned_from_application_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column("parent_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(), server_default="draft", nullable=False),
        sa.Column(
            "approved_by_user", sa.Boolean(), server_default="false", nullable=False
        ),
        sa.Column("pdf_bytes", sa.LargeBinary(), nullable=True),
        sa.Column("revision_note", sa.Text(), nullable=True),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
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
            ["cover_letter_id"], ["cover_letters.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["spawned_from_job_id"], ["jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["spawned_from_application_id"], ["applications.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["parent_version_id"], ["cover_letter_versions.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_cover_letter_versions_letter_created",
        "cover_letter_versions",
        ["cover_letter_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_cover_letter_versions_letter_created", table_name="cover_letter_versions"
    )
    op.drop_table("cover_letter_versions")
    op.drop_index("ix_cover_letters_user_job", table_name="cover_letters")
    op.drop_table("cover_letters")
