"""Give interview_preps.id and interview_questions.id the default they were always meant to have

Revision ID: interview_prep_id_default
Revises: scraper_import_cursor_20260819
Create Date: 2026-08-22

The original migration (interview_prep_20260812) gave `created_at`/
`updated_at` a `server_default=sa.text("now()")` on both tables but left
`id` as a plain `nullable=False` column with no default at all -- every
sibling table (`applications`, `jobs`, ...) correctly carries
`gen_random_uuid()` on `id`; these two never did. `UUIDPK` (the Python
model mixin every one of these tables uses) has always assumed the
database fills `id` in, so INSERTing through the ORM without supplying
one hits a real NOT NULL violation.

This was invisible until today: every real `/interview-prep/generate`
call hit Heroku's 30-second router timeout (H12) before the request ever
reached this INSERT, at least once real production data was involved (a
job description of any size plus a retry on a first unusable reply
easily passes 30s). Converting that endpoint to a background job (see
routers/interviews.py) let a request finally survive long enough to
reach this INSERT and reveal it. Confirmed directly against the live
table (`\\d interview_preps`) rather than assumed: `id` has `Default:`
empty; `applications.id` and `jobs.id` both show `gen_random_uuid()`.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "interview_prep_id_default"
down_revision: str | Sequence[str] | None = "scraper_import_cursor_20260819"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "interview_preps", "id", server_default=sa.text("gen_random_uuid()")
    )
    op.alter_column(
        "interview_questions", "id", server_default=sa.text("gen_random_uuid()")
    )


def downgrade() -> None:
    op.alter_column("interview_questions", "id", server_default=None)
    op.alter_column("interview_preps", "id", server_default=None)
