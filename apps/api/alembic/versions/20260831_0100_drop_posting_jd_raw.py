"""Stop storing the vendor markup nothing reads

Revision ID: drop_posting_jd_raw
Revises: postings_back_to_pg
Create Date: 2026-08-31

WHY: `job_postings.jd_raw` is written on every insert and every hydrate, and
read by nothing. Measured against 2,550 real Greenhouse rows while sizing the
rebuilt table: 5,581 compressed bytes per row, the single largest column,
larger even than `jd_clean` at 4,610. Dropping it takes roughly 38% off a
hydrated row.

That matters because the index does not fit the storage it needs to. At 359,000
postings the table projects to about 920 MB at 10% hydration and 1,650 MB at
25%, against a 500 MB free tier. Shortening `search_text` from 8,000 to 600
characters, done in the previous revision, took 35% off. This is the next
largest lever and it costs nothing, because the column has no readers.

Checked rather than assumed: nothing in `services/job_index.py` selects it, and
every `jd_raw` read elsewhere in the codebase is against the `jobs` table, which
is a different thing with a real reader in `routers/outreach.py`.

`RawPosting.jd_raw` stays. Providers naturally produce the vendor's own markup
on the way to building `jd_clean`, and the field is an honest record of what
arrived. What changes is that it is no longer persisted: an accurate in-memory
value costs nothing, and a column costs 5.5 KB a row forever.

Timing is deliberate. `job_postings` was recreated by the previous revision and
does not exist in production yet, so this drops a column that has never held a
row. Written as a forward migration rather than an edit to that revision
because the recreate has already run in CI and on developer machines, and a
migration that changes shape after it has run somewhere is the kind of thing
that bites months later.

Revision id kept short (<= 32 chars): `alembic_version.version_num` is
VARCHAR(32), and an over-long id fails the first real deploy, not local runs.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "drop_posting_jd_raw"
down_revision: str | Sequence[str] | None = "postings_back_to_pg"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("job_postings", "jd_raw")


def downgrade() -> None:
    # Nullable on the way back, because the data is gone and inventing a
    # default would be claiming the vendor sent something it did not.
    op.add_column(
        "job_postings",
        sa.Column("jd_raw", sa.Text(), nullable=True),
    )
