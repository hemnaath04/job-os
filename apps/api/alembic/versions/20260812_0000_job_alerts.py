"""Job alerts: subscriptions, digest log, per-job sent log

Revision ID: job_alerts_email_digest
Revises: 0006_resume_engine
Create Date: 2026-08-12

From the `feat/job-alerts` branch.

The revision id is deliberately not "0007". Several feature branches were cut
from the same head at the same time and every one of them numbered its migration
0007 with down_revision 0006_resume_engine, so the ordinals collide and only one
of them could ever have kept it. Naming this revision after what it does instead
of where it sat in one branch's queue means the id stays valid whatever order the
branches land in.

What does still need attention at merge time: `down_revision` here points at
0006_resume_engine, and so does every sibling. Whichever branch merges second has
to repoint its own down_revision at the one that merged first, or Alembic will
see multiple heads. Only the last branch to merge can leave this line alone.

0004 left a note that saved_searches carried last_run_at and last_run_count "so a
future cron job can run them on a schedule and surface deltas without changing
the table shape". This is that cron job, and it does need its own tables: the
schedule, the sent log, and the per-email record all belong to the alert rather
than to the search.

The unique constraint on (user_id, source_key) in alert_sends is the load-bearing
line in this migration. It is what makes "a job already mailed to you never gets
mailed again" a property of the database rather than of a code path that could be
skipped.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "job_alerts_email_digest"
down_revision: str | Sequence[str] | None = "0006_resume_engine"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ALERT_CADENCE_VALUES = ("immediate", "daily", "weekly")
ALERT_DIGEST_STATUS_VALUES = ("sent", "failed", "suppressed_empty")


def upgrade() -> None:
    cadence = postgresql.ENUM(*ALERT_CADENCE_VALUES, name="alert_cadence", create_type=False)
    cadence.create(op.get_bind(), checkfirst=True)
    digest_status = postgresql.ENUM(
        *ALERT_DIGEST_STATUS_VALUES, name="alert_digest_status", create_type=False
    )
    digest_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "alert_subscriptions",
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
            "saved_search_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("saved_searches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("cadence", cadence, nullable=False, server_default="daily"),
        sa.Column("timezone", sa.String, nullable=False, server_default="UTC"),
        sa.Column("send_hour_local", sa.SmallInteger, nullable=False, server_default="8"),
        sa.Column("send_weekday", sa.SmallInteger, nullable=False, server_default="0"),
        sa.Column(
            "quiet_hours_start_local", sa.SmallInteger, nullable=False, server_default="22"
        ),
        sa.Column("quiet_hours_end_local", sa.SmallInteger, nullable=False, server_default="7"),
        sa.Column("active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("unsubscribed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sent_job_count", sa.Integer, nullable=True),
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
        sa.UniqueConstraint(
            "user_id", "saved_search_id", name="uq_alert_subscriptions_user_search"
        ),
        # Hours are local clock hours, not offsets, so anything outside 0..23 is
        # a bug rather than an unusual preference. Checked in the database because
        # the digest run reads these without re-validating them.
        sa.CheckConstraint(
            "send_hour_local BETWEEN 0 AND 23", name="ck_alert_subscriptions_send_hour"
        ),
        sa.CheckConstraint(
            "send_weekday BETWEEN 0 AND 6", name="ck_alert_subscriptions_send_weekday"
        ),
        sa.CheckConstraint(
            "quiet_hours_start_local BETWEEN 0 AND 23",
            name="ck_alert_subscriptions_quiet_start",
        ),
        sa.CheckConstraint(
            "quiet_hours_end_local BETWEEN 0 AND 23", name="ck_alert_subscriptions_quiet_end"
        ),
    )
    op.create_index("ix_alert_subscriptions_user", "alert_subscriptions", ["user_id"])
    op.create_index("ix_alert_subscriptions_active", "alert_subscriptions", ["active"])

    op.create_table(
        "alert_digests",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "subscription_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("alert_subscriptions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", digest_status, nullable=False),
        sa.Column("subject", sa.String, nullable=False),
        sa.Column("job_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("deduped_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("provider", sa.String, nullable=True),
        sa.Column("provider_message_id", sa.String, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_alert_digests_subscription", "alert_digests", ["subscription_id"])
    op.create_index("ix_alert_digests_user_created", "alert_digests", ["user_id", "created_at"])

    op.create_table(
        "alert_sends",
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
            "subscription_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("alert_subscriptions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "digest_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("alert_digests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source", sa.String, nullable=False),
        sa.Column("source_id", sa.String, nullable=False),
        sa.Column("source_key", sa.String, nullable=False),
        sa.Column("content_key", sa.String, nullable=False),
        sa.Column("title", sa.String, nullable=False),
        sa.Column("company_name", sa.String, nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("user_id", "source_key", name="uq_alert_sends_user_source"),
    )
    op.create_index("ix_alert_sends_user_content", "alert_sends", ["user_id", "content_key"])
    op.create_index("ix_alert_sends_digest", "alert_sends", ["digest_id"])


def downgrade() -> None:
    op.drop_index("ix_alert_sends_digest", table_name="alert_sends")
    op.drop_index("ix_alert_sends_user_content", table_name="alert_sends")
    op.drop_table("alert_sends")

    op.drop_index("ix_alert_digests_user_created", table_name="alert_digests")
    op.drop_index("ix_alert_digests_subscription", table_name="alert_digests")
    op.drop_table("alert_digests")

    op.drop_index("ix_alert_subscriptions_active", table_name="alert_subscriptions")
    op.drop_index("ix_alert_subscriptions_user", table_name="alert_subscriptions")
    op.drop_table("alert_subscriptions")

    postgresql.ENUM(name="alert_digest_status").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="alert_cadence").drop(op.get_bind(), checkfirst=True)
