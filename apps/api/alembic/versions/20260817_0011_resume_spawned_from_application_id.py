"""resume spawned_from_application_id

Revision ID: baf8fbce6207
Revises: 770a1a428da5
Create Date: 2026-08-17 00:11:04.181859

Autogenerate also proposed dropping ~30 indexes (HNSW vector indexes, GIN
trigram indexes, partial indexes) that exist in the real database but aren't
reflected in the current ORM metadata -- a pre-existing drift, not something
this change touches. Stripped down to just the one real column addition.
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = 'baf8fbce6207'
down_revision: str | Sequence[str] | None = '770a1a428da5'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('resumes', sa.Column('spawned_from_application_id', sa.UUID(), nullable=True))
    op.create_foreign_key(
        'resumes_spawned_from_application_id_fkey',
        'resumes', 'applications', ['spawned_from_application_id'], ['id'], ondelete='SET NULL',
    )


def downgrade() -> None:
    op.drop_constraint('resumes_spawned_from_application_id_fkey', 'resumes', type_='foreignkey')
    op.drop_column('resumes', 'spawned_from_application_id')
