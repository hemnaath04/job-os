from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from job_os.db.models._mixins import Timestamped, UUIDPK
from job_os.db.session import Base


class User(UUIDPK, Timestamped, Base):
    __tablename__ = "users"

    clerk_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String, nullable=True)
