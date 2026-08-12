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
from datetime import UTC, datetime
from uuid import UUID

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
from job_os.db.session import get_session
from job_os.schemas.interviews import (
    InterviewPrepGenerateRequest,
    InterviewPrepRead,
    InterviewPrepSummary,
    InterviewQuestionPatch,
    InterviewQuestionRead,
)
from job_os.services.interview_prep import next_review_at, prep_for_application

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
    """Generate a pack for one application.

    Slow: one model call over the JD, the vault and the tailored resume. The
    readiness half runs on rules and is returned even when the model call fails,
    so a 201 with an empty question list and an honest note is a real outcome
    rather than a swallowed error. See `generate_prep`.
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
