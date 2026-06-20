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
from job_os.schemas.me import MeRead, UserSettings, UserSettingsPatch

router = APIRouter(prefix="/me")


def _to_settings(raw: dict[str, Any] | None) -> UserSettings:
    """Coerce the JSONB blob into the typed schema, filling defaults."""
    return UserSettings.model_validate(raw or {})


@router.get("", response_model=MeRead)
async def get_me(user: User = Depends(get_current_user)) -> MeRead:
    return MeRead(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        settings=_to_settings(user.settings),
    )


@router.get("/settings", response_model=UserSettings)
async def get_settings(user: User = Depends(get_current_user)) -> UserSettings:
    return _to_settings(user.settings)


@router.patch("/settings", response_model=UserSettings)
async def patch_settings(
    payload: UserSettingsPatch,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> UserSettings:
    current = _to_settings(user.settings).model_dump(mode="json")
    updates = payload.model_dump(exclude_unset=True, mode="json")
    current.update(updates)
    # Re-validate the merged dict so we never persist a junk shape.
    merged = UserSettings.model_validate(current)
    user.settings = merged.model_dump(mode="json")
    await session.flush()
    return merged
