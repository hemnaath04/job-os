"""Outreach: who to contact about an application, what to say, and what was sent.

Three endpoints do real work and the rest are bookkeeping.

`POST /contacts` is the provider write path. The user pastes a name and an
address they found themselves, `ManualContactProvider` normalises and validates
it, and the row records that the USER is the source of that address. No third
party is involved and none is required for the feature to work.

`POST /contacts/{id}/draft` runs the drafting agent. It refuses before spending a
token when the person is marked do-not-contact or when the double-message guard
says a message went too recently, because both of those are cheaper to answer
from the database than from a model.

`POST /contacts/{id}/sent` is the user telling us the message actually went. It
writes an `application_events` row, which is what every later double-message
check reads. Nothing here sends anything: no mail transport is wired up, and the
send remains an act the user performs in their own client.
"""
from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from job_os.auth import get_current_user
from job_os.db.models import (
    Application,
    ApplicationEvent,
    Job,
    OutreachContact,
    User,
)
from job_os.db.session import get_session
from job_os.schemas.outreach import (
    FollowUpRow,
    OutreachContactCreate,
    OutreachContactPatch,
    OutreachContactRead,
    OutreachDraftRequest,
    OutreachDraftResponse,
    OutreachHistoryRow,
    OutreachSendLog,
    OutreachStatus,
    ProvenanceRow,
    SharedContextRow,
)
from job_os.services.contact_providers import ContactProviderError, manual_provider
from job_os.services.outreach import (
    EVENT_DRAFTED,
    EVENT_SENT,
    WORD_CAPS,
    OutreachDraftRejected,
    OutreachTarget,
    double_message_block,
    load_verified_vault,
    prior_contacts,
    run_outreach_draft,
)

router = APIRouter(prefix="/outreach")

# A logged send keeps a copy of what went out so the next draft can avoid
# repeating an opener the person has already read. Capped because this lands in a
# JSONB event payload and the whole point of these messages is that they are
# short: anything past this is a pasted resume, not an outreach note.
_STORED_BODY_CHARS = 4000


@router.get(
    "/applications/{application_id}/contacts",
    response_model=list[OutreachContactRead],
)
async def list_contacts(
    application_id: UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[OutreachContactRead]:
    """Everyone recorded for this application, with their send counts."""
    app = await _load_application(session, application_id, user)
    contacts = list(
        (
            await session.execute(
                select(OutreachContact)
                .where(OutreachContact.application_id == app.id)
                .order_by(OutreachContact.created_at)
            )
        )
        .scalars()
        .all()
    )
    events = await _sent_events(session, app.id)
    return [_contact_read(contact, events) for contact in contacts]


@router.post(
    "/applications/{application_id}/contacts",
    response_model=OutreachContactRead,
    status_code=201,
)
async def add_contact(
    application_id: UUID,
    payload: OutreachContactCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> OutreachContactRead:
    """Store one person the user found themselves.

    The provider decides the shape of what gets stored, not this handler. That
    is the seam a real enrichment vendor slots into later: a Hunter result would
    arrive as the same `ContactCandidate` and be written by the same lines, with
    `email_source` saying which of the two it was.
    """
    app = await _load_application(session, application_id, user)
    try:
        candidate = manual_provider().accept(
            full_name=payload.full_name,
            title=payload.title,
            company_name=payload.company_name
            or (app.job.company.name if app.job.company else None),
            email=payload.email,
            linkedin_url=payload.linkedin_url,
            evidence_url=payload.evidence_url,
            relationship=payload.relationship_kind,
            shared_school=payload.shared_school,
            shared_employer=payload.shared_employer,
            referred_by=payload.referred_by,
        )
    except ContactProviderError as error:
        raise HTTPException(422, str(error)) from error

    contact = OutreachContact(
        user_id=user.id,
        application_id=app.id,
        full_name=candidate.full_name,
        identity_key=candidate.identity_key,
        title=candidate.title,
        company_name=candidate.company_name,
        email=candidate.email,
        email_source=candidate.email_source.value if candidate.email_source else None,
        confidence=candidate.confidence,
        linkedin_url=candidate.linkedin_url,
        evidence_url=candidate.evidence_url,
        relationship_kind=candidate.relationship.value,
        provider=candidate.source,
        shared_school=candidate.shared_school,
        shared_employer=candidate.shared_employer,
        referred_by=candidate.referred_by,
        notes=payload.notes,
    )
    session.add(contact)
    try:
        await session.flush()
    except IntegrityError as error:
        # The per-application uniqueness constraint. Answered as a conflict with
        # a readable reason rather than a 500, because adding someone twice is an
        # ordinary mistake: the user did not spot them in the list.
        await session.rollback()
        raise HTTPException(
            409,
            f"{candidate.full_name} is already recorded on this application.",
        ) from error
    return _contact_read(contact, [])


@router.patch("/contacts/{contact_id}", response_model=OutreachContactRead)
async def patch_contact(
    contact_id: UUID,
    payload: OutreachContactPatch,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> OutreachContactRead:
    """Correct a stored contact, or mark them do-not-contact."""
    contact = await _load_contact(session, contact_id, user)
    updates = payload.model_dump(exclude_unset=True)

    # Anything that changes who this person is goes back through the provider,
    # so a corrected address is validated exactly as a pasted one was and the
    # identity key stays in step with it.
    identity_fields = {"full_name", "email", "linkedin_url"}
    if identity_fields & updates.keys():
        try:
            candidate = manual_provider().accept(
                full_name=updates.get("full_name", contact.full_name),
                email=updates.get("email", contact.email),
                linkedin_url=updates.get("linkedin_url", contact.linkedin_url),
            )
        except ContactProviderError as error:
            raise HTTPException(422, str(error)) from error
        contact.full_name = candidate.full_name
        contact.email = candidate.email
        contact.linkedin_url = candidate.linkedin_url
        contact.identity_key = candidate.identity_key
        # The address changed, so the old source no longer describes it. It is
        # still the user who found this one.
        contact.email_source = (
            candidate.email_source.value if candidate.email_source else None
        )
        for field in identity_fields:
            updates.pop(field, None)

    for key, value in updates.items():
        setattr(contact, key, value.value if key == "relationship_kind" and value else value)

    events = await _sent_events(session, contact.application_id)
    return _contact_read(contact, events)


@router.delete("/contacts/{contact_id}", status_code=204)
async def delete_contact(
    contact_id: UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Remove a contact.

    The `application_events` rows survive, on purpose. Deleting the person does
    not un-send the message, and a history that quietly loses a send is a history
    that lets the user message someone twice.
    """
    contact = await _load_contact(session, contact_id, user)
    await session.delete(contact)


@router.get("/contacts/{contact_id}/status", response_model=OutreachStatus)
async def contact_status(
    contact_id: UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> OutreachStatus:
    """Whether a message to this person should happen right now, and why not."""
    contact = await _load_contact(session, contact_id, user)
    prior = await _prior_for(session, contact)
    blocked = _blocked_reason(contact, prior)
    return OutreachStatus(
        can_draft=blocked is None,
        blocked_reason=blocked,
        messages_sent=len(prior),
    )


@router.post("/contacts/{contact_id}/draft", response_model=OutreachDraftResponse)
async def draft_message(
    contact_id: UUID,
    payload: OutreachDraftRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> OutreachDraftResponse:
    """Draft one checked message to this person about this application."""
    contact = await _load_contact(session, contact_id, user)
    app = await _load_application(session, contact.application_id, user)

    prior = await _prior_for(session, contact)
    blocked = _blocked_reason(contact, prior)
    if blocked:
        # 409 rather than 422: nothing about the request is malformed, the state
        # of the world says not yet.
        raise HTTPException(409, blocked)

    facts, bullets = await load_verified_vault(session, user.id)
    if not facts:
        raise HTTPException(
            409,
            "No verified profile facts yet, so there is nothing the message could "
            "honestly say about you. Import a resume and verify the facts first.",
        )

    job = app.job
    job_payload = {
        "title": job.title,
        "company": job.company.name if job.company else contact.company_name,
        "location": job.location,
        # `jd_clean`, not `jd_raw`: the writer needs what the role asks for, and
        # the raw capture carries navigation chrome and cookie banners that spend
        # the context window on nothing.
        "description": (job.jd_clean or job.jd_raw or "")[:6000],
        "applied_status": app.status.value,
        "applied_at": app.applied_at.date().isoformat() if app.applied_at else None,
    }

    try:
        draft = await run_outreach_draft(
            variant=payload.variant,
            target=OutreachTarget(
                full_name=contact.full_name,
                title=contact.title,
                company_name=contact.company_name
                or (job.company.name if job.company else None),
                relationship=contact.relationship_kind,
                shared_school=contact.shared_school,
                shared_employer=contact.shared_employer,
                referred_by=contact.referred_by,
            ),
            job=job_payload,
            facts=facts,
            bullets=bullets,
            prior=prior,
            user_note=payload.note,
        )
    except OutreachDraftRejected as error:
        # 422 with the flags intact. The user can act on most of them: a refused
        # alumni variant is fixed by recording the school, an unsupported claim by
        # verifying the fact behind it.
        raise HTTPException(422, {"reason": "draft_rejected", "flags": error.flags}) from error

    session.add(
        ApplicationEvent(
            application_id=app.id,
            kind=EVENT_DRAFTED,
            payload={
                "contact_id": str(contact.id),
                "contact_name": contact.full_name,
                "variant": draft.variant.value,
                "subject": draft.subject,
                "word_count": draft.word_count,
                "provenance_rows": len(draft.provenance),
            },
        )
    )
    await session.flush()

    return OutreachDraftResponse(
        contact_id=contact.id,
        variant=draft.variant,
        subject=draft.subject,
        body=draft.body,
        word_count=draft.word_count,
        word_cap=WORD_CAPS[draft.variant],
        provenance=[
            ProvenanceRow.model_validate(row, from_attributes=True)
            for row in draft.provenance
        ],
        shared_context_used=[
            SharedContextRow(id=entry.id, kind=entry.kind, claim=entry.claim)
            for entry in draft.shared_context_used
        ],
        follow_up=FollowUpRow(
            suggested_at=draft.follow_up.suggested_at,
            label=draft.follow_up.label,
            is_final=draft.follow_up.is_final,
        ),
        warnings=draft.warnings,
        note=draft.note,
    )


@router.post("/contacts/{contact_id}/sent", response_model=OutreachHistoryRow, status_code=201)
async def log_sent(
    contact_id: UUID,
    payload: OutreachSendLog,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> OutreachHistoryRow:
    """Record that a message actually went to this person.

    Guarded by the same double-message check as drafting, so logging cannot be
    used to walk around it: if the guard says a message went two days ago, a
    second one two days ago is the thing being prevented.
    """
    contact = await _load_contact(session, contact_id, user)
    prior = await _prior_for(session, contact)
    blocked = _blocked_reason(contact, prior)
    if blocked:
        raise HTTPException(409, blocked)

    occurred_at = datetime.now(UTC)
    event = ApplicationEvent(
        application_id=contact.application_id,
        kind=EVENT_SENT,
        payload={
            "contact_id": str(contact.id),
            "contact_name": contact.full_name,
            "variant": payload.variant.value,
            "channel": payload.channel,
            "subject": payload.subject,
            "body": (payload.body or "")[:_STORED_BODY_CHARS] or None,
        },
        occurred_at=occurred_at,
    )
    session.add(event)
    await session.flush()
    return OutreachHistoryRow(
        kind=event.kind,
        occurred_at=occurred_at,
        contact_id=contact.id,
        contact_name=contact.full_name,
        variant=payload.variant.value,
        channel=payload.channel,
        subject=payload.subject,
    )


@router.get(
    "/applications/{application_id}/history",
    response_model=list[OutreachHistoryRow],
)
async def outreach_history(
    application_id: UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[OutreachHistoryRow]:
    """Every draft and send on this application, newest first."""
    app = await _load_application(session, application_id, user)
    rows = list(
        (
            await session.execute(
                select(ApplicationEvent)
                .where(
                    ApplicationEvent.application_id == app.id,
                    ApplicationEvent.kind.in_((EVENT_DRAFTED, EVENT_SENT)),
                )
                .order_by(ApplicationEvent.occurred_at.desc())
            )
        )
        .scalars()
        .all()
    )
    history: list[OutreachHistoryRow] = []
    for row in rows:
        payload = row.payload or {}
        raw_contact_id = payload.get("contact_id")
        history.append(
            OutreachHistoryRow(
                kind=row.kind,
                occurred_at=row.occurred_at,
                contact_id=UUID(raw_contact_id) if raw_contact_id else None,
                contact_name=payload.get("contact_name"),
                variant=payload.get("variant"),
                channel=payload.get("channel"),
                subject=payload.get("subject"),
            )
        )
    return history


# ---------------------------------------------------------------------------
# Loading and guards.
# ---------------------------------------------------------------------------


def _blocked_reason(contact: OutreachContact, prior: list) -> str | None:
    """Why no message should go to this person now, or None.

    Do-not-contact first, because it is absolute and no waiting period fixes it.
    """
    if contact.do_not_contact:
        return (
            f"{contact.full_name} is marked do not contact. Clear that flag first "
            "if it was set by mistake."
        )
    return double_message_block(prior=prior, now=datetime.now(UTC))


async def _prior_for(session: AsyncSession, contact: OutreachContact) -> list:
    """Messages already sent to this one person, out of the event log."""
    events = await _sent_events(session, contact.application_id)
    return prior_contacts(events, contact_id=str(contact.id))


async def _sent_events(
    session: AsyncSession, application_id: UUID
) -> list[ApplicationEvent]:
    """Every logged send on this application, for any contact."""
    result = await session.execute(
        select(ApplicationEvent).where(
            ApplicationEvent.application_id == application_id,
            ApplicationEvent.kind == EVENT_SENT,
        )
    )
    return list(result.scalars().all())


def _contact_read(
    contact: OutreachContact, sent_events: list[ApplicationEvent]
) -> OutreachContactRead:
    """One contact plus its send count, from events already in hand."""
    prior = prior_contacts(sent_events, contact_id=str(contact.id))
    return OutreachContactRead(
        id=contact.id,
        created_at=contact.created_at,
        updated_at=contact.updated_at,
        application_id=contact.application_id,
        full_name=contact.full_name,
        title=contact.title,
        company_name=contact.company_name,
        email=contact.email,
        email_source=contact.email_source,
        confidence=contact.confidence,
        linkedin_url=contact.linkedin_url,
        evidence_url=contact.evidence_url,
        relationship_kind=contact.relationship_kind,
        provider=contact.provider,
        shared_school=contact.shared_school,
        shared_employer=contact.shared_employer,
        referred_by=contact.referred_by,
        do_not_contact=contact.do_not_contact,
        notes=contact.notes,
        messages_sent=len(prior),
        last_sent_at=max((sent.sent_at for sent in prior), default=None),
    )


async def _load_application(
    session: AsyncSession, application_id: UUID, user: User
) -> Application:
    from sqlalchemy.orm import joinedload

    result = await session.execute(
        select(Application)
        .options(joinedload(Application.job).joinedload(Job.company))  # type: ignore[attr-defined]
        .where(Application.id == application_id, Application.user_id == user.id)
    )
    app = result.unique().scalar_one_or_none()
    if app is None:
        raise HTTPException(404, "application not found")
    return app


async def _load_contact(
    session: AsyncSession, contact_id: UUID, user: User
) -> OutreachContact:
    result = await session.execute(
        select(OutreachContact).where(
            OutreachContact.id == contact_id, OutreachContact.user_id == user.id
        )
    )
    contact = result.scalar_one_or_none()
    if contact is None:
        raise HTTPException(404, "contact not found")
    return contact
