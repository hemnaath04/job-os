"""Store the comparable form of each job's source URL

Revision ID: jobs_source_url_key
Revises: version_template_key
Create Date: 2026-08-29

WHY: the same posting could be saved several times. `create_from_url`
deduplicated on the raw `source_url` string and only within `source == "url"`,
so a link with a `?gh_src=` on it, a trailing slash, a `www.`, or one that had
already arrived through discovery all produced a second row for one job -- with
its own card, its own tailoring runs and its own application history.

This adds the normalised key the lookup can actually match on, and an index so
that lookup stays an equality probe instead of a scan.

Deliberately NOT unique. Rows that are already duplicated would make creating a
unique index fail on the first deploy, and merging them is a destructive
operation that belongs in a script somebody runs and reads the output of
(`job_os.scripts.merge_duplicate_jobs`), not in a migration that runs itself.

Revision id kept short (<= 32 chars): `alembic_version.version_num` is
VARCHAR(32), and an over-long id fails the first real deploy, not local runs.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "jobs_source_url_key"
down_revision: str | Sequence[str] | None = "version_template_key"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("source_url_key", sa.String(), nullable=True))

    # Backfilled in Python, through the same function the application uses, so
    # that existing rows and new ones are keyed identically. A SQL rewrite of
    # the normaliser would be a second implementation to keep in step, and the
    # first time the two disagreed it would show up as a duplicate nobody could
    # explain.
    from job_os.services.job_identity import canonical_url

    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id, source_url FROM jobs WHERE source_url IS NOT NULL")
    ).fetchall()
    for row in rows:
        key = canonical_url(row.source_url)
        if key is None:
            continue
        bind.execute(
            sa.text("UPDATE jobs SET source_url_key = :key WHERE id = :id"),
            {"key": key, "id": row.id},
        )

    op.create_index("ix_jobs_source_url_key", "jobs", ["source_url_key"])


def downgrade() -> None:
    op.drop_index("ix_jobs_source_url_key", table_name="jobs")
    op.drop_column("jobs", "source_url_key")
