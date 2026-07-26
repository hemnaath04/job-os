from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from job_os.db.models._mixins import UUIDPK, Timestamped
from job_os.db.session import Base


class Resume(UUIDPK, Timestamped, Base):
    """A named resume template — Master / SWE / ML / AI / Research / etc."""

    __tablename__ = "resumes"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_resumes_user_name"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    base_role: Mapped[str | None] = mapped_column(String, nullable=True)
    is_master: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    source_kind: Mapped[str | None] = mapped_column(String, nullable=True)
    source_label: Mapped[str | None] = mapped_column(String, nullable=True)


class ResumeVersion(UUIDPK, Timestamped, Base):
    """A specific JSON Resume document — either the master baseline or a
    tailored variant produced by the M3 agent. Every bullet referenced in
    `json_resume` must appear in `provenance` linked to a fact_bullet."""

    __tablename__ = "resume_versions"

    resume_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("resumes.id", ondelete="CASCADE"), nullable=False
    )
    json_resume: Mapped[dict] = mapped_column(JSONB, nullable=False)
    spawned_from_application_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("applications.id", ondelete="SET NULL"),
        nullable=True,
    )
    spawned_from_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True
    )
    provenance: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    ats_score: Mapped[Decimal | None] = mapped_column(Numeric(4, 1), nullable=True)
    ats_report: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    approved_by_user: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    pdf_r2_key: Mapped[str | None] = mapped_column(String, nullable=True)
    docx_r2_key: Mapped[str | None] = mapped_column(String, nullable=True)
    # WeasyPrint render of `json_resume`, cached at tailor-completion time
    # so the per-click download path doesn't re-render every time (and
    # doesn't compound the Vercel proxy timeout on cold starts).
    pdf_bytes: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    status: Mapped[str] = mapped_column(String, default="draft", server_default="draft")
    review_score: Mapped[Decimal | None] = mapped_column(Numeric(4, 1), nullable=True)
    review_report: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    parent_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("resume_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_filename: Mapped[str | None] = mapped_column(String, nullable=True)
    revision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    latex_source: Mapped[str | None] = mapped_column(Text, nullable=True)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ResumeRevisionMessage(UUIDPK, Timestamped, Base):
    """Chat history for one resume revision.

    Messages are attached to the resulting version so every AI edit remains
    auditable even when the user creates several branches from one draft.
    """

    __tablename__ = "resume_revision_messages"

    resume_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("resume_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    suggestions: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    applied: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
