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
    spawned_from_application_id: UUID | None = None
    tailored_count: int = 0
    archived_at: datetime | None = None


class ResumeCreate(ORMModel):
    name: str
    base_role: str | None = None
    is_master: bool = False
    source_kind: str | None = None
    source_label: str | None = None
    spawned_from_application_id: UUID | None = None


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


class PresignUploadRequest(ORMModel):
    filename: str


class PresignUploadResponse(ORMModel):
    key: str
    upload_url: str
    expires_in: int


class ConfirmUploadRequest(ORMModel):
    key: str
    filename: str
    note: str = ""
    application_id: UUID | None = None


class MoveVersionRequest(ORMModel):
    target_resume_id: UUID


class ResumeDirectEditRequest(ORMModel):
    json_resume: dict[str, Any]
    note: str = "Manual edit"


class ResumePreviewRequest(ORMModel):
    json_resume: dict[str, Any]
    template_key: str | None = None


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
    # Advisory only. `score` is the authoritative, deterministic, issue-derived
    # number the UI shows. `model_estimate` is the reviewing model's own free-form
    # 0-100 guess, kept for context but never the grade because it is not
    # reproducible run to run. `score_breakdown` explains how `score` was reached,
    # so every deducted point is traceable to a named issue.
    model_estimate: int | None = None
    score_breakdown: dict[str, int] | None = None


class BuiltinTemplateSummary(BaseModel):
    """One of the templates that ship with the app.

    `ats_note` is shown to the user next to the template. Two of the seven are
    two-column layouts that some applicant tracking systems parse badly, and a
    picker that hides that is a picker that costs somebody an interview.
    """

    key: str
    name: str
    description: str
    columns: int
    ats_note: str
    upstream: str
    licence: str
    author: str
    tags: list[str] = Field(default_factory=list)


class GeneratedTemplateResponse(BaseModel):
    """A design turned into a LaTeX template, proven to compile before returning.

    `pdf_base64` is the sample render that validated it, which the caller stores
    as the preview: acceptance and preview are the same act. `attempts` and
    `repairs` report how much fixing the compile took, so the UI can be honest
    about a template that needed several passes.
    """

    name: str
    latex_source: str
    engine: Literal["tectonic"] = "tectonic"
    notes: str
    pdf_base64: str
    attempts: int = 1
    repairs: list[str] = Field(default_factory=list)


class ResumeRenderReviewRequest(ORMModel):
    json_resume: dict[str, Any]
    # Which look to render. `template_key` names a bundled template;
    # `latex_source` supplies a stored custom one and wins if both are given.
    # Neither falls back to the default. A stored template is untrusted input:
    # it is filled in a Jinja sandbox and compiled with tectonic --untrusted.
    template_key: str | None = None
    latex_source: str | None = None
    # The evidence vault the resume was built from, when the caller has it.
    #
    # This endpoint is stateless and resumes tailored through the Appwrite
    # workspace do not exist in this database, so the reviewer cannot look the
    # facts up: only the caller can supply them. Without them it has an empty
    # vault and grades the candidate's own verified history as unverified.
    # Measured on one real tailored document, the identical resume scored 21.0
    # with no facts and 60.0 with them, a 39-point swing driven by three
    # false blocking issues. Optional so an older client still gets a review,
    # just a blinder one.
    verified_facts: list[dict[str, Any]] | None = None


class ResumeRenderReviewResponse(BaseModel):
    """Review of a document this service never stores, plus the PDF it rendered.

    Lets a caller that cannot render PDFs itself (the Appwrite agent function,
    whose runtime has no LaTeX engine) obtain a real review and a real PDF and
    persist them in its own store.
    """

    review: ResumeReviewResult
    latex_source: str
    pdf_base64: str


class RenderReviewJobStart(BaseModel):
    job_id: str


class RenderReviewJobStatus(BaseModel):
    """Polled while a render-review job runs in the background.

    "running" until the background task finishes; then either "done" with
    the same payload /render-review used to return directly, or "error" with
    the message the caller would otherwise have gotten as an HTTP error.
    """

    status: Literal["running", "done", "error"]
    result: ResumeRenderReviewResponse | None = None
    error: str | None = None


class ResumeRenderResponse(BaseModel):
    """The rendered PDF and its LaTeX, with NO quality review.

    The fast half of render-review. Rendering a resume takes a few seconds;
    the independent model review takes over a minute. A caller that only needs a
    downloadable PDF (Download, a preview to attach) must not wait on the review,
    so this returns the PDF the moment the compile finishes. The review is fetched
    separately through /render-review and stored when it arrives.
    """

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


class RequirementMatch(BaseModel):
    """One JD requirement that a verified bullet already covers under another name.

    `rename` is how to word that bullet so the employer's own term appears. It is
    a wording instruction, never new content: the analyst may only point at a
    bullet the candidate already has.
    """
    requirement: str
    fact_bullet_id: str
    rename: str = ""


class TailorAnalysis(BaseModel):
    """The analyst pass, run before anything is written.

    Reading the job against the evidence once, up front, is cheaper and better
    than discovering the same answers one full rewrite at a time. The writer that
    follows is handed this instead of guessing.
    """
    covered: list[RequirementMatch] = Field(default_factory=list)
    gaps: list[GapQuestion] = Field(default_factory=list)
    shortlist_fact_ids: list[str] = Field(default_factory=list)
    positioning: str = ""


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
