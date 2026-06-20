from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from job_os.auth import get_current_user
from job_os.db.models import (
    Application,
    ApplicationEvent,
    AppStatus,
    Job,
    User,
)
from job_os.db.session import get_session
from job_os.schemas.applications import (
    ApplicationCreate,
    ApplicationEventCreate,
    ApplicationEventRead,
    ApplicationPatch,
    ApplicationRead,
)

router = APIRouter(prefix="/applications")


@router.get("", response_model=list[ApplicationRead])
async def list_applications(
    *,
    status_filter: AppStatus | None = Query(default=None, alias="status"),
    q: str | None = None,
    archived: bool = False,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[Application]:
    stmt = (
        select(Application)
        .options(joinedload(Application.job).joinedload(Job.company))  # type: ignore[attr-defined]
        .where(Application.user_id == user.id, Application.archived == archived)
    )
    if status_filter:
        stmt = stmt.where(Application.status == status_filter)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.join(Job).where(Job.title.ilike(like))
    stmt = stmt.order_by(Application.updated_at.desc())
    result = await session.execute(stmt)
    return list(result.unique().scalars().all())


@router.post("", response_model=ApplicationRead, status_code=201)
async def create_application(
    payload: ApplicationCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Application:
    job = await session.get(Job, payload.job_id)
    if job is None:
        raise HTTPException(404, "job not found")

    existing = await session.execute(
        select(Application).where(
            Application.user_id == user.id, Application.job_id == payload.job_id
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(409, "application already exists for this job")

    app = Application(
        user_id=user.id,
        job_id=payload.job_id,
        status=payload.status,
        notes=payload.notes,
        applied_at=datetime.now(UTC) if payload.status == AppStatus.APPLIED else None,
    )
    session.add(app)
    await session.flush()

    session.add(
        ApplicationEvent(
            application_id=app.id,
            kind="created",
            to_status=app.status,
        )
    )
    await session.flush()
    return await _load(session, app.id, user)


@router.get("/{application_id}", response_model=ApplicationRead)
async def get_application(
    application_id: UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Application:
    app = await _load(session, application_id, user)
    return app


@router.patch("/{application_id}", response_model=ApplicationRead)
async def patch_application(
    application_id: UUID,
    payload: ApplicationPatch,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Application:
    app = await _load(session, application_id, user)
    updates = payload.model_dump(exclude_unset=True)

    prev_status = app.status
    new_status = updates.get("status")
    if new_status and new_status != prev_status:
        if new_status == AppStatus.APPLIED and app.applied_at is None:
            app.applied_at = datetime.now(UTC)
        session.add(
            ApplicationEvent(
                application_id=app.id,
                kind="status_change",
                from_status=prev_status,
                to_status=new_status,
            )
        )

    for key, value in updates.items():
        setattr(app, key, value)

    return app


@router.delete("/{application_id}", status_code=204)
async def archive_application(
    application_id: UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    app = await _load(session, application_id, user)
    app.archived = True
    session.add(
        ApplicationEvent(application_id=app.id, kind="archived", payload={})
    )


@router.post("/{application_id}/events", response_model=ApplicationEventRead, status_code=201)
async def log_event(
    application_id: UUID,
    payload: ApplicationEventCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ApplicationEvent:
    app = await _load(session, application_id, user)
    event = ApplicationEvent(
        application_id=app.id,
        kind=payload.kind,
        payload=payload.payload,
    )
    session.add(event)
    await session.flush()
    return event


@router.get("/{application_id}/timeline", response_model=list[ApplicationEventRead])
async def get_timeline(
    application_id: UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[ApplicationEvent]:
    app = await _load(session, application_id, user)
    result = await session.execute(
        select(ApplicationEvent)
        .where(ApplicationEvent.application_id == app.id)
        .order_by(ApplicationEvent.occurred_at.desc())
    )
    return list(result.scalars().all())


async def _load(session: AsyncSession, application_id: UUID, user: User) -> Application:
    result = await session.execute(
        select(Application)
        .options(joinedload(Application.job).joinedload(Job.company))  # type: ignore[attr-defined]
        .where(Application.id == application_id, Application.user_id == user.id)
    )
    app = result.unique().scalar_one_or_none()
    if app is None:
        raise HTTPException(404, "application not found")
    return app
