"""move_version reassigns a version to a different resume the caller owns —
the fix for versions that landed under one shared, generic resume instead of
each getting its own (e.g. several company-tailored uploads reused one
container instead of naming one per company).
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException

from job_os.db.models import Resume, ResumeVersion, User
from job_os.routers.resumes import move_version
from job_os.schemas.resumes import MoveVersionRequest


async def _make_user(session, suffix: str) -> User:
    user = User(clerk_id=f"clerk_{suffix}", email=f"{suffix}@example.com")
    session.add(user)
    await session.flush()
    return user


async def _make_resume(session, user: User, name: str = "Resume") -> Resume:
    resume = Resume(user_id=user.id, name=name)
    session.add(resume)
    await session.flush()
    return resume


async def _make_version(session, resume: Resume) -> ResumeVersion:
    version = ResumeVersion(resume_id=resume.id, json_resume={"uploaded": True})
    session.add(version)
    await session.flush()
    return version


@pytest.mark.asyncio
async def test_move_version_reassigns_resume_id(db_session) -> None:
    user = await _make_user(db_session, "move-ok")
    source = await _make_resume(db_session, user, "AI / Backend SWE")
    target = await _make_resume(db_session, user, "Daice Labs")
    version = await _make_version(db_session, source)

    moved = await move_version(
        version.id,
        MoveVersionRequest(target_resume_id=target.id),
        user=user,
        session=db_session,
    )
    assert moved.resume_id == target.id


@pytest.mark.asyncio
async def test_move_version_rejects_a_target_resume_owned_by_someone_else(db_session) -> None:
    owner = await _make_user(db_session, "move-owner")
    other = await _make_user(db_session, "move-other")
    source = await _make_resume(db_session, owner)
    others_resume = await _make_resume(db_session, other)
    version = await _make_version(db_session, source)

    with pytest.raises(HTTPException) as exc_info:
        await move_version(
            version.id,
            MoveVersionRequest(target_resume_id=others_resume.id),
            user=owner,
            session=db_session,
        )
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_move_version_rejects_a_version_owned_by_someone_else(db_session) -> None:
    owner = await _make_user(db_session, "move-vowner")
    other = await _make_user(db_session, "move-vother")
    source = await _make_resume(db_session, owner)
    target = await _make_resume(db_session, other)
    version = await _make_version(db_session, source)

    with pytest.raises(HTTPException) as exc_info:
        await move_version(
            version.id,
            MoveVersionRequest(target_resume_id=target.id),
            user=other,
            session=db_session,
        )
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_move_version_is_a_noop_when_target_is_the_current_resume(db_session) -> None:
    user = await _make_user(db_session, "move-noop")
    resume = await _make_resume(db_session, user)
    version = await _make_version(db_session, resume)

    moved = await move_version(
        version.id,
        MoveVersionRequest(target_resume_id=resume.id),
        user=user,
        session=db_session,
    )
    assert moved.resume_id == resume.id


@pytest.mark.asyncio
async def test_move_version_404s_on_unknown_version(db_session) -> None:
    user = await _make_user(db_session, "move-unknown")
    target = await _make_resume(db_session, user)

    with pytest.raises(HTTPException) as exc_info:
        await move_version(
            uuid.uuid4(),
            MoveVersionRequest(target_resume_id=target.id),
            user=user,
            session=db_session,
        )
    assert exc_info.value.status_code == 404
