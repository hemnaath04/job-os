from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import EmailStr, Field

from job_os.db.models import AppStatus
from job_os.schemas.common import ORMModel, TimestampedRead
from job_os.schemas.jobs import JobRead


class ApplicationRead(TimestampedRead):
    job: JobRead
    status: AppStatus
    applied_at: datetime | None
    recruiter_name: str | None
    recruiter_email: str | None
    recruiter_linkedin: str | None
    notes: str | None
    next_action_at: datetime | None
    next_action_label: str | None
    archived: bool


class ApplicationCreate(ORMModel):
    job_id: UUID
    status: AppStatus = AppStatus.WISHLIST
    notes: str | None = None


class ApplicationPatch(ORMModel):
    status: AppStatus | None = None
    applied_at: datetime | None = None
    recruiter_name: str | None = None
    recruiter_email: EmailStr | None = None
    recruiter_linkedin: str | None = None
    notes: str | None = None
    next_action_at: datetime | None = None
    next_action_label: str | None = None
    archived: bool | None = None


class ApplicationEventRead(ORMModel):
    id: UUID
    kind: str
    from_status: AppStatus | None
    to_status: AppStatus | None
    payload: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime


class ApplicationEventCreate(ORMModel):
    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)


class CalendarEntry(ORMModel):
    """One upcoming-action row for the calendar feed.

    Sources today: Application.next_action_at (the user-set follow-up). Future
    sources (interview times etc.) live on ApplicationEvent.payload — when we
    standardise that shape we'll fold them in here without breaking callers."""

    application_id: UUID
    when: datetime
    label: str
    status: AppStatus
    job_id: UUID
    job_title: str
    company_name: str | None = None


class CalendarHistoryEntry(ORMModel):
    """One past status change for the calendar's history view — when an
    application actually moved to applied, rejected, an offer, etc., sourced
    from ApplicationEvent rather than the forward-looking next_action_at."""

    application_id: UUID
    occurred_at: datetime
    status: AppStatus
    job_id: UUID
    job_title: str
    company_name: str | None = None
