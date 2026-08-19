"""scraper_import_cursor

Revision ID: scraper_import_cursor_20260819
Revises: baf8fbce6207
Create Date: 2026-08-19 00:00:00.000000

One durable row so ingest.scraper_import resumes where it left off across
separate process runs, instead of restarting at the export's beginning every
time (a Heroku Scheduler invocation has no memory of the previous one). See
db/models/ingest.py::ScraperImportCursor for the full reasoning.
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = 'scraper_import_cursor_20260819'
down_revision: str | Sequence[str] | None = 'baf8fbce6207'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'scraper_import_cursor',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('since', sa.DateTime(timezone=True), nullable=False),
        sa.Column('since_id', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('scraper_import_cursor')
