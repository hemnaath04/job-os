"""Importing a resume must answer inside Heroku's 30-second router ceiling.

`POST /api/v1/resumes/import` ran an Anthropic extraction per file inside the
request. One multi-page master resume was enough to exceed the ceiling, and
Heroku then returns 503 to the browser while the dyno keeps working:

    at=error code=H12 desc="Request timeout" method=POST
    path="/api/v1/resumes/import" service=30000ms status=503

The upload had usually succeeded by then, so what the user saw was Heroku's
error page stored as the import's status: "Protected master. 503: <!DOCTYPE
html> ... Application Error". A working import reporting itself as a crash.

Batching made it worse but was never the cause. The batch limit is 30 files
and one file already exceeded the ceiling, which is why the fix is deferral
rather than a smaller batch.
"""

from __future__ import annotations

import pytest

from job_os.db.models import User
from job_os.db.models.resume import Resume, ResumeVersion
from job_os.services import resume_ingest
from job_os.services.resume_ingest import (
    PENDING_RESUME,
    complete_resume_extraction,
    is_pending,
    schedule_resume_extraction,
)


@pytest.fixture
def deferred_session(monkeypatch, db_session):
    """Point the deferred extraction at the test's own session.

    `background_session` does this for jd_ingest; the resume extraction opens
    its own session for the same reason and needs the same treatment.
    """
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _factory():
        yield db_session

    monkeypatch.setattr(resume_ingest, "async_session", _factory)
    return db_session


EXTRACTED = {
    "basics": {"name": "Hemnaath Balasubramani"},
    "work": [{"name": "EPAM", "position": "Software Test Automation Engineer"}],
}


async def _seed(session, *, suffix: str, is_master: bool = False) -> ResumeVersion:
    user = User(clerk_id=f"clerk_{suffix}", email=f"{suffix}@example.com")
    session.add(user)
    await session.flush()
    resume = Resume(user_id=user.id, name="Master" if is_master else suffix, is_master=is_master)
    session.add(resume)
    await session.flush()
    version = ResumeVersion(
        resume_id=resume.id,
        json_resume=PENDING_RESUME,
        pdf_bytes=b"%PDF-1.4 fake",
        source_filename="Master.pdf",
        status="extracting",
    )
    session.add(version)
    await session.flush()
    await session.commit()
    return version


@pytest.mark.asyncio
async def test_a_finished_extraction_fills_the_version_and_clears_the_status(
    db_session, deferred_session, monkeypatch
) -> None:
    version = await _seed(db_session, suffix="deferred-ok")

    async def _extract(_raw: bytes) -> dict:
        return EXTRACTED

    monkeypatch.setattr(
        "job_os.services.profile_extract.extract_json_resume_from_pdf", _extract
    )
    await complete_resume_extraction(version.id)

    await db_session.refresh(version)
    assert version.status == "imported"
    assert version.json_resume == EXTRACTED
    assert is_pending(version.json_resume) is False


@pytest.mark.asyncio
async def test_a_failed_extraction_says_so_instead_of_reading_as_slow(
    db_session, deferred_session, monkeypatch
) -> None:
    """A row left at "extracting" forever is indistinguishable from a slow one.

    The task is not awaited by anyone, so raising into it would lose the
    reason entirely.
    """
    version = await _seed(db_session, suffix="deferred-fail")

    async def _boom(_raw: bytes) -> dict:
        raise ValueError("the model returned nothing usable")

    monkeypatch.setattr("job_os.services.profile_extract.extract_json_resume_from_pdf", _boom)
    await complete_resume_extraction(version.id)

    await db_session.refresh(version)
    assert version.status == "import_failed"
    assert "the model returned nothing usable" in (version.revision_note or "")
    assert version.json_resume == PENDING_RESUME, "a failed read must not invent content"


@pytest.mark.asyncio
async def test_a_deleted_import_does_not_crash_the_task(
    db_session, deferred_session
) -> None:
    """Reachable: the row is committed before the task starts, so a user can
    delete an import while it is still being read."""
    version = await _seed(db_session, suffix="deferred-gone")
    version_id = version.id
    await db_session.delete(version)
    await db_session.commit()

    await complete_resume_extraction(version_id)  # must not raise


@pytest.mark.asyncio
async def test_the_same_version_is_not_extracted_twice_at_once(monkeypatch) -> None:
    """Two tasks reading one resume spend two gateway calls to write one row."""
    started: list[object] = []
    monkeypatch.setattr(
        resume_ingest, "complete_resume_extraction", lambda vid: started.append(vid)
    )
    import uuid

    version_id = uuid.uuid4()
    resume_ingest._INFLIGHT.add(version_id)
    try:
        schedule_resume_extraction(version_id)
        assert started == [], "an in-flight extraction was scheduled again"
    finally:
        resume_ingest._INFLIGHT.discard(version_id)


def test_pending_is_distinguishable_from_extracted() -> None:
    assert is_pending(None) is True
    assert is_pending({}) is True
    assert is_pending(PENDING_RESUME) is True
    assert is_pending(EXTRACTED) is False
