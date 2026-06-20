from datetime import date
from typing import Any
from uuid import UUID

from pydantic import Field

from job_os.schemas.common import ORMModel, TimestampedRead


class FactBulletRead(TimestampedRead):
    text: str
    target_role: str | None = None
    metric_verified: bool = True


class FactBulletCreate(ORMModel):
    text: str
    target_role: str | None = None
    metric_verified: bool = True


class ProfileFactRead(TimestampedRead):
    kind: str
    title: str
    org: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    location: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    verified: bool
    source_url: str | None = None
    bullets: list[FactBulletRead] = Field(default_factory=list)


class ProfileFactCreate(ORMModel):
    kind: str
    title: str
    org: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    location: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    verified: bool = False
    source_url: str | None = None
    bullets: list[FactBulletCreate] = Field(default_factory=list)


class ProfileFactPatch(ORMModel):
    title: str | None = None
    org: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    location: str | None = None
    payload: dict[str, Any] | None = None
    verified: bool | None = None
    source_url: str | None = None


class JsonResumeImport(ORMModel):
    json_resume: dict[str, Any] | None = None
    server_path: str | None = Field(
        default=None,
        description="Absolute path on the API host to a JSON Resume file. "
        "Only honored in development mode.",
    )
    mark_verified: bool = True
    replace_existing: bool = False


class ImportReport(ORMModel):
    facts_created: int
    facts_skipped: int
    bullets_created: int
    bullets_embedded: int
    notes: list[str] = Field(default_factory=list)
