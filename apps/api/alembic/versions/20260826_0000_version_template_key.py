"""Persist the chosen resume template on each version

Revision ID: version_template_key
Revises: interview_prep_id_default
Create Date: 2026-08-26

WHY: the tailor page lets the user pick a template (look), but the Postgres
tailor and finalize endpoints always rendered with the default template because
the selection was never sent to, or read back from, the backend. Storing
`template_key` on the version means every render of it -- tailor completion,
finalize, preview, download -- honors the template the user picked.

Revision id kept short (<= 32 chars) on purpose: `alembic_version.version_num`
is VARCHAR(32), and an over-long id fails the first real deploy, not local runs.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "version_template_key"
down_revision: str | Sequence[str] | None = "interview_prep_id_default"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "resume_versions",
        sa.Column("template_key", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("resume_versions", "template_key")
