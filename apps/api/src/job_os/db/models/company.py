from sqlalchemy import String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from job_os.db.models._mixins import Timestamped, UUIDPK
from job_os.db.session import Base


class Company(UUIDPK, Timestamped, Base):
    """Companies.

    The case-insensitive uniqueness on (lower(name), coalesce(domain, '')) is
    enforced by an expression index defined in the Alembic migration.
    """

    __tablename__ = "companies"

    name: Mapped[str] = mapped_column(String, nullable=False)
    domain: Mapped[str | None] = mapped_column(String, nullable=True)
    linkedin_url: Mapped[str | None] = mapped_column(String, nullable=True)
    industry: Mapped[str | None] = mapped_column(String, nullable=True)
    size_bucket: Mapped[str | None] = mapped_column(String, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    research: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
