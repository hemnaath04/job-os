from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from job_os.db.models._mixins import UUIDPK, Timestamped
from job_os.db.session import Base
from job_os.services.job_identity import canonical_url

if TYPE_CHECKING:
    from job_os.db.models.company import Company

EMBEDDING_DIM = 1536  # text-embedding-3-large truncated via Matryoshka (HNSW caps at 2000)


class Job(UUIDPK, Timestamped, Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("source", "source_id", name="uq_jobs_source_pair"),
        Index("ix_jobs_source_url_key", "source_url_key"),
    )

    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="SET NULL"), nullable=True
    )

    title: Mapped[str] = mapped_column(String, nullable=False)
    level: Mapped[str | None] = mapped_column(String, nullable=True)
    function: Mapped[str | None] = mapped_column(String, nullable=True)
    location: Mapped[str | None] = mapped_column(String, nullable=True)
    remote: Mapped[str | None] = mapped_column(String, nullable=True)

    salary_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    salary_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    salary_currency: Mapped[str] = mapped_column(String(3), default="USD", server_default="USD")

    jd_raw: Mapped[str] = mapped_column(Text, nullable=False)
    jd_clean: Mapped[str] = mapped_column(Text, nullable=False)
    jd_embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    jd_parsed: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")

    source: Mapped[str] = mapped_column(String, nullable=False)
    source_id: Mapped[str | None] = mapped_column(String, nullable=True)
    source_url: Mapped[str | None] = mapped_column(String, nullable=True)
    # The comparable form of `source_url`, kept alongside it so a dedup lookup
    # is an indexed equality rather than a scan with normalisation on top.
    #
    # Derived, never set by hand: the validator below recomputes it from
    # whatever `source_url` is assigned, at every one of the fourteen places
    # this app builds a Job. Passing it explicitly is the one way this column
    # could ever disagree with the URL it describes, so there is no path that
    # does.
    source_url_key: Mapped[str | None] = mapped_column(String, nullable=True)

    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closes_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )

    company: Mapped[Company | None] = relationship(lazy="joined")

    @validates("source_url")
    def _derive_source_url_key(self, _key: str, value: str | None) -> str | None:
        self.source_url_key = canonical_url(value)
        return value
