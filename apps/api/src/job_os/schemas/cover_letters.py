"""Schemas for the cover-letter agent.

Contract, and it is the same one the tailoring pipeline enforces: every specific
claim in a generated letter must trace to a `fact_bullets` row. The model returns
*sentences with attributions*; Python decides which of them may print, assembles
the letter deterministically, and emits one `CoverLetterProvenanceEntry` per
surviving claim. A JD requirement the vault cannot support becomes a
`GapQuestion`, never a sentence.

`GapQuestion` is imported from the resume schemas rather than redefined. It is
the same object with the same meaning, and the UI already knows how to show one.
"""
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from job_os.schemas.common import ORMModel, TimestampedRead
from job_os.schemas.resumes import GapQuestion

# How the letter should sound. Deliberately three, and none of them is
# "enthusiastic": the competitor's letters read as cheap because they were
# enthusiastic and unspecific, and every tone here is a way of being plain.
#
#   plain  - flat, factual, no warmth claims. The default.
#   warm   - the same facts, one degree less clipped. Still no adjectives.
#   direct - shortest possible. Opens on the strongest claim, no scene setting.
CoverLetterTone = Literal["plain", "warm", "direct"]


class LetterSentence(BaseModel):
    """One sentence of the letter, and the verified bullet it rests on.

    `fact_bullet_id` is null only for a sentence that makes no claim about the
    candidate's own work: a greeting line, a statement of what the role is, a
    closing. Python decides whether that is actually true of the sentence rather
    than trusting the model's null, so an unattributed sentence carrying a
    number, a technology or a past-tense claim verb is refused. That check is
    what makes an unverifiable sentence unprintable instead of merely
    discouraged.
    """

    text: str
    fact_bullet_id: str | None = None


class LetterParagraph(BaseModel):
    sentences: list[LetterSentence] = Field(default_factory=list)


class CoverLetterAgentOutput(BaseModel):
    """Schema the model returns verbatim. Pydantic-validated before assembly."""

    opening: LetterParagraph = Field(default_factory=LetterParagraph)
    body: list[LetterParagraph] = Field(default_factory=list)
    closing: LetterParagraph = Field(default_factory=LetterParagraph)
    gap_questions: list[GapQuestion] = Field(default_factory=list)
    agent_note: str = ""


class CoverLetterProvenanceEntry(BaseModel):
    """One row per claim sentence in the letter, proving the contract.

    `paragraph` and `sentence` are indexes into the assembled document, so a
    reader can point at the exact sentence a row is about. `fact_bullet_id` and
    `fact_id` are the verified rows it came from.
    """

    paragraph: int
    sentence: int
    text: str
    fact_bullet_id: str
    fact_id: str


class RefusedSentence(BaseModel):
    """A sentence Python refused to print, and why.

    Surfaced rather than swallowed. A refusal is the system working, and the user
    is better served by seeing that a claim was dropped for inventing a number
    than by receiving a shorter letter with no explanation.
    """

    text: str
    reason: str
    fact_bullet_id: str | None = None


class CoverLetterSender(BaseModel):
    """The candidate's own contact block, taken only from the master resume.

    Never from the model. The rules already forbid inferring a phone number or
    an email, and the cheapest way to honour that is to give the model no way to
    write one.
    """

    name: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    links: list[str] = Field(default_factory=list)


class CoverLetterDocument(BaseModel):
    """The letter as it will be rendered.

    Assembled by Python from the agent's surviving sentences. `paragraphs` is
    already joined prose, in print order, with the greeting and the sign-off held
    separately so a template never has to guess which paragraph is which.
    """

    sender: CoverLetterSender = Field(default_factory=CoverLetterSender)
    date: str = ""
    company: str = ""
    role: str = ""
    recipient_name: str = ""
    greeting: str = "Dear Hiring Team,"
    subject: str = ""
    paragraphs: list[str] = Field(default_factory=list)
    signoff: str = "Sincerely,"
    word_count: int = 0


class CoverLetterResult(BaseModel):
    """Everything one generation produced, ready to persist or to show.

    `quality_flags` are writing problems Python measured on the assembled letter,
    keyed the way `document_quality_flags` keys resume problems, so the same UI
    shape works for both.
    """

    document: CoverLetterDocument
    provenance: list[CoverLetterProvenanceEntry] = Field(default_factory=list)
    gap_questions: list[GapQuestion] = Field(default_factory=list)
    refused: list[RefusedSentence] = Field(default_factory=list)
    quality_flags: dict[str, list[str]] = Field(default_factory=dict)
    tone: CoverLetterTone = "plain"
    agent_note: str = ""
    passes: int = 1


# ---- API surface -------------------------------------------------------------


class CoverLetterRead(TimestampedRead):
    name: str
    job_id: UUID | None = None
    archived_at: datetime | None = None


class CoverLetterVersionSummary(TimestampedRead):
    cover_letter_id: UUID
    spawned_from_job_id: UUID | None = None
    spawned_from_application_id: UUID | None = None
    parent_version_id: UUID | None = None
    status: str = "draft"
    tone: str = "plain"
    template_key: str | None = None
    word_count: int | None = None
    approved_by_user: bool = False
    revision_note: str | None = None
    finalized_at: datetime | None = None
    archived_at: datetime | None = None


class CoverLetterVersionRead(CoverLetterVersionSummary):
    document: dict[str, Any] = Field(default_factory=dict)
    provenance: list[dict[str, Any]] = Field(default_factory=list)
    gap_questions: list[dict[str, Any]] = Field(default_factory=list)
    refused: list[dict[str, Any]] = Field(default_factory=list)
    quality_flags: dict[str, list[str]] = Field(default_factory=dict)
    agent_note: str = ""


class CoverLetterGenerateRequest(ORMModel):
    job_id: UUID
    tone: CoverLetterTone = "plain"
    # Which resume look the letter should match, so the two documents read as a
    # set. Defaults to the template the tailored resume for this job was
    # rendered with, and falls back to the app default.
    template_key: str | None = None
    # Only ever what the user typed. The agent is never asked for a name and
    # cannot supply one, because inventing a hiring manager is the same class of
    # failure as inventing a metric.
    recipient_name: str | None = None
    # Regenerating from an existing version records it as the parent, which is
    # what gives a letter history rather than a single mutable draft.
    parent_version_id: UUID | None = None
    revision_note: str | None = None


class CoverLetterEditRequest(ORMModel):
    """A manual edit, re-validated exactly as a generated letter is.

    Paragraphs arrive as prose, because that is what the user edited. Provenance
    for an edited sentence cannot be inferred, so an edit that changes a claim
    sentence loses that sentence's row unless the text still matches. See
    `revalidate_edited_letter`.
    """

    paragraphs: list[str]
    note: str = "Manual edit"


class CoverLetterRenderResponse(BaseModel):
    """The rendered letter, plus what its text layer looks like to a parser."""

    pdf_base64: str
    engine: str
    page_count: int
    text_selectable: bool
    text_layer_issues: list[str] = Field(default_factory=list)
