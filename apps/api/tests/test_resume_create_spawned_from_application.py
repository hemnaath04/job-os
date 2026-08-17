"""create_resume accepts spawned_from_application_id, tagging a resume as
belonging to one specific company/job rather than being a general-purpose
source (master, "SWE resume", etc.) -- the field the Resumes page uses to
separate the two instead of listing every company-tailored resume alongside
the real, general-purpose sources.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException

from job_os.db.models import Application, AppStatus, Job, User
from job_os.routers.resumes import create_resume
from job_os.schemas.resumes import ResumeCreate


async def _make_user(session, suffix: str) -> User:
    user = User(clerk_id=f"clerk_{suffix}", email=f"{suffix}@example.com")
    session.add(user)
    await session.flush()
    return user


async def _make_application(session, user: User) -> Application:
    job = Job(title="Backend Engineer", jd_raw="jd", jd_clean="jd", source="url", source_url=None)
    session.add(job)
    await session.flush()
    application = Application(user_id=user.id, job_id=job.id, status=AppStatus.WISHLIST)
    session.add(application)
    await session.flush()
    return application


@pytest.mark.asyncio
async def test_create_resume_tags_it_with_the_owning_application(db_session) -> None:
    user = await _make_user(db_session, "create-tagged")
    application = await _make_application(db_session, user)

    resume = await create_resume(
        ResumeCreate(name="Daice Labs", spawned_from_application_id=application.id),
        user=user,
        session=db_session,
    )
    assert resume.spawned_from_application_id == application.id


@pytest.mark.asyncio
async def test_create_resume_without_application_id_is_a_general_source(db_session) -> None:
    user = await _make_user(db_session, "create-general")

    resume = await create_resume(
        ResumeCreate(name="AI / Backend SWE"), user=user, session=db_session
    )
    assert resume.spawned_from_application_id is None


@pytest.mark.asyncio
async def test_create_resume_rejects_an_application_owned_by_someone_else(db_session) -> None:
    owner = await _make_user(db_session, "create-owner")
    other = await _make_user(db_session, "create-other")
    application = await _make_application(db_session, owner)

    with pytest.raises(HTTPException) as exc_info:
        await create_resume(
            ResumeCreate(name="Daice Labs", spawned_from_application_id=application.id),
            user=other,
            session=db_session,
        )
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_create_resume_404s_on_unknown_application(db_session) -> None:
    user = await _make_user(db_session, "create-unknown")

    with pytest.raises(HTTPException) as exc_info:
        await create_resume(
            ResumeCreate(name="Daice Labs", spawned_from_application_id=uuid.uuid4()),
            user=user,
            session=db_session,
        )
    assert exc_info.value.status_code == 404
