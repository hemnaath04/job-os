"""The model review alone routinely runs past a minute (see
ResumeRenderResponse's own docstring), well past Heroku's hard 30-second
router timeout -- render-review was getting an H12 and a dead connection on
close to every real call, not occasionally. start/status moves the same work
into a background task so the request that kicks it off returns almost
immediately, and the slow part is polled instead of blocked on.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from job_os.routers import resumes as resumes_module
from job_os.routers.resumes import get_render_review_job, start_render_review_job
from job_os.schemas.resumes import (
    ResumeRenderReviewRequest,
    ResumeRenderReviewResponse,
    ResumeReviewResult,
)


def _fake_review() -> ResumeRenderReviewResponse:
    return ResumeRenderReviewResponse(
        review=ResumeReviewResult(
            score=90, passed=True, page_count=1, text_selectable=True
        ),
        latex_source="\\documentclass{article}",
        pdf_base64="ZmFrZQ==",
    )


@pytest.mark.asyncio
async def test_job_completes_and_status_reports_done(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_render_and_review(payload):
        return _fake_review()

    monkeypatch.setattr(resumes_module, "_render_and_review", fake_render_and_review)

    start = await start_render_review_job(
        ResumeRenderReviewRequest(json_resume={"basics": {}}), _user=None
    )
    await asyncio.sleep(0.05)
    status = await get_render_review_job(start.job_id, _user=None)
    assert status.status == "done"
    assert status.result is not None
    assert status.result.review.passed is True


@pytest.mark.asyncio
async def test_status_is_running_before_the_background_task_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = asyncio.Event()

    async def slow_render_and_review(payload):
        await gate.wait()
        return _fake_review()

    monkeypatch.setattr(resumes_module, "_render_and_review", slow_render_and_review)

    start = await start_render_review_job(
        ResumeRenderReviewRequest(json_resume={"basics": {}}), _user=None
    )
    status = await get_render_review_job(start.job_id, _user=None)
    assert status.status == "running"
    gate.set()
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_a_failed_review_reports_as_an_error_status(monkeypatch: pytest.MonkeyPatch) -> None:
    async def failing_render_and_review(payload):
        raise HTTPException(422, "could not compile the document")

    monkeypatch.setattr(resumes_module, "_render_and_review", failing_render_and_review)

    start = await start_render_review_job(
        ResumeRenderReviewRequest(json_resume={"basics": {}}), _user=None
    )
    await asyncio.sleep(0.05)
    status = await get_render_review_job(start.job_id, _user=None)
    assert status.status == "error"
    assert status.error == "could not compile the document"


@pytest.mark.asyncio
async def test_status_404s_on_an_unknown_job(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(HTTPException) as exc_info:
        await get_render_review_job("no-such-job", _user=None)
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_a_finished_job_is_cleared_on_the_read_that_reports_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_render_and_review(payload):
        return _fake_review()

    monkeypatch.setattr(resumes_module, "_render_and_review", fake_render_and_review)

    start = await start_render_review_job(
        ResumeRenderReviewRequest(json_resume={"basics": {}}), _user=None
    )
    await asyncio.sleep(0.05)
    await get_render_review_job(start.job_id, _user=None)

    with pytest.raises(HTTPException) as exc_info:
        await get_render_review_job(start.job_id, _user=None)
    assert exc_info.value.status_code == 404
