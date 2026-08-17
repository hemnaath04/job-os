"""Covers the presign-then-confirm upload path: an MCP client with a local
file too large to inline as base64, and no server of its own for job.os to
fetch from, PUTs straight to storage instead and then confirms.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException

from job_os.db.models import Application, AppStatus, Job, Resume, User
from job_os.integrations import r2
from job_os.routers.resumes import confirm_upload, presign_upload
from job_os.schemas.resumes import ConfirmUploadRequest, PresignUploadRequest


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


@pytest.mark.asyncio
async def test_presign_upload_returns_a_url_scoped_to_this_user_and_resume(
    db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = await _make_user(db_session, "presign")
    resume = await _make_resume(db_session, user)

    async def fake_presign_put(key: str, expires_seconds: int = 900) -> str:
        return f"https://r2.example/{key}?signed=1"

    monkeypatch.setattr(r2, "presign_put", fake_presign_put)

    result = await presign_upload(
        resume.id, PresignUploadRequest(filename="resume.pdf"), user=user, session=db_session
    )
    assert result.key.startswith(f"resumes/{user.id}/{resume.id}/uploaded/")
    assert result.key.endswith(".pdf")
    assert result.upload_url == f"https://r2.example/{result.key}?signed=1"


@pytest.mark.asyncio
async def test_presign_upload_503s_when_r2_is_not_configured(
    db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = await _make_user(db_session, "presign-noconf")
    resume = await _make_resume(db_session, user)

    async def fake_presign_put(key: str, expires_seconds: int = 900) -> None:
        return None

    monkeypatch.setattr(r2, "presign_put", fake_presign_put)

    with pytest.raises(HTTPException) as exc_info:
        await presign_upload(
            resume.id, PresignUploadRequest(filename="resume.pdf"), user=user, session=db_session
        )
    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_confirm_upload_rejects_when_nothing_was_actually_uploaded(
    db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = await _make_user(db_session, "confirm-missing")
    resume = await _make_resume(db_session, user)

    async def fake_exists(key: str) -> bool:
        return False

    monkeypatch.setattr(r2, "exists", fake_exists)

    with pytest.raises(HTTPException) as exc_info:
        await confirm_upload(
            resume.id,
            ConfirmUploadRequest(
                key=f"resumes/{user.id}/{resume.id}/uploaded/x.pdf", filename="resume.pdf"
            ),
            user=user,
            session=db_session,
        )
    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_confirm_upload_rejects_a_key_scoped_to_another_user_or_resume(
    db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = await _make_user(db_session, "confirm-foreign")
    resume = await _make_resume(db_session, user)

    async def fake_exists(key: str) -> bool:
        return True  # would succeed if the ownership check didn't run first

    monkeypatch.setattr(r2, "exists", fake_exists)

    with pytest.raises(HTTPException) as exc_info:
        await confirm_upload(
            resume.id,
            ConfirmUploadRequest(
                key="resumes/someone-else/other-resume/uploaded/x.pdf", filename="resume.pdf"
            ),
            user=user,
            session=db_session,
        )
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_confirm_upload_creates_a_final_version_and_can_attach_to_an_application(
    db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = await _make_user(db_session, "confirm-ok")
    resume = await _make_resume(db_session, user)
    application = await _make_application(db_session, user)

    async def fake_exists(key: str) -> bool:
        return True

    monkeypatch.setattr(r2, "exists", fake_exists)

    key = f"resumes/{user.id}/{resume.id}/uploaded/{uuid.uuid4().hex}.pdf"
    version = await confirm_upload(
        resume.id,
        ConfirmUploadRequest(
            key=key,
            filename="Hemnaath_Balasubramani_DaiceLabs.pdf",
            application_id=application.id,
        ),
        user=user,
        session=db_session,
    )
    assert version.status == "final"
    assert version.finalized_at is not None
    assert version.pdf_r2_key == key
    assert version.source_filename == "Hemnaath_Balasubramani_DaiceLabs.pdf"
    assert version.spawned_from_application_id == application.id
