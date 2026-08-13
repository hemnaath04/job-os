"""Current-user endpoints: profile snapshot + settings.

Settings live in `User.settings` (JSONB). Pydantic validates the accepted
keys on write, so unknown keys are dropped instead of bloating the blob.
"""
from types import UnionType
from typing import Any, Union, get_args, get_origin

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from job_os.auth import get_current_user
from job_os.db.models import User
from job_os.db.session import get_session
from job_os.schemas.me import MeRead, UserSettings, UserSettingsPatch

router = APIRouter(prefix="/me")


def _accepts_none(annotation: Any) -> bool:
    return get_origin(annotation) in (Union, UnionType) and type(None) in get_args(annotation)


NULLABLE_SETTING_KEYS = frozenset(
    name for name, field in UserSettings.model_fields.items() if _accepts_none(field.annotation)
)
"""Keys where a stored null is a real value, so an explicit `null` in a PATCH
clears them. That is the only way to unset a work authorization or a pay floor.

For every other key a null means "no opinion" and is dropped instead. Merging it
would hand `UserSettings` a null for a field that has a non-null default, which
is exactly how `{"theme": null}` used to reach validation and raise. Derived from
the schema rather than listed by hand, so a nullable field added later is covered
without anyone remembering to come back here.
"""


def _to_settings(raw: dict[str, Any] | None) -> UserSettings:
    """Coerce the JSONB blob into the typed schema, filling defaults."""
    return UserSettings.model_validate(raw or {})


def _reconcile_locations(merged: dict[str, Any], sent: dict[str, Any]) -> None:
    """Point the old single-location field and the new list at the same answer.

    `UserSettings` only fills a blank side, which is right for a read and not
    enough for a write: a PATCH setting `default_location` while a stored
    `locations` disagrees would be overruled by the list and appear to have done
    nothing. Here the client's intent is known, so whichever of the two it
    actually sent wins, in both directions. When it sends both, both stand.
    """
    sent_location = "default_location" in sent
    sent_list = "locations" in sent
    if sent_location and not sent_list:
        location = (merged.get("default_location") or "").strip()
        merged["locations"] = [location] if location else []
    elif sent_list and not sent_location:
        locations = merged.get("locations") or []
        merged["default_location"] = locations[0] if locations else None


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
    sent = payload.model_dump(exclude_unset=True, mode="json")
    updates = {k: v for k, v in sent.items() if v is not None or k in NULLABLE_SETTING_KEYS}
    current.update(updates)
    _reconcile_locations(current, updates)
    # Re-validate the merged dict so we never persist a junk shape.
    merged = UserSettings.model_validate(current)
    user.settings = merged.model_dump(mode="json")
    await session.flush()
    return merged
