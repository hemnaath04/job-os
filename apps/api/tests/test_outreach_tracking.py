"""Not messaging the same person twice, through the real router code.

The guard itself is unit tested in `test_outreach_guards.py`. What is tested here
is the wiring, because that is where it would actually break: the router WRITES
an `application_events` payload and a later request READS it back, and if those
two ever disagree about a key name the guard stops firing and nothing fails
loudly. So these tests round trip through the handlers rather than asserting on
the payload shape twice.

`application_events` is reused rather than a parallel outreach log being invented,
so this history shows up on the existing application timeline for free.
"""
from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://job_os:job_os@localhost/job_os"
)

from job_os.db.models import ApplicationEvent, OutreachContact  # noqa: E402
from job_os.routers.outreach import (  # noqa: E402
    _blocked_reason,
    _contact_read,
    contact_status,
    log_sent,
    outreach_history,
)
from job_os.schemas.outreach import OutreachSendLog  # noqa: E402
from job_os.services.outreach import (  # noqa: E402
    EVENT_DRAFTED,
    EVENT_SENT,
    MAX_FOLLOW_UPS,
    OutreachVariant,
)

APPLICATION_ID = uuid.uuid4()
CONTACT_ID = uuid.uuid4()
USER = SimpleNamespace(id=uuid.uuid4())


def _contact(**overrides: Any) -> Any:
    fields = {
        "id": CONTACT_ID,
        "user_id": USER.id,
        "application_id": APPLICATION_ID,
        "created_at": datetime(2026, 8, 1, tzinfo=UTC),
        "updated_at": datetime(2026, 8, 1, tzinfo=UTC),
        "full_name": "Priya Raman",
        "identity_key": "priya@stripe.com",
        "title": "Engineering Manager",
        "company_name": "Stripe",
        "email": "priya@stripe.com",
        "email_source": "user_provided",
        "confidence": None,
        "linkedin_url": None,
        "evidence_url": "https://stripe.com/team",
        "relationship_kind": "hiring_manager",
        "provider": "manual",
        "shared_school": None,
        "shared_employer": None,
        "referred_by": None,
        "do_not_contact": False,
        "notes": None,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> _Result:
        return self

    def unique(self) -> _Result:
        return self

    def all(self) -> list[Any]:
        return self._rows

    def scalar_one_or_none(self) -> Any:
        return self._rows[0] if self._rows else None


class _FakeSession:
    """Answers by entity, and keeps whatever the handler added.

    Deliberately not a mock of `execute`: the handlers pick their own tables and
    a fake that returns the same rows to every query would let a handler read the
    wrong one and still pass.
    """

    def __init__(self, *, contact: Any = None, events: list[Any] | None = None) -> None:
        self.contact = contact
        self.events = list(events or [])
        self.added: list[Any] = []

    async def execute(self, statement: Any) -> _Result:
        entity = statement.column_descriptions[0]["entity"]
        if entity is OutreachContact:
            return _Result([self.contact] if self.contact else [])
        if entity is ApplicationEvent:
            return _Result(list(self.events))
        raise AssertionError(f"unexpected query against {entity}")

    def add(self, row: Any) -> None:
        self.added.append(row)
        # Mirrors the database: once flushed, the row is visible to the next
        # read in the same transaction. Without this the second send in a test
        # would not see the first, which is precisely the bug being guarded.
        if isinstance(row, ApplicationEvent):
            self.events.append(row)

    async def flush(self) -> None:
        return None


def _sent_event(
    *, days_ago: int, contact_id: uuid.UUID = CONTACT_ID, variant: str = "referral_ask"
) -> ApplicationEvent:
    return ApplicationEvent(
        application_id=APPLICATION_ID,
        kind=EVENT_SENT,
        payload={
            "contact_id": str(contact_id),
            "contact_name": "Priya Raman",
            "variant": variant,
            "channel": "email",
            "subject": "backend role on payments",
        },
        occurred_at=datetime.now(UTC) - timedelta(days=days_ago),
    )


# ---------------------------------------------------------------------------
# Logging a send, and reading it back.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_logged_send_is_readable_by_the_guard_that_reads_it() -> None:
    """The round trip. A key name that drifts between writer and reader would
    silently disable double-message prevention, and nothing would fail."""
    session = _FakeSession(contact=_contact())

    await log_sent(
        CONTACT_ID,
        OutreachSendLog(variant=OutreachVariant.REFERRAL_ASK, subject="quick question"),
        user=USER,
        session=session,
    )

    assert len(session.added) == 1
    status = await contact_status(CONTACT_ID, user=USER, session=session)
    assert status.messages_sent == 1
    assert status.can_draft is False
    assert "Wait until" in (status.blocked_reason or "")


@pytest.mark.asyncio
async def test_a_second_send_inside_the_waiting_period_is_refused() -> None:
    session = _FakeSession(contact=_contact(), events=[_sent_event(days_ago=2)])

    with pytest.raises(HTTPException) as raised:
        await log_sent(
            CONTACT_ID,
            OutreachSendLog(variant=OutreachVariant.REFERRAL_ASK),
            user=USER,
            session=session,
        )

    assert raised.value.status_code == 409
    assert "2 day(s) ago" in raised.value.detail
    assert session.added == []


@pytest.mark.asyncio
async def test_a_send_is_allowed_once_the_waiting_period_has_passed() -> None:
    session = _FakeSession(contact=_contact(), events=[_sent_event(days_ago=8)])

    row = await log_sent(
        CONTACT_ID,
        OutreachSendLog(variant=OutreachVariant.POST_APPLICATION_FOLLOWUP),
        user=USER,
        session=session,
    )

    assert row.variant == "post_application_followup"
    assert len(session.added) == 1


@pytest.mark.asyncio
async def test_messages_to_a_different_person_do_not_block_this_one() -> None:
    """The log is per application, so a message sent to the recruiter yesterday
    must not stop a note to an engineer today."""
    someone_else = _sent_event(days_ago=1, contact_id=uuid.uuid4())
    session = _FakeSession(contact=_contact(), events=[someone_else])

    status = await contact_status(CONTACT_ID, user=USER, session=session)

    assert status.messages_sent == 0
    assert status.can_draft is True


@pytest.mark.asyncio
async def test_the_limit_on_total_messages_holds_however_long_the_gap() -> None:
    events = [
        _sent_event(days_ago=40 - index * 12) for index in range(MAX_FOLLOW_UPS + 1)
    ]
    session = _FakeSession(contact=_contact(), events=events)

    with pytest.raises(HTTPException) as raised:
        await log_sent(
            CONTACT_ID,
            OutreachSendLog(variant=OutreachVariant.REFERRAL_ASK),
            user=USER,
            session=session,
        )

    assert raised.value.status_code == 409
    assert "is the limit" in raised.value.detail


@pytest.mark.asyncio
async def test_a_stored_body_is_capped_rather_than_swallowing_a_pasted_resume() -> None:
    session = _FakeSession(contact=_contact())

    await log_sent(
        CONTACT_ID,
        OutreachSendLog(variant=OutreachVariant.REFERRAL_ASK, body="x" * 9000),
        user=USER,
        session=session,
    )

    assert len(session.added[0].payload["body"]) == 4000


# ---------------------------------------------------------------------------
# Do not contact.
# ---------------------------------------------------------------------------


def test_a_do_not_contact_flag_beats_every_waiting_period() -> None:
    reason = _blocked_reason(_contact(do_not_contact=True), [])
    assert reason is not None
    assert "do not contact" in reason


@pytest.mark.asyncio
async def test_nothing_can_be_logged_against_someone_who_asked_to_be_left_alone() -> None:
    session = _FakeSession(contact=_contact(do_not_contact=True))

    with pytest.raises(HTTPException) as raised:
        await log_sent(
            CONTACT_ID,
            OutreachSendLog(variant=OutreachVariant.REFERRAL_ASK),
            user=USER,
            session=session,
        )

    assert raised.value.status_code == 409
    assert session.added == []


# ---------------------------------------------------------------------------
# History, and what the panel reads.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_history_carries_drafts_and_sends_newest_first() -> None:
    now = datetime.now(UTC)
    drafted = ApplicationEvent(
        application_id=APPLICATION_ID,
        kind=EVENT_DRAFTED,
        payload={
            "contact_id": str(CONTACT_ID),
            "contact_name": "Priya Raman",
            "variant": "referral_ask",
            "subject": "quick question",
        },
        occurred_at=now,
    )
    # The history handler loads the application before the events, and the
    # application query is a join this fake does not model, so it is answered
    # with a stand-in row and the assertions are about the events.
    class _AppSession(_FakeSession):
        async def execute(self, statement: Any) -> _Result:
            entity = statement.column_descriptions[0]["entity"]
            if entity is ApplicationEvent:
                return _Result(
                    sorted(self.events, key=lambda event: event.occurred_at, reverse=True)
                )
            return _Result([SimpleNamespace(id=APPLICATION_ID)])

    rows = await outreach_history(
        APPLICATION_ID,
        user=USER,
        session=_AppSession(events=[drafted, _sent_event(days_ago=3)]),
    )

    assert [row.kind for row in rows] == [EVENT_DRAFTED, EVENT_SENT]
    assert rows[0].contact_id == CONTACT_ID
    assert rows[0].contact_name == "Priya Raman"


def test_a_contact_reports_how_many_messages_have_gone_to_them() -> None:
    events = [_sent_event(days_ago=20), _sent_event(days_ago=9)]
    read = _contact_read(_contact(), events)
    assert read.messages_sent == 2
    assert read.last_sent_at is not None
    # The address the user typed is labelled as theirs, never as verified.
    assert read.email_source == "user_provided"
    assert read.confidence is None


def test_a_contact_nobody_has_written_to_reports_nothing() -> None:
    read = _contact_read(_contact(), [])
    assert read.messages_sent == 0
    assert read.last_sent_at is None
