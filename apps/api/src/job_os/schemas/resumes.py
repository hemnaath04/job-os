from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import Field

from job_os.schemas.common import ORMModel, TimestampedRead


class ResumeRead(TimestampedRead):
    name: str
    base_role: str | None = None
    is_master: bool


class ResumeCreate(ORMModel):
    name: str
    base_role: str | None = None
    is_master: bool = False


class ResumeVersionSummary(TimestampedRead):
    resume_id: UUID
    spawned_from_job_id: UUID | None = None
    spawned_from_application_id: UUID | None = None
    ats_score: Decimal | None = None
    approved_by_user: bool
    pdf_r2_key: str | None = None
    docx_r2_key: str | None = None


class ResumeVersionRead(ResumeVersionSummary):
    json_resume: dict[str, Any]
    provenance: list[dict[str, Any]] = Field(default_factory=list)
    ats_report: dict[str, Any] | None = None


class ResumeVersionCreate(ORMModel):
    json_resume: dict[str, Any]
    spawned_from_job_id: UUID | None = None
    spawned_from_application_id: UUID | None = None
    provenance: list[dict[str, Any]] = Field(default_factory=list)
    ats_score: Decimal | None = None
    ats_report: dict[str, Any] | None = None


class ExportRequest(ORMModel):
    format: str = Field(default="pdf", pattern="^(pdf|docx)$")


class ExportResult(ORMModel):
    format: str
    r2_key: str | None
    presigned_url: str | None
    rendered: bool
    note: str | None = None
