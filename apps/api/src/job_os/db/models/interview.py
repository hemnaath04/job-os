from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from job_os.db.models._mixins import UUIDPK, Timestamped
from job_os.db.session import Base


class InterviewPrep(UUIDPK, Timestamped, Base):
    """One generated interview prep pack for one application.

    Rows accumulate rather than being overwritten: a pack generated before the
    resume was tailored and one generated after are different documents, and the
    second is only trustworthy because you can still see the first. Readers ask
    for the newest by `created_at`.

    `readiness_score` is derived by Python from the JD's requirements measured
    against the verified fact vault, and `readiness_report` explains it topic by
    topic. `model_estimate` is the generating model's own guess at the same
    number, kept for context and never used as the grade, exactly as
    `ResumeReviewResult` treats its own. See `interview_prep.readiness`.
    """

    __tablename__ = "interview_preps"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("applications.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True
    )
    # The tailored resume the resume-probe questions were written against, when
    # there was one. Null means the pack was generated from the JD and the vault
    # alone, which is a weaker pack and the UI says so.
    resume_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("resume_versions.id", ondelete="SET NULL"), nullable=True
    )

    readiness_score: Mapped[Decimal | None] = mapped_column(Numeric(4, 1), nullable=True)
    readiness_report: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default="{}"
    )
    model_estimate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="", server_default="")

    questions: Mapped[list[InterviewQuestion]] = relationship(
        back_populates="prep",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="InterviewQuestion.position",
    )


class InterviewQuestion(UUIDPK, Timestamped, Base):
    """One question in a prep pack, with whatever verified evidence answers it.

    `evidence` is the provenance of the scaffold: a list of
    `{fact_id, fact_bullet_id, label, text}` rows, every one of them copied from
    a `verified=True` ProfileFact or one of its bullets. A scaffolded answer with
    an empty `evidence` list is a bug, not a thin answer, and
    `interview_prep._ground_answer` refuses to produce one.

    `gap` is the honest alternative to inventing a story: the question is worth
    preparing for and the vault has nothing that answers it, so the pack says so
    instead of writing a Situation the candidate never lived. `removed_claims`
    records the sentences a guard stripped out of a scaffold, so a dropped claim
    is visible rather than silently missing.
    """

    __tablename__ = "interview_questions"

    prep_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("interview_preps.id", ondelete="CASCADE"), nullable=False
    )
    # technical | behavioral | resume_probe | candidate_ask
    category: Mapped[str] = mapped_column(String, nullable=False)
    # Display order inside a category, assigned by the service so the pack reads
    # the same way every time it is loaded.
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    # The JD requirement or competency this came from, so a reader can see why a
    # question is in the pack at all.
    topic: Mapped[str | None] = mapped_column(String, nullable=True)
    difficulty: Mapped[str] = mapped_column(String, default="core", server_default="core")
    why_asked: Mapped[str] = mapped_column(Text, default="", server_default="")

    scaffold: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default="[]"
    )
    gap: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    gap_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    removed_claims: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")

    # Spaced review. Separate from generation on purpose: nothing above this line
    # changes when a user practises, and nothing below it affects the pack's
    # honesty guarantees.
    flagged: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    confidence: Mapped[str | None] = mapped_column(String, nullable=True)
    times_reviewed: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    last_reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_review_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    prep: Mapped[InterviewPrep] = relationship(back_populates="questions")
