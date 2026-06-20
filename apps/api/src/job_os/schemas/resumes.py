from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from job_os.schemas.common import ORMModel, TimestampedRead

BulletSection = Literal["work", "projects", "volunteer"]


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


# ---- Tailoring agent (M3) ----------------------------------------------------
#
# Contract: every bullet in a tailored ResumeVersion.json_resume must be
# traceable to a `fact_bullets` row. The agent returns *decisions* (which facts
# and bullets to include, with optional light edits); Python assembles the
# final JSON Resume deterministically from those decisions. Unmet JD
# requirements become GapQuestions — never invented bullets.


class SelectedBullet(BaseModel):
    """Agent decision: include this FactBullet, optionally with a light edit."""
    fact_bullet_id: UUID
    rewritten_text: str
    target_section: BulletSection


class GapQuestion(BaseModel):
    """A JD requirement with no matching fact. Surfaced for the user to fill or dismiss."""
    requirement: str
    why_no_match: str
    suggested_fact_ids: list[UUID] = Field(default_factory=list)


class TailorAgentOutput(BaseModel):
    """Schema Claude returns verbatim. Pydantic-validated before assembly."""
    selected_fact_ids: list[UUID] = Field(default_factory=list)
    selected_bullets: list[SelectedBullet] = Field(default_factory=list)
    gap_questions: list[GapQuestion] = Field(default_factory=list)
    summary_objective: str | None = None
    ats_keywords_matched: list[str] = Field(default_factory=list)
    ats_keywords_missing: list[str] = Field(default_factory=list)
    agent_note: str = ""


class ProvenanceEntry(BaseModel):
    """One row per bullet in the tailored json_resume — proves the no-hallucination contract."""
    section: BulletSection
    text: str
    fact_bullet_id: UUID
    fact_id: UUID


class TailorRequest(ORMModel):
    job_id: UUID


class TailorResponse(ResumeVersionRead):
    """Returned after the tailor agent runs. Extends ResumeVersionRead with agent-side metadata."""
    gap_questions: list[GapQuestion] = Field(default_factory=list)
    agent_note: str = ""
