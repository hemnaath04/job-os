from sqlalchemy import String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from job_os.db.models._mixins import UUIDPK, Timestamped
from job_os.db.session import Base


class User(UUIDPK, Timestamped, Base):
    __tablename__ = "users"

    clerk_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String, nullable=True)
    settings: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
