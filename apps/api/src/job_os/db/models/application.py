from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from job_os.db.models._mixins import UUIDPK, Timestamped
from job_os.db.session import Base

if TYPE_CHECKING:
    from job_os.db.models.job import Job


class AppStatus(str, enum.Enum):
    WISHLIST = "wishlist"
    READY_TO_APPLY = "ready_to_apply"
    APPLIED = "applied"
    OA_RECEIVED = "oa_received"
    INTERVIEW_SCHEDULED = "interview_scheduled"
    OFFER = "offer"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"
    GHOSTED = "ghosted"


# Reuse one ENUM type across columns; create_type=False so Alembic owns the lifecycle.
app_status_enum = PgEnum(
    AppStatus,
    name="app_status",
    values_callable=lambda enum_cls: [m.value for m in enum_cls],
    create_type=False,
)


class Application(UUIDPK, Timestamped, Base):
    __tablename__ = "applications"
    __table_args__ = (
        UniqueConstraint("user_id", "job_id", name="uq_applications_user_job"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=False
    )

    status: Mapped[AppStatus] = mapped_column(
        app_status_enum, nullable=False, default=AppStatus.WISHLIST, server_default="wishlist"
    )
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    recruiter_name: Mapped[str | None] = mapped_column(String, nullable=True)
    recruiter_email: Mapped[str | None] = mapped_column(String, nullable=True)
    recruiter_linkedin: Mapped[str | None] = mapped_column(String, nullable=True)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    next_action_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_action_label: Mapped[str | None] = mapped_column(String, nullable=True)

    archived: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    job: Mapped[Job] = relationship(lazy="joined")


class ApplicationEvent(UUIDPK, Base):
    """Event log per application — status transitions, notes, recruiter emails, etc."""

    __tablename__ = "application_events"

    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("applications.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String, nullable=False)

    from_status: Mapped[AppStatus | None] = mapped_column(app_status_enum, nullable=True)
    to_status: Mapped[AppStatus | None] = mapped_column(app_status_enum, nullable=True)

    payload: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default="now()", nullable=False
    )
