"""Cache rendered PDF bytes on ResumeVersion

Revision ID: 0005_resume_pdf_cache
Revises: 0004_saved_searches
Create Date: 2026-06-21

Adds a `pdf_bytes` BYTEA column to `resume_versions`. We render the PDF
once at tailor time (via FastAPI BackgroundTask) and persist the bytes
here so subsequent /download requests are instant — important because
the Vercel hobby-plan serverless proxy times out at ~60s, which is
unfriendly to Render's cold-start + per-click WeasyPrint render path.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_resume_pdf_cache"
down_revision: str | Sequence[str] | None = "0004_saved_searches"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "resume_versions",
        sa.Column("pdf_bytes", sa.LargeBinary(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("resume_versions", "pdf_bytes")
