from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from job_os.db.models._mixins import UUIDPK, Timestamped
from job_os.db.session import Base


class SavedSearch(UUIDPK, Timestamped, Base):
    """A named DiscoverySearchRequest the user wants to keep around."""

    __tablename__ = "saved_searches"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_saved_searches_user_name"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    query: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_run_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
