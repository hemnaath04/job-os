"""Covers the calendar's history view: past status changes (applied,
rejected, ...) plotted by when they actually happened, sourced from
ApplicationEvent rather than the forward-looking next_action_at that
/calendar/upcoming reads.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from job_os.db.models import Application, ApplicationEvent, AppStatus, Job, User
from job_os.routers.calendar import history


async def _make_user(session, suffix: str) -> User:
    user = User(clerk_id=f"clerk_{suffix}", email=f"{suffix}@example.com")
    session.add(user)
    await session.flush()
    return user


async def _make_application(session, user: User, title: str) -> Application:
    job = Job(title=title, jd_raw="jd", jd_clean="jd", source="url", source_url=None)
    session.add(job)
    await session.flush()
    application = Application(user_id=user.id, job_id=job.id, status=AppStatus.WISHLIST)
    session.add(application)
    await session.flush()
    return application


@pytest.mark.asyncio
async def test_history_returns_status_changes_newest_first(db_session) -> None:
    user = await _make_user(db_session, "cal-history")
    app_a = await _make_application(db_session, user, "Backend Engineer")
    app_b = await _make_application(db_session, user, "ML Engineer")

    now = datetime.now(UTC)
    db_session.add_all(
        [
            ApplicationEvent(
                application_id=app_a.id,
                kind="status_change",
                to_status=AppStatus.APPLIED,
                occurred_at=now - timedelta(days=5),
            ),
            ApplicationEvent(
                application_id=app_b.id,
                kind="status_change",
                to_status=AppStatus.REJECTED,
                occurred_at=now - timedelta(days=1),
            ),
            # Not a status_change — should be excluded.
            ApplicationEvent(application_id=app_a.id, kind="created", to_status=AppStatus.WISHLIST),
        ]
    )
    await db_session.flush()

    entries = await history(days=90, user=user, session=db_session)

    assert [e.job_title for e in entries] == ["ML Engineer", "Backend Engineer"]
    assert entries[0].status == AppStatus.REJECTED
    assert entries[1].status == AppStatus.APPLIED


@pytest.mark.asyncio
async def test_history_excludes_events_outside_the_window(db_session) -> None:
    user = await _make_user(db_session, "cal-history-window")
    app = await _make_application(db_session, user, "Old Application")

    db_session.add(
        ApplicationEvent(
            application_id=app.id,
            kind="status_change",
            to_status=AppStatus.REJECTED,
            occurred_at=datetime.now(UTC) - timedelta(days=200),
        )
    )
    await db_session.flush()

    entries = await history(days=90, user=user, session=db_session)
    assert entries == []


@pytest.mark.asyncio
async def test_history_scoped_to_the_requesting_user(db_session) -> None:
    owner = await _make_user(db_session, f"cal-owner-{uuid.uuid4().hex[:6]}")
    other = await _make_user(db_session, f"cal-other-{uuid.uuid4().hex[:6]}")
    app = await _make_application(db_session, owner, "Someone Else's Job")

    db_session.add(
        ApplicationEvent(
            application_id=app.id, kind="status_change", to_status=AppStatus.APPLIED
        )
    )
    await db_session.flush()

    entries = await history(days=90, user=other, session=db_session)
    assert entries == []
