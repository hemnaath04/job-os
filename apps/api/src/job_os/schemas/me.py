"""Schemas for the current-user (me) router — profile + settings."""
from typing import Literal
from uuid import UUID

from job_os.schemas.common import ORMModel


class UserSettings(ORMModel):
    """Allowed keys in `User.settings`. Anything not in this schema is ignored
    on write — keeps the JSONB blob from accumulating arbitrary client junk."""

    theme: Literal["system", "dark", "light"] = "dark"
    default_resume_id: UUID | None = None
    default_function: str | None = None
    default_level: str | None = None
    default_location: str | None = None
    timezone: str | None = None
    weekly_summary_email: bool = False


class UserSettingsPatch(ORMModel):
    """Partial update — every field optional; only provided keys are applied."""

    theme: Literal["system", "dark", "light"] | None = None
    default_resume_id: UUID | None = None
    default_function: str | None = None
    default_level: str | None = None
    default_location: str | None = None
    timezone: str | None = None
    weekly_summary_email: bool | None = None


class MeRead(ORMModel):
    """The current user's identity + settings — drives the Settings page."""

    id: UUID
    email: str
    display_name: str | None
    settings: UserSettings
