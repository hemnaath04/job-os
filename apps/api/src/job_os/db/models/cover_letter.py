from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from job_os.db.models._mixins import UUIDPK, Timestamped
from job_os.db.session import Base


class CoverLetter(UUIDPK, Timestamped, Base):
    """One cover letter, usually for one job, holding every version of itself.

    Deliberately the same two-table shape as Resume / ResumeVersion rather than a
    single mutable row. A letter gets rewritten: the user regenerates it in a
    different tone, edits a paragraph, then wants the earlier wording back. That
    only works if a version is a record rather than an update.
    """

    __tablename__ = "cover_letters"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_cover_letters_user_name"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    # The job this letter is for. Nullable and SET NULL, because a job row can be
    # pruned when a posting closes and losing the posting must not lose the letter
    # the user sent.
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True
    )
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class CoverLetterVersion(UUIDPK, Timestamped, Base):
    """One generated or edited letter.

    Every claim sentence in `document` must appear in `provenance` linked to a
    `fact_bullets` row. That is the same contract `resume_versions` carries, and
    it is enforced in `services/cover_letter.py` at assembly time rather than
    trusted to the model that wrote the prose.
    """

    __tablename__ = "cover_letter_versions"

    cover_letter_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cover_letters.id", ondelete="CASCADE"), nullable=False
    )
    # Every JSONB column below spells out its element type, unlike the bare
    # `Mapped[dict]` on `resume_versions`. Those predate the type checker being
    # run by any automation and are part of its standing backlog, which the lint
    # ratchet only lets move downwards, so a new column may not add to it.
    #
    # The assembled CoverLetterDocument: sender block, greeting, paragraphs,
    # sign-off. Prose rather than a JSON Resume, so it has its own shape, but it
    # is the same idea: the renderable document, built by Python.
    document: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    provenance: Mapped[list[Any]] = mapped_column(JSONB, default=list, server_default="[]")
    gap_questions: Mapped[list[Any]] = mapped_column(
        JSONB, default=list, server_default="[]"
    )
    # Sentences Python refused to print, with the reason. Kept rather than
    # discarded: a user who can see that a claim was dropped for inventing a
    # metric learns something about their own vault, and a silent deletion just
    # looks like a short letter.
    refused: Mapped[list[Any]] = mapped_column(JSONB, default=list, server_default="[]")
    quality_flags: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default="{}"
    )
    tone: Mapped[str] = mapped_column(String, default="plain", server_default="plain")
    # Which resume template this letter was rendered to match, so the two
    # documents look like a set.
    template_key: Mapped[str | None] = mapped_column(String, nullable=True)
    word_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    agent_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    spawned_from_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True
    )
    spawned_from_application_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("applications.id", ondelete="SET NULL"),
        nullable=True,
    )
    parent_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cover_letter_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String, default="draft", server_default="draft")
    approved_by_user: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    # The render, cached at generation time so the download path does not
    # recompile. Same reasoning as `resume_versions.pdf_bytes`.
    pdf_bytes: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    revision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    finalized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
