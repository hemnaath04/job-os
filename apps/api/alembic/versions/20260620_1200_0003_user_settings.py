"""User settings JSONB column

Revision ID: 0003_user_settings
Revises: 0002_m2_profile_and_resumes
Create Date: 2026-06-20

Adds a small per-user preferences blob (theme, default tailoring template,
default discovery filters, timezone). Stored as JSONB so we can evolve the
shape without further migrations — Pydantic validates the accepted keys.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_user_settings"
down_revision: str | Sequence[str] | None = "0002_m2_profile_and_resumes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "settings",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "settings")
