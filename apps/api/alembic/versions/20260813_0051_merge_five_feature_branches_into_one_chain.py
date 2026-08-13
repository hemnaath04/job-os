"""merge five feature branches into one chain

Revision ID: 770a1a428da5
Revises: 0007_outreach_contacts, cover_letters_20260812, ingest_index_20260812, interview_prep_20260812, job_alerts_email_digest
Create Date: 2026-08-13 00:51:35.655827

Updated 2026-08-13: one parent renamed from `0007_cover_letters_feat_cover_letters`
to `cover_letters_20260812` after that id proved too long for Alembic's own
`alembic_version.version_num` (VARCHAR(32)) and failed on the real deploy. See
the cover-letters migration's own header for the full story.
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = '770a1a428da5'
down_revision: str | Sequence[str] | None = ('0007_outreach_contacts', 'cover_letters_20260812', 'ingest_index_20260812', 'interview_prep_20260812', 'job_alerts_email_digest')
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
