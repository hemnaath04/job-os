from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page[T](ORMModel):
    items: list[T]
    total: int


class TimestampedRead(ORMModel):
    id: UUID
    created_at: datetime
    updated_at: datetime
