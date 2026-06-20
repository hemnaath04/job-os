"""Upcoming-action feed for the calendar view.

Today the feed is driven by `Application.next_action_at` — the user-set
follow-up date plus its human-readable label. Future sources (e.g. concrete
interview slots stored on `ApplicationEvent.payload`) can be folded in here
without changing the response shape — that's why we return a flat
`CalendarEntry` list rather than nested-by-source.
"""
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from job_os.auth import get_current_user
from job_os.db.models import Application, Job, User
from job_os.db.session import get_session
from job_os.schemas.applications import CalendarEntry

router = APIRouter(prefix="/calendar")


@router.get("/upcoming", response_model=list[CalendarEntry])
async def upcoming(
    *,
    days: int = Query(default=90, ge=1, le=365),
    include_past: int = Query(
        default=14, ge=0, le=180, description="Days of past actions to keep for context."
    ),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[CalendarEntry]:
    now = datetime.now(UTC)
    earliest = now - timedelta(days=include_past)
    latest = now + timedelta(days=days)

    stmt = (
        select(Application)
        .options(joinedload(Application.job).joinedload(Job.company))
        .where(
            Application.user_id == user.id,
            Application.archived.is_(False),
            Application.next_action_at.is_not(None),
            Application.next_action_at >= earliest,
            Application.next_action_at <= latest,
        )
        .order_by(Application.next_action_at.asc())
    )
    result = await session.execute(stmt)
    apps = result.unique().scalars().all()

    entries: list[CalendarEntry] = []
    for a in apps:
        when = a.next_action_at
        if when is None:
            continue  # narrows for the type checker; the SQL filter already enforces this
        entries.append(
            CalendarEntry(
                application_id=a.id,
                when=when,
                label=a.next_action_label or "Follow up",
                status=a.status,
                job_id=a.job.id,
                job_title=a.job.title,
                company_name=a.job.company.name if a.job.company else None,
            )
        )
    return entries
