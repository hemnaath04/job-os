"""Covers the upload -> attach -> download round trip for an externally
built resume PDF (job.os never renders these; it stores them verbatim), and
the case that motivated fixing it: an MCP client pushing a LaTeX PDF in and
expecting to get it back out, optionally linked to one application.
"""
from __future__ import annotations

import io
import uuid
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException, UploadFile

from job_os.db.models import Application, AppStatus, Job, Resume, ResumeVersion, User
from job_os.routers.resumes import (
    download_version,
    list_resumes,
    list_versions_by_application,
    upload_version,
)


async def _make_user(session, suffix: str) -> User:
    user = User(clerk_id=f"clerk_{suffix}", email=f"{suffix}@example.com")
    session.add(user)
    await session.flush()
    return user


async def _make_resume(session, user: User) -> Resume:
    resume = Resume(user_id=user.id, name=f"resume-{uuid.uuid4().hex[:8]}")
    session.add(resume)
    await session.flush()
    return resume


async def _make_application(session, user: User) -> Application:
    job = Job(title="Backend Engineer", jd_raw="jd", jd_clean="jd", source="url", source_url=None)
    session.add(job)
    await session.flush()
    application = Application(user_id=user.id, job_id=job.id, status=AppStatus.WISHLIST)
    session.add(application)
    await session.flush()
    return application


def _pdf_upload(name: str = "resume.pdf") -> UploadFile:
    return UploadFile(file=io.BytesIO(b"%PDF-1.4 fake resume bytes"), filename=name)


@pytest.mark.asyncio
async def test_upload_is_immediately_final_and_downloadable(db_session) -> None:
    user = await _make_user(db_session, "upload-final")
    resume = await _make_resume(db_session, user)

    version = await upload_version(
        resume.id, file=_pdf_upload(), note="", application_id=None, user=user, session=db_session
    )
    assert version.status == "final"
    assert version.finalized_at is not None
    assert version.spawned_from_application_id is None
    assert version.source_filename == "resume.pdf"

    # Round-trips through the local-fallback storage path (no R2 configured
    # in tests), which is the bug this fixes: download_version used to only
    # ever look at pdf_bytes and re-render json_resume on a miss, but an
    # upload's json_resume is just the {"uploaded": True, ...} stub.
    response = await download_version(resume.id, version.id, user=user, session=db_session)
    assert response.body == b"%PDF-1.4 fake resume bytes"
    assert response.media_type == "application/pdf"


@pytest.mark.asyncio
async def test_upload_attaches_to_an_application_the_user_owns(db_session) -> None:
    user = await _make_user(db_session, "upload-attach")
    resume = await _make_resume(db_session, user)
    application = await _make_application(db_session, user)

    version = await upload_version(
        resume.id,
        file=_pdf_upload(),
        note="",
        application_id=application.id,
        user=user,
        session=db_session,
    )
    assert version.spawned_from_application_id == application.id

    attached = await list_versions_by_application(
        application.id, user=user, session=db_session
    )
    assert [v.id for v in attached] == [version.id]


@pytest.mark.asyncio
async def test_upload_rejects_another_users_application(db_session) -> None:
    owner = await _make_user(db_session, "app-owner")
    other = await _make_user(db_session, "app-other")
    resume = await _make_resume(db_session, other)
    application = await _make_application(db_session, owner)

    with pytest.raises(HTTPException) as exc_info:
        await upload_version(
            resume.id,
            file=_pdf_upload(),
            note="",
            application_id=application.id,
            user=other,
            session=db_session,
        )
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_list_versions_by_application_404s_for_another_users_application(
    db_session,
) -> None:
    owner = await _make_user(db_session, "list-owner")
    other = await _make_user(db_session, "list-other")
    application = await _make_application(db_session, owner)

    with pytest.raises(HTTPException) as exc_info:
        await list_versions_by_application(application.id, user=other, session=db_session)
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_list_resumes_counts_only_job_tailored_versions(db_session) -> None:
    user = await _make_user(db_session, "tailored-count")
    resume = await _make_resume(db_session, user)
    other_resume = await _make_resume(db_session, user)

    job = Job(title="Backend Engineer", jd_raw="jd", jd_clean="jd", source="url", source_url=None)
    db_session.add(job)
    await db_session.flush()

    db_session.add_all(
        [
            ResumeVersion(resume_id=resume.id, json_resume={}, spawned_from_job_id=job.id),
            ResumeVersion(resume_id=resume.id, json_resume={}, spawned_from_job_id=job.id),
            # Generic, never tailored for any job — should not count.
            ResumeVersion(resume_id=resume.id, json_resume={}),
            # Archived tailored version — should not count either.
            ResumeVersion(
                resume_id=resume.id,
                json_resume={},
                spawned_from_job_id=job.id,
                archived_at=datetime.now(UTC),
            ),
        ]
    )
    await db_session.flush()

    resumes = await list_resumes(user=user, session=db_session)
    by_id = {r.id: r for r in resumes}
    assert by_id[resume.id].tailored_count == 2
    assert by_id[other_resume.id].tailored_count == 0
