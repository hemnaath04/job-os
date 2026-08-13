"""Wire shapes for outreach contacts, drafts and the send log.

Two things are deliberately absent from the write shapes.

`identity_key` is never accepted from a client. It is what the per-application
uniqueness constraint keys on, so letting a caller set it hands them a way to
store the same person twice and walk past the double-message guard.

`email_source` and `confidence` are never accepted either. They say how much to
trust an address, and a client that can set them can label a guess as verified.
The manual provider sets both, and it sets confidence to nothing, because nobody
measured anything.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field

from job_os.db.models.outreach import ContactRelationship
from job_os.schemas.common import ORMModel, TimestampedRead
from job_os.services.outreach import OutreachVariant


class OutreachContactCreate(ORMModel):
    """One person the user found themselves and pasted in.

    Everything except the name is optional, because the user genuinely may only
    have a name and a LinkedIn URL at this point. A contact with no address is
    still worth storing: the draft can be written and sent through LinkedIn, and
    the tracking works the same either way.
    """

    full_name: str
    title: str | None = None
    company_name: str | None = None
    # A plain str rather than EmailStr, so the one place that decides whether an
    # address is usable is `ManualContactProvider.accept`. Two validators with
    # slightly different opinions is how a value gets rejected by one layer and
    # accepted by the next.
    email: str | None = None
    linkedin_url: str | None = None
    evidence_url: str | None = None
    relationship_kind: ContactRelationship = ContactRelationship.OTHER
    # What the user asserts they share with this person. Asserting it here does
    # NOT license the draft to say it. `services.outreach.shared_context`
    # intersects these with the verified vault first, and an assertion with no
    # matching verified fact produces nothing the message may claim.
    shared_school: str | None = None
    shared_employer: str | None = None
    referred_by: str | None = None
    notes: str | None = None


class OutreachContactPatch(ORMModel):
    """Corrections after the fact, including the one that matters most.

    `do_not_contact` is on this shape so that a person asking to be left alone
    can be recorded in one click. It blocks drafting and logging outright rather
    than relying on the user to remember.
    """

    full_name: str | None = None
    title: str | None = None
    company_name: str | None = None
    email: str | None = None
    linkedin_url: str | None = None
    evidence_url: str | None = None
    relationship_kind: ContactRelationship | None = None
    shared_school: str | None = None
    shared_employer: str | None = None
    referred_by: str | None = None
    do_not_contact: bool | None = None
    notes: str | None = None


class OutreachContactRead(TimestampedRead):
    application_id: UUID
    full_name: str
    title: str | None
    company_name: str | None
    email: str | None
    #: Where the address came from, in `EmailSource` terms. Shown rather than
    #: hidden: an address a provider inferred from a domain pattern and one the
    #: user read off a page are different bets, and the user gets to make it.
    email_source: str | None
    confidence: int | None
    linkedin_url: str | None
    evidence_url: str | None
    relationship_kind: str
    provider: str
    shared_school: str | None
    shared_employer: str | None
    referred_by: str | None
    do_not_contact: bool
    notes: str | None
    #: How many messages have already gone to this person, from the event log.
    #: The panel needs it to say "2 sent" without a second round trip.
    messages_sent: int = 0
    last_sent_at: datetime | None = None


class ProvenanceRow(ORMModel):
    """One phrase in the message, and the verified row that backs it."""

    phrase: str
    evidence_kind: str
    evidence_id: str
    evidence_text: str


class SharedContextRow(ORMModel):
    """One thing the message was allowed to claim they have in common."""

    id: str
    kind: str
    claim: str


class FollowUpRow(ORMModel):
    suggested_at: datetime | None
    label: str
    is_final: bool


class OutreachDraftRequest(ORMModel):
    variant: OutreachVariant
    #: Free text from the user, passed to the writer as context and never as
    #: evidence. It cannot be cited and it cannot license a claim.
    note: str | None = None


class OutreachDraftResponse(ORMModel):
    """A drafted message that already passed every check.

    There is no "draft with warnings about fabricated content" state. A message
    that fails a guard raises, because the way a bad draft gets sent is by being
    shown to someone in a hurry. `warnings` here covers things the user should
    know and can act on, like a sentence having been cut, not unresolved doubts
    about whether the content is true.
    """

    contact_id: UUID
    variant: OutreachVariant
    subject: str
    body: str
    word_count: int
    word_cap: int
    provenance: list[ProvenanceRow]
    shared_context_used: list[SharedContextRow]
    follow_up: FollowUpRow
    warnings: list[str] = Field(default_factory=list)
    note: str = ""


class OutreachSendLog(ORMModel):
    """The user telling us they actually sent it.

    Separate from drafting on purpose. Drafting is free and repeatable; sending
    is the thing that cannot be undone and the only thing the double-message
    guard counts. Nothing in this codebase sends anything, so this endpoint is
    how a send becomes a fact.
    """

    variant: OutreachVariant
    channel: str = "email"
    subject: str | None = None
    #: Kept so the next draft can avoid repeating an opener the person has
    #: already read. Truncated by the router before it is stored.
    body: str | None = None


class OutreachHistoryRow(ORMModel):
    """One line of what happened, read back from `application_events`."""

    kind: str
    occurred_at: datetime
    contact_id: UUID | None
    contact_name: str | None
    variant: str | None
    channel: str | None
    subject: str | None


class OutreachStatus(ORMModel):
    """Whether a message to this person should happen at all right now.

    `blocked_reason` is the double-message guard talking. It is returned rather
    than raised on the read path so the panel can grey the button out and say
    why, instead of letting the user press it and get an error.
    """

    can_draft: bool
    blocked_reason: str | None = None
    messages_sent: int = 0
