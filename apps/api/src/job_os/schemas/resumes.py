from datetime import datetime
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
    source_kind: str | None = None
    source_label: str | None = None
    archived_at: datetime | None = None


class ResumeCreate(ORMModel):
    name: str
    base_role: str | None = None
    is_master: bool = False
    source_kind: str | None = None
    source_label: str | None = None


class ResumePatch(ORMModel):
    name: str | None = None
    base_role: str | None = None


class ResumeVersionSummary(TimestampedRead):
    resume_id: UUID
    spawned_from_job_id: UUID | None = None
    spawned_from_application_id: UUID | None = None
    ats_score: Decimal | None = None
    approved_by_user: bool
    pdf_r2_key: str | None = None
    docx_r2_key: str | None = None
    status: str = "draft"
    review_score: Decimal | None = None
    review_report: dict[str, Any] | None = None
    parent_version_id: UUID | None = None
    source_filename: str | None = None
    revision_note: str | None = None
    finalized_at: datetime | None = None
    archived_at: datetime | None = None


class ResumeVersionRead(ResumeVersionSummary):
    json_resume: dict[str, Any]
    provenance: list[dict[str, Any]] = Field(default_factory=list)
    ats_report: dict[str, Any] | None = None
    latex_source: str | None = None


class ResumeVersionCreate(ORMModel):
    json_resume: dict[str, Any]
    spawned_from_job_id: UUID | None = None
    spawned_from_application_id: UUID | None = None
    provenance: list[dict[str, Any]] = Field(default_factory=list)
    ats_score: Decimal | None = None
    ats_report: dict[str, Any] | None = None
    parent_version_id: UUID | None = None
    source_filename: str | None = None
    revision_note: str | None = None


class ResumeDirectEditRequest(ORMModel):
    json_resume: dict[str, Any]
    note: str = "Manual edit"


class ResumePreviewRequest(ORMModel):
    json_resume: dict[str, Any]


class ResumeReviewIssue(BaseModel):
    severity: Literal["blocking", "warning", "suggestion"]
    code: str
    message: str
    section: str | None = None


class ResumeReviewResult(BaseModel):
    score: Decimal
    passed: bool
    page_count: int
    text_selectable: bool
    issues: list[ResumeReviewIssue] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    github_projects_checked: list[str] = Field(default_factory=list)
    model_summary: str = ""


class ResumeRenderReviewRequest(ORMModel):
    json_resume: dict[str, Any]
    # A stored template's look. Omit both to use the bundled default. Rendered
    # in a Jinja sandbox, since a template may have been written by a model.
    html_source: str | None = None
    css_source: str | None = None


class ResumeRenderReviewResponse(BaseModel):
    """Review of a document this service never stores, plus the PDF it rendered.

    Lets a caller that cannot render PDFs itself (the Appwrite agent function,
    whose runtime has no pango or cairo) obtain a real review and a real PDF and
    persist them in its own store.
    """

    review: ResumeReviewResult
    latex_source: str
    pdf_base64: str


class ResumeChatRequest(ORMModel):
    message: str = Field(min_length=2, max_length=4000)
    apply: bool = True


class ResumeChatResponse(BaseModel):
    message: str
    suggestions: list[str] = Field(default_factory=list)
    proposal_id: UUID | None = None
    proposed_json_resume: dict[str, Any] | None = None
    version: ResumeVersionRead | None = None
    review: ResumeReviewResult | None = None


class ResumeImportItem(BaseModel):
    filename: str
    resume_id: UUID | None = None
    version_id: UUID | None = None
    imported: bool
    is_master: bool = False
    note: str = ""


class ResumeImportResult(BaseModel):
    items: list[ResumeImportItem] = Field(default_factory=list)


class RevisionMessageRead(TimestampedRead):
    resume_version_id: UUID
    role: str
    content: str
    suggestions: list[str] = Field(default_factory=list)
    proposed_json_resume: dict[str, Any] | None = None
    applied: bool


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


# Fact and bullet ids are strings, not UUIDs, across the tailoring contract.
# Postgres mints real UUIDs, but the Appwrite workspace mints Appwrite ids
# (`ID.unique()`, a 20-char token), so a UUID-typed field would reject every
# fact the user adds from the browser. Strings accept both, and the frontend
# has always treated these as opaque strings.
class SelectedBullet(BaseModel):
    """Agent decision: include this FactBullet, optionally with a light edit."""
    fact_bullet_id: str
    rewritten_text: str
    target_section: BulletSection


class GapQuestion(BaseModel):
    """A JD requirement with no matching fact. Surfaced for the user to fill or dismiss."""
    requirement: str
    why_no_match: str
    suggested_fact_ids: list[str] = Field(default_factory=list)


class TailorAgentOutput(BaseModel):
    """Schema Claude returns verbatim. Pydantic-validated before assembly."""
    selected_fact_ids: list[str] = Field(default_factory=list)
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
    fact_bullet_id: str
    fact_id: str


class TailorRequest(ORMModel):
    job_id: UUID


class TailorResponse(ResumeVersionRead):
    """Returned after the tailor agent runs. Extends ResumeVersionRead with agent-side metadata."""
    gap_questions: list[GapQuestion] = Field(default_factory=list)
    agent_note: str = ""
