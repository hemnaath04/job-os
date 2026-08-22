"""The model pass over the JD, the vault and the tailored resume routinely
runs past Heroku's hard 30-second router timeout -- two real production
calls to `/interview-prep/generate` both died with an H12 at exactly 30.0s,
not occasionally. start/status moves the same work into a background task
so the request that kicks it off returns almost immediately, and the slow
part is polled instead of blocked on. Modelled directly on
test_render_review_job.py.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from job_os.routers import interviews as interviews_module
from job_os.routers.interviews import get_generate_job, start_generate_job
from job_os.schemas.interviews import InterviewPrepGenerateRequest, InterviewPrepRead

_USER = SimpleNamespace(id=uuid4())


def _fake_prep_read() -> InterviewPrepRead:
    now = datetime.now(UTC)
    return InterviewPrepRead(
        id=uuid4(),
        created_at=now,
        updated_at=now,
        application_id=uuid4(),
    )


@pytest.mark.asyncio
async def test_job_completes_and_status_reports_done(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_generate_and_read(payload, user):
        return _fake_prep_read()

    monkeypatch.setattr(interviews_module, "_generate_and_read", fake_generate_and_read)

    start = await start_generate_job(
        InterviewPrepGenerateRequest(application_id=uuid4()), user=_USER
    )
    await asyncio.sleep(0.05)
    status = await get_generate_job(start.job_id, _user=_USER)
    assert status.status == "done"
    assert status.result is not None


@pytest.mark.asyncio
async def test_status_is_running_before_the_background_task_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = asyncio.Event()

    async def slow_generate_and_read(payload, user):
        await gate.wait()
        return _fake_prep_read()

    monkeypatch.setattr(interviews_module, "_generate_and_read", slow_generate_and_read)

    start = await start_generate_job(
        InterviewPrepGenerateRequest(application_id=uuid4()), user=_USER
    )
    status = await get_generate_job(start.job_id, _user=_USER)
    assert status.status == "running"
    gate.set()
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_application_not_found_reports_as_an_error_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def missing_application(payload, user):
        raise LookupError("application not found")

    monkeypatch.setattr(interviews_module, "_generate_and_read", missing_application)

    start = await start_generate_job(
        InterviewPrepGenerateRequest(application_id=uuid4()), user=_USER
    )
    await asyncio.sleep(0.05)
    status = await get_generate_job(start.job_id, _user=_USER)
    assert status.status == "error"
    assert status.error == "application not found"


@pytest.mark.asyncio
async def test_an_unexpected_failure_reports_as_an_error_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def failing_generate_and_read(payload, user):
        raise RuntimeError("the model call blew up")

    monkeypatch.setattr(interviews_module, "_generate_and_read", failing_generate_and_read)

    start = await start_generate_job(
        InterviewPrepGenerateRequest(application_id=uuid4()), user=_USER
    )
    await asyncio.sleep(0.05)
    status = await get_generate_job(start.job_id, _user=_USER)
    assert status.status == "error"
    assert status.error == "the model call blew up"


@pytest.mark.asyncio
async def test_status_404s_on_an_unknown_job() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await get_generate_job("no-such-job", _user=_USER)
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_a_finished_job_is_cleared_on_the_read_that_reports_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_generate_and_read(payload, user):
        return _fake_prep_read()

    monkeypatch.setattr(interviews_module, "_generate_and_read", fake_generate_and_read)

    start = await start_generate_job(
        InterviewPrepGenerateRequest(application_id=uuid4()), user=_USER
    )
    await asyncio.sleep(0.05)
    await get_generate_job(start.job_id, _user=_USER)

    with pytest.raises(HTTPException) as exc_info:
        await get_generate_job(start.job_id, _user=_USER)
    assert exc_info.value.status_code == 404
