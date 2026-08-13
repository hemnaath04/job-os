"""Schemas for job alerts.

Kept in its own module, and the preferences live on `AlertSubscription` rather
than in `users.settings`, for one practical reason: an alert has to be readable
and writable by a scheduled job and by an unauthenticated unsubscribe request. A
blob on the user row is the wrong shape for both.
"""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from job_os.schemas.common import ORMModel

AlertCadenceName = Literal["immediate", "daily", "weekly"]


class AlertSubscriptionCreate(ORMModel):
    saved_search_id: UUID
    cadence: AlertCadenceName = "daily"
    timezone: str = Field(
        default="UTC",
        max_length=64,
        description='IANA zone name, e.g. "America/New_York". Every hour below is local to it.',
    )
    send_hour_local: int = Field(default=8, ge=0, le=23)
    send_weekday: int = Field(
        default=0, ge=0, le=6, description="Monday is 0, matching date.weekday()."
    )
    quiet_hours_start_local: int = Field(default=22, ge=0, le=23)
    quiet_hours_end_local: int = Field(default=7, ge=0, le=23)

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        """Reject an unresolvable zone at write time.

        The runner falls back to UTC for a zone it cannot load, which is the right
        behaviour there because a bad value must not cost someone their alerts.
        But accepting the bad value in the first place would mean the user asked
        for 08:00 local and silently gets 08:00 UTC, with nothing on screen
        saying so.
        """
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise ValueError(f"unknown timezone {value!r}") from e
        return value


class AlertSubscriptionUpdate(ORMModel):
    cadence: AlertCadenceName | None = None
    timezone: str | None = Field(default=None, max_length=64)
    send_hour_local: int | None = Field(default=None, ge=0, le=23)
    send_weekday: int | None = Field(default=None, ge=0, le=6)
    quiet_hours_start_local: int | None = Field(default=None, ge=0, le=23)
    quiet_hours_end_local: int | None = Field(default=None, ge=0, le=23)
    active: bool | None = None

    @model_validator(mode="after")
    def _not_empty(self) -> "AlertSubscriptionUpdate":
        if not self.model_dump(exclude_none=True):
            raise ValueError("no fields to update")
        return self

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return AlertSubscriptionCreate._known_timezone(value)


class AlertSubscriptionRead(ORMModel):
    id: UUID
    saved_search_id: UUID
    saved_search_name: str = ""
    cadence: AlertCadenceName
    timezone: str
    send_hour_local: int
    send_weekday: int
    quiet_hours_start_local: int
    quiet_hours_end_local: int
    active: bool
    unsubscribed_at: datetime | None = None
    last_sent_at: datetime | None = None
    last_checked_at: datetime | None = None
    last_sent_job_count: int | None = None
    created_at: datetime
    updated_at: datetime


class AlertDigestRead(ORMModel):
    """One past email, for the history panel."""

    id: UUID
    subscription_id: UUID
    status: Literal["sent", "failed", "suppressed_empty"]
    subject: str
    job_count: int
    deduped_count: int
    provider: str | None = None
    error: str | None = None
    created_at: datetime


class AlertPreviewJob(ORMModel):
    title: str
    company: str
    location: str
    url: str
    source_label: str
    salary: str | None = None
    salary_from_posting_text: bool = False
    freshness: str
    freshness_caveat: str | None = None
    is_repost: bool = False


class AlertPreviewResponse(ORMModel):
    """What the next digest would contain. Renders nothing to the user's inbox.

    `would_send` is false when everything found had already been mailed, which is
    the normal state of a healthy alert and not an error.
    """

    would_send: bool
    subject: str | None = None
    jobs: list[AlertPreviewJob] = Field(default_factory=list)
    candidates: int = 0
    deduped_count: int = 0
    repost_count: int = 0
    text_body: str | None = None
    reason: str = ""


class UnsubscribeResult(ORMModel):
    scope: Literal["sub", "all"]
    subscriptions_disabled: int
    already_unsubscribed: bool = False
