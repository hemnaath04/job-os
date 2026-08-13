from __future__ import annotations

import enum
import uuid

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from job_os.db.models._mixins import UUIDPK, Timestamped
from job_os.db.session import Base


class ContactRelationship(str, enum.Enum):
    """How the user knows, or wants to reach, this person.

    Drives which message variant makes sense: you do not send a referral ask to
    a recruiter, and an alumni opener only exists because of ALUMNI.
    """

    HIRING_MANAGER = "hiring_manager"
    RECRUITER = "recruiter"
    ENGINEER = "engineer"
    ALUMNI = "alumni"
    OTHER = "other"


class EmailSource(str, enum.Enum):
    """Where an address came from, which decides how much to trust it.

    An address the user read off a company page and one a provider inferred from
    a domain pattern are different evidence, and folding them together is how a
    guessed address gets treated as a known one. Stored as a plain string column
    so a new provider does not need a database migration to describe itself.
    """

    # The user found it themselves and typed it in. The only source today.
    USER_PROVIDED = "user_provided"
    # A provider returned it AND said it verified deliverability.
    PROVIDER_VERIFIED = "provider_verified"
    # A provider guessed it from an observed domain pattern. Hunter returns
    # roughly 60 on these, against roughly 95 for one found in a public source.
    PROVIDER_INFERRED = "provider_inferred"


class OutreachContact(UUIDPK, Timestamped, Base):
    """One person the user intends to contact about one application.

    Deliberately per-application rather than a global address book. The same
    human at two companies is two rows, because what the user can honestly say to
    them differs by role, and because the double-message check reads per
    application and per contact.

    `shared_school`, `shared_employer` and `referred_by` are what the USER
    asserts about this person. They are inputs to the shared-context ledger, not
    permission on their own: `services/outreach.py` only allows a "we both
    studied at X" claim when this row names X AND the user's own verified vault
    holds a matching education fact. One side alone is a fabrication risk, which
    is the failure this whole feature exists to avoid.
    """

    __tablename__ = "outreach_contacts"
    __table_args__ = (
        # One row per person per application. Without this, the double-message
        # guard is trivially defeated by adding the same person twice.
        UniqueConstraint(
            "application_id", "identity_key", name="uq_outreach_contacts_app_identity"
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("applications.id", ondelete="CASCADE"),
        nullable=False,
    )

    full_name: Mapped[str] = mapped_column(String, nullable=False)
    # The folded form of the name, or of the email when there is one. Written by
    # the service, never by the client, so the uniqueness constraint above cannot
    # be dodged with different capitalisation.
    identity_key: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    company_name: Mapped[str | None] = mapped_column(String, nullable=True)

    email: Mapped[str | None] = mapped_column(String, nullable=True)
    email_source: Mapped[str | None] = mapped_column(String, nullable=True)
    # 0 to 100, in the provider's own terms. Null when nobody claimed a number,
    # which is the honest answer for an address the user typed in.
    confidence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    linkedin_url: Mapped[str | None] = mapped_column(String, nullable=True)
    # The page the details were read off. Free to keep and it is the only thing
    # that makes an address defensible months later.
    evidence_url: Mapped[str | None] = mapped_column(String, nullable=True)

    relationship_kind: Mapped[str] = mapped_column(
        String, nullable=False, server_default=ContactRelationship.OTHER.value
    )
    # Which implementation produced this row. "manual" is the only one today.
    provider: Mapped[str] = mapped_column(String, nullable=False, server_default="manual")

    shared_school: Mapped[str | None] = mapped_column(String, nullable=True)
    shared_employer: Mapped[str | None] = mapped_column(String, nullable=True)
    referred_by: Mapped[str | None] = mapped_column(String, nullable=True)

    # Set when this person asks not to be contacted again. Blocks drafting and
    # logging outright rather than leaving it to the user to remember.
    do_not_contact: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
