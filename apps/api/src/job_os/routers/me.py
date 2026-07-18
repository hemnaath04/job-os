"""Current-user endpoints: profile snapshot + settings.

Settings live in `User.settings` (JSONB). Pydantic validates the accepted
keys on write, so unknown keys are dropped instead of bloating the blob.
"""
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from job_os.auth import get_current_user
from job_os.db.models import User
from job_os.db.session import get_session
from job_os.schemas.me import MeRead, UserSettings, UserSettingsPatch, UserSettingsRead

router = APIRouter(prefix="/me")


def _read_settings(raw: dict[str, Any] | None) -> UserSettingsRead:
    """Coerce the JSONB blob into the client-facing shape, masking the secret."""
    stored = UserSettings.model_validate(raw or {})
    data = stored.model_dump(mode="json", exclude={"apify_api_token"})
    return UserSettingsRead(**data, apify_configured=bool(stored.apify_api_token))


@router.get("", response_model=MeRead)
async def get_me(user: User = Depends(get_current_user)) -> MeRead:
    return MeRead(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        settings=_read_settings(user.settings),
    )


@router.get("/settings", response_model=UserSettingsRead)
async def get_settings(user: User = Depends(get_current_user)) -> UserSettingsRead:
    return _read_settings(user.settings)


@router.patch("/settings", response_model=UserSettingsRead)
async def patch_settings(
    payload: UserSettingsPatch,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> UserSettingsRead:
    current = UserSettings.model_validate(user.settings or {}).model_dump(mode="json")
    updates = payload.model_dump(exclude_unset=True, mode="json")
    # An empty token means "clear the stored key".
    if "apify_api_token" in updates and not (updates["apify_api_token"] or "").strip():
        updates["apify_api_token"] = None
    current.update(updates)
    # Re-validate the merged dict so we never persist a junk shape.
    merged = UserSettings.model_validate(current)
    user.settings = merged.model_dump(mode="json")
    await session.flush()
    return _read_settings(user.settings)
