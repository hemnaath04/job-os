from __future__ import annotations

import uuid
from datetime import date

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, Date, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from job_os.db.models._mixins import UUIDPK, Timestamped
from job_os.db.session import Base

EMBEDDING_DIM = 1536


class ProfileFact(UUIDPK, Timestamped, Base):
    """Atomic candidate fact — the only thing the tailoring agent can cite.

    `kind` partitions the resume into resumable sections; `payload` carries
    kind-specific structured data (GPA, courses, URL, technologies, etc.).
    `verified=False` means the agent proposed this via a gap question and the
    user hasn't confirmed it yet — drafts must not appear in generated resumes.
    """

    __tablename__ = "profile_facts"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    org: Mapped[str | None] = mapped_column(String, nullable=True)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    location: Mapped[str | None] = mapped_column(String, nullable=True)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    verified: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    source_url: Mapped[str | None] = mapped_column(String, nullable=True)

    bullets: Mapped[list[FactBullet]] = relationship(
        back_populates="fact",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class FactBullet(UUIDPK, Timestamped, Base):
    """A single resume bullet variant pinned to a fact.

    Every bullet in a generated resume_version MUST reference one of these
    rows by id — that's the no-hallucination contract.
    """

    __tablename__ = "fact_bullets"

    fact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profile_facts.id", ondelete="CASCADE"), nullable=False
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    target_role: Mapped[str | None] = mapped_column(String, nullable=True)
    metric_verified: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)

    fact: Mapped[ProfileFact] = relationship(back_populates="bullets")
