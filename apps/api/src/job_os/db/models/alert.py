"""Standing job alerts: subscription config, per-email log, per-job sent log.

Three tables rather than one, because they answer three different questions and
have three different lifetimes.

`alert_subscriptions` is the user's intent. It hangs off a `saved_searches` row
instead of copying the filters, so editing a saved search edits the alert and the
two cannot drift.

`alert_digests` is one row per email we tried to send. It exists so "did we mail
this person, when, and what did the provider say" is answerable without reading
logs, which is what you need the moment someone reports a duplicate or a
missing digest.

`alert_sends` is one row per job that went into an email, and it is the table
that makes the product honest. Users of the competitor complain that jobs they
have already dismissed keep coming back; the only way not to repeat that is to
remember every job ever mailed to a user and refuse to mail it twice. It is
keyed two ways for that reason:

  source_key   "{source}:{source_id}", the same listing seen again.
  content_key  hash of normalised company + title + location, so the same role
               reposted under a new id, or found through a second source.

Deliberately scoped to the user, not the subscription: two overlapping saved
searches must not both mail the same role.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import (
    ENUM as PgEnum,  # noqa: N811 - repo convention, see models/application.py
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from job_os.db.models._mixins import UUIDPK, Timestamped
from job_os.db.session import Base


class AlertCadence(enum.StrEnum):
    IMMEDIATE = "immediate"
    DAILY = "daily"
    WEEKLY = "weekly"


class AlertDigestStatus(enum.StrEnum):
    SENT = "sent"
    FAILED = "failed"
    #: Built, then dropped before the provider was called. Recorded because a
    #: run that suppresses an empty digest and a run that never happened look
    #: identical otherwise.
    SUPPRESSED_EMPTY = "suppressed_empty"


# One ENUM type per column set, create_type=False so Alembic owns the lifecycle.
# Same pattern as app_status in models/application.py.
alert_cadence_enum = PgEnum(
    AlertCadence,
    name="alert_cadence",
    values_callable=lambda enum_cls: [m.value for m in enum_cls],
    create_type=False,
)
alert_digest_status_enum = PgEnum(
    AlertDigestStatus,
    name="alert_digest_status",
    values_callable=lambda enum_cls: [m.value for m in enum_cls],
    create_type=False,
)


class AlertSubscription(UUIDPK, Timestamped, Base):
    """A saved search promoted to a standing alert."""

    __tablename__ = "alert_subscriptions"
    __table_args__ = (
        # One alert per saved search. A second one would mail the same results
        # twice on two schedules, and the sent log would make the second
        # permanently empty, which is a confusing way to learn that.
        UniqueConstraint(
            "user_id", "saved_search_id", name="uq_alert_subscriptions_user_search"
        ),
        Index("ix_alert_subscriptions_user", "user_id"),
        Index("ix_alert_subscriptions_active", "active"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    saved_search_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("saved_searches.id", ondelete="CASCADE"), nullable=False
    )

    cadence: Mapped[AlertCadence] = mapped_column(
        alert_cadence_enum, nullable=False, default=AlertCadence.DAILY, server_default="daily"
    )
    #: IANA name, e.g. "America/New_York". Every hour field below is a local hour
    #: in this zone. Storing the zone rather than a UTC offset is the difference
    #: between "08:00 my time" surviving a daylight saving change and drifting an
    #: hour twice a year.
    timezone: Mapped[str] = mapped_column(
        String, nullable=False, default="UTC", server_default="UTC"
    )
    #: Local hour a daily or weekly digest goes out.
    send_hour_local: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=8, server_default="8"
    )
    #: Local weekday for a weekly digest. Monday is 0, matching date.weekday().
    send_weekday: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default="0"
    )
    #: Quiet hours, local, half-open [start, end). Gate the `immediate` cadence
    #: only: a daily digest has an explicit send hour, and letting quiet hours
    #: veto that hour would mean a user who picked 23:00 silently never gets mail.
    quiet_hours_start_local: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=22, server_default="22"
    )
    quiet_hours_end_local: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=7, server_default="7"
    )

    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    #: Set when a one-click unsubscribe is honoured. Kept alongside `active` so
    #: "the user turned this off" and "the user unsubscribed by email" stay
    #: distinguishable, which matters if we are ever asked to prove we honoured
    #: an opt-out.
    unsubscribed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Updated on every run, sent or not, so a subscription that keeps producing
    #: nothing is visible as such rather than looking untouched.
    last_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_sent_job_count: Mapped[int | None] = mapped_column(Integer, nullable=True)


class AlertDigest(UUIDPK, Base):
    """One attempted email."""

    __tablename__ = "alert_digests"
    __table_args__ = (
        Index("ix_alert_digests_subscription", "subscription_id"),
        Index("ix_alert_digests_user_created", "user_id", "created_at"),
    )

    subscription_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("alert_subscriptions.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    status: Mapped[AlertDigestStatus] = mapped_column(alert_digest_status_enum, nullable=False)
    subject: Mapped[str] = mapped_column(String, nullable=False)
    job_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    #: How many candidates the sent log dropped. The number that tells you
    #: whether dedupe is working.
    deduped_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    provider: Mapped[str | None] = mapped_column(String, nullable=True)
    provider_message_id: Mapped[str | None] = mapped_column(String, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )


class AlertSend(UUIDPK, Base):
    """One job that went into one email. The dedupe ledger."""

    __tablename__ = "alert_sends"
    __table_args__ = (
        # The constraint that enforces the promise. Scoped to the user so a role
        # cannot arrive twice through two saved searches.
        UniqueConstraint("user_id", "source_key", name="uq_alert_sends_user_source"),
        Index("ix_alert_sends_user_content", "user_id", "content_key"),
        Index("ix_alert_sends_digest", "digest_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    subscription_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("alert_subscriptions.id", ondelete="CASCADE"),
        nullable=False,
    )
    digest_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("alert_digests.id", ondelete="CASCADE"), nullable=False
    )

    source: Mapped[str] = mapped_column(String, nullable=False)
    source_id: Mapped[str] = mapped_column(String, nullable=False)
    #: "{source}:{source_id}". Denormalised because it is the unique key and the
    #: lookup, and computing it in SQL on every read would forfeit the index.
    source_key: Mapped[str] = mapped_column(String, nullable=False)
    content_key: Mapped[str] = mapped_column(String, nullable=False)

    title: Mapped[str] = mapped_column(String, nullable=False)
    company_name: Mapped[str | None] = mapped_column(String, nullable=True)
    #: The date the source claimed, kept as claimed. Contrast first_seen_at.
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: The first time this content was seen by us, which is what freshness is
    #: measured against. Persisted so a later repost can be recognised as one
    #: even after the original listing disappears from the source.
    first_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
