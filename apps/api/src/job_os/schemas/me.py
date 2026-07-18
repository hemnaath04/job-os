"""Schemas for the current-user (me) router — profile + settings."""
from typing import Literal
from uuid import UUID

from job_os.schemas.common import ORMModel


class UserSettings(ORMModel):
    """Allowed keys in `User.settings` (the canonical stored shape). Anything not
    in this schema is ignored on write — keeps the JSONB blob from accumulating
    arbitrary client junk.

    NOTE: `apify_api_token` is a secret. It is write-through here but never sent
    back to the client — read responses use `UserSettingsRead`, which masks it.
    """

    theme: Literal["system", "dark", "light"] = "dark"
    default_resume_id: UUID | None = None
    default_function: str | None = None
    default_level: str | None = None
    default_location: str | None = None
    timezone: str | None = None
    weekly_summary_email: bool = False
    apify_api_token: str | None = None


class UserSettingsPatch(ORMModel):
    """Partial update — every field optional; only provided keys are applied.

    Send `apify_api_token: ""` to clear the stored Apify key.
    """

    theme: Literal["system", "dark", "light"] | None = None
    default_resume_id: UUID | None = None
    default_function: str | None = None
    default_level: str | None = None
    default_location: str | None = None
    timezone: str | None = None
    weekly_summary_email: bool | None = None
    apify_api_token: str | None = None


class UserSettingsRead(ORMModel):
    """What the client sees. Mirrors `UserSettings` but omits the raw Apify token,
    exposing only whether one is stored via `apify_configured`."""

    theme: Literal["system", "dark", "light"] = "dark"
    default_resume_id: UUID | None = None
    default_function: str | None = None
    default_level: str | None = None
    default_location: str | None = None
    timezone: str | None = None
    weekly_summary_email: bool = False
    apify_configured: bool = False


class MeRead(ORMModel):
    """The current user's identity + settings — drives the Settings page."""

    id: UUID
    email: str
    display_name: str | None
    settings: UserSettingsRead
