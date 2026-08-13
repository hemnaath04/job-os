"""Outreach contacts, one person per application

Revision ID: 0007_outreach_contacts
Revises: 0006_resume_engine
Create Date: 2026-08-12

Deliberately NOT called plain "0007". Three sibling feature branches were being
written against 0006 at the same time as this one and a bare ordinal would have
collided with all of them, so every new revision here carries what it does in its
id. Whoever merges these will still have several heads pointing at 0006 and will
need one `alembic merge`; distinct ids are what makes that merge possible instead
of a conflict on the same filename.

No table for messages. Outreach history is written to `application_events` with
kinds `outreach_drafted` and `outreach_sent`, so the existing application
timeline shows it for free and there is one answer to "what happened with this
application" rather than two.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_outreach_contacts"
down_revision: str | Sequence[str] | None = "0006_resume_engine"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "outreach_contacts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "application_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("applications.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("full_name", sa.String, nullable=False),
        # Written by the service from the email, or from the folded name when
        # there is no email. Never accepted from the client, because it is what
        # the uniqueness constraint below keys on.
        sa.Column("identity_key", sa.String, nullable=False),
        sa.Column("title", sa.String, nullable=True),
        sa.Column("company_name", sa.String, nullable=True),
        sa.Column("email", sa.String, nullable=True),
        sa.Column("email_source", sa.String, nullable=True),
        sa.Column("confidence", sa.Integer, nullable=True),
        sa.Column("linkedin_url", sa.String, nullable=True),
        sa.Column("evidence_url", sa.String, nullable=True),
        sa.Column(
            "relationship_kind",
            sa.String,
            nullable=False,
            server_default="other",
        ),
        sa.Column("provider", sa.String, nullable=False, server_default="manual"),
        sa.Column("shared_school", sa.String, nullable=True),
        sa.Column("shared_employer", sa.String, nullable=True),
        sa.Column("referred_by", sa.String, nullable=True),
        sa.Column(
            "do_not_contact",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # One row per person per application. Without it the double-message guard
        # is defeated by adding the same person twice, which is exactly what a
        # user does after failing to find them in the list.
        sa.UniqueConstraint(
            "application_id", "identity_key", name="uq_outreach_contacts_app_identity"
        ),
    )
    op.create_index(
        "ix_outreach_contacts_application", "outreach_contacts", ["application_id"]
    )
    op.create_index("ix_outreach_contacts_user", "outreach_contacts", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_outreach_contacts_user", table_name="outreach_contacts")
    op.drop_index("ix_outreach_contacts_application", table_name="outreach_contacts")
    op.drop_table("outreach_contacts")
