"""Interview prep endpoints.

One prefix rather than routes hung off /applications, because a pack has its own
lifecycle: it is generated for an application, read back on its own, and its
questions are practised individually long after the application row stopped
changing.

Every read is scoped by `user_id` on the prep row itself, not only by the
application it points at. A cross-tenant session leak has already cost this
codebase once, and a filter that depends on a join being right is a filter that
can be got wrong by a later refactor of the join.
"""
import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from job_os.auth import get_current_user
from job_os.db.models import (
    Application,
    InterviewPrep,
    InterviewQuestion,
    Job,
    User,
)
from job_os.db.session import async_session, get_session
from job_os.schemas.interviews import (
    InterviewPrepGenerateRequest,
    InterviewPrepJobStart,
    InterviewPrepJobStatus,
    InterviewPrepRead,
    InterviewPrepSummary,
    InterviewQuestionPatch,
    InterviewQuestionRead,
)
from job_os.services.interview_prep import next_review_at, prep_for_application

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/interview-prep")


@router.get("", response_model=list[InterviewPrepSummary])
async def list_preps(
    *,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[InterviewPrep]:
    """Every pack this user has generated, newest first."""
    result = await session.execute(
        select(InterviewPrep)
        .where(InterviewPrep.user_id == user.id)
        .order_by(InterviewPrep.created_at.desc())
    )
    return list(result.unique().scalars().all())


@router.post("/generate", response_model=InterviewPrepRead, status_code=201)
async def generate(
    payload: InterviewPrepGenerateRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> InterviewPrepRead:
    """Generate a pack for one application, and wait for it.

    Slow: one model call over the JD, the vault and the tailored resume. The
    readiness half runs on rules and is returned even when the model call fails,
    so a 201 with an empty question list and an honest note is a real outcome
    rather than a swallowed error. See `generate_prep`.

    That model call routinely runs past Heroku's hard 30-second router
    timeout -- see `/generate/start`'s own docstring for the numbers that
    made this the wrong default for a browser to call directly. Kept for any
    caller that still wants the blocking form; the web app uses the
    background-job pair below instead.
    """
    try:
        prep, _result = await prep_for_application(
            session,
            user_id=user.id,
            application_id=payload.application_id,
            supplied_facts=payload.verified_facts,
        )
    except LookupError as exc:
        raise HTTPException(404, "application not found") from exc
    return await _read(session, prep.id, user)


# In-process job store, for the same reason resumes.py's render-review pair
# needs one: Heroku's router kills any request still waiting past 30
# seconds, and one real model pass over the JD, the vault and the resume
# routinely runs longer than that -- both real production calls to
# `/generate` failed with an H12 at exactly 30.0s, not occasionally. A single
# dict is safe because this image runs `--workers 1` (see Dockerfile.vercel's
# CMD): there is exactly one process that could ever read or write it.
# Cleared per job on the read that finishes it, so this cannot grow unbounded
# across a long-running dyno.
_PREP_JOBS: dict[str, InterviewPrepJobStatus] = {}


async def _generate_and_read(
    payload: InterviewPrepGenerateRequest, user: User
) -> InterviewPrepRead:
    """The whole slow half of `/generate`, in its own session.

    Split out so the background job below has exactly one call to make and
    exactly one thing to monkeypatch in a test -- the same shape resumes.py's
    `_render_and_review` gives the render-review job it was modelled on.
    """
    async with async_session() as session:
        try:
            prep, _result = await prep_for_application(
                session,
                user_id=user.id,
                application_id=payload.application_id,
                supplied_facts=payload.verified_facts,
            )
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        return await _read(session, prep.id, user)


@router.post("/generate/start", response_model=InterviewPrepJobStart, status_code=202)
async def start_generate_job(
    payload: InterviewPrepGenerateRequest,
    user: User = Depends(get_current_user),
) -> InterviewPrepJobStart:
    """Start generating a pack in the background; poll `/generate/status/{job_id}`.

    Returns almost immediately; the actual wait happens in the poller's own
    loop, as a series of fast, individually-fine requests instead of one
    long one that Heroku's router would kill at 30 seconds regardless of
    what this container or the browser were willing to wait for.

    `_generate_and_read` opens its own session rather than reusing the
    request's: that session is handed back to the pool the moment this
    handler returns, and the real generation keeps running well past that
    point.
    """
    job_id = str(uuid4())
    _PREP_JOBS[job_id] = InterviewPrepJobStatus(status="running")

    async def run() -> None:
        try:
            result = await _generate_and_read(payload, user)
            _PREP_JOBS[job_id] = InterviewPrepJobStatus(status="done", result=result)
        except LookupError:
            _PREP_JOBS[job_id] = InterviewPrepJobStatus(
                status="error", error="application not found"
            )
        except Exception as exc:  # noqa: BLE001 -- a job that dies must reach the poller
            log.exception("interview_prep.generate_job_failed", job_id=job_id)
            _PREP_JOBS[job_id] = InterviewPrepJobStatus(status="error", error=str(exc))

    asyncio.create_task(run())
    return InterviewPrepJobStart(job_id=job_id)


@router.get("/generate/status/{job_id}", response_model=InterviewPrepJobStatus)
async def get_generate_job(
    job_id: str,
    _user: User = Depends(get_current_user),
) -> InterviewPrepJobStatus:
    job = _PREP_JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    if job.status != "running":
        del _PREP_JOBS[job_id]
    return job


@router.get("/latest", response_model=InterviewPrepRead)
async def latest_for_application(
    *,
    application_id: UUID = Query(...),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> InterviewPrepRead:
    """The newest pack for one application, or 404 when none has been generated."""
    result = await session.execute(
        select(InterviewPrep)
        .where(
            InterviewPrep.user_id == user.id,
            InterviewPrep.application_id == application_id,
        )
        .order_by(InterviewPrep.created_at.desc())
        .limit(1)
    )
    prep = result.unique().scalars().first()
    if prep is None:
        raise HTTPException(404, "no interview prep for this application yet")
    return await _read(session, prep.id, user)


@router.get("/review-queue", response_model=list[InterviewQuestionRead])
async def review_queue(
    *,
    limit: int = Query(default=20, ge=1, le=100),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[InterviewQuestion]:
    """Flagged questions that are due, plus flagged ones never practised.

    Ordered by due date with the never-practised ones first, so a session starts
    with the questions carrying no information at all about whether the candidate
    can answer them.
    """
    now = datetime.now(UTC)
    result = await session.execute(
        select(InterviewQuestion)
        .join(InterviewPrep, InterviewQuestion.prep_id == InterviewPrep.id)
        .where(
            InterviewPrep.user_id == user.id,
            InterviewQuestion.flagged.is_(True),
        )
        .where(
            (InterviewQuestion.next_review_at.is_(None))
            | (InterviewQuestion.next_review_at <= now)
        )
        .order_by(
            InterviewQuestion.next_review_at.asc().nulls_first(),
            InterviewQuestion.created_at.asc(),
        )
        .limit(limit)
    )
    return list(result.unique().scalars().all())


@router.get("/{prep_id}", response_model=InterviewPrepRead)
async def get_prep(
    prep_id: UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> InterviewPrepRead:
    return await _read(session, prep_id, user)


@router.delete("/{prep_id}", status_code=204)
async def delete_prep(
    prep_id: UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    prep = await _load(session, prep_id, user)
    await session.delete(prep)


@router.patch("/questions/{question_id}", response_model=InterviewQuestionRead)
async def patch_question(
    question_id: UUID,
    payload: InterviewQuestionPatch,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> InterviewQuestion:
    """Flag a question, or record how a practice attempt went.

    Recording a confidence counts as a practice attempt: it bumps the counter and
    schedules the next showing. Flagging alone does not, because deciding a
    question is worth drilling is not the same as having drilled it.
    """
    result = await session.execute(
        select(InterviewQuestion)
        .join(InterviewPrep, InterviewQuestion.prep_id == InterviewPrep.id)
        .where(
            InterviewQuestion.id == question_id,
            InterviewPrep.user_id == user.id,
        )
    )
    question = result.unique().scalars().first()
    if question is None:
        raise HTTPException(404, "question not found")

    updates = payload.model_dump(exclude_unset=True)
    if "flagged" in updates:
        question.flagged = bool(updates["flagged"])
    if updates.get("confidence") is not None:
        now = datetime.now(UTC)
        question.confidence = updates["confidence"]
        question.times_reviewed += 1
        question.last_reviewed_at = now
        question.next_review_at = next_review_at(updates["confidence"], now=now)
        # Practising a question until it is solid is the way off the drill list.
        # Leaving it flagged forever would mean the queue only ever grows, which
        # is how a review mode stops being opened.
        if updates["confidence"] == "solid":
            question.flagged = False
    return question


async def _load(session: AsyncSession, prep_id: UUID, user: User) -> InterviewPrep:
    result = await session.execute(
        select(InterviewPrep).where(
            InterviewPrep.id == prep_id, InterviewPrep.user_id == user.id
        )
    )
    prep = result.unique().scalars().first()
    if prep is None:
        raise HTTPException(404, "interview prep not found")
    return prep


async def _read(session: AsyncSession, prep_id: UUID, user: User) -> InterviewPrepRead:
    """One pack, with the role it was generated for named on it.

    The title and company are denormalised into the response because every screen
    that shows a pack shows what it is a pack FOR, and making the browser fetch
    the application separately to find out is a second round trip for two
    strings.
    """
    prep = await _load(session, prep_id, user)
    application = (
        (
            await session.execute(
                select(Application)
                .options(joinedload(Application.job).joinedload(Job.company))
                .where(Application.id == prep.application_id)
            )
        )
        .unique()
        .scalar_one_or_none()
    )
    read = InterviewPrepRead.model_validate(prep, from_attributes=True)
    if application is not None:
        read.job_title = application.job.title
        read.company_name = (
            application.job.company.name if application.job.company else None
        )
    return read
