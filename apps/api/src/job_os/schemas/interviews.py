"""Interview prep contracts.

The pack is generated from two things this app already holds legitimately: the
parsed job description, and the candidate's own verified fact vault. Nothing
here is a question bank. A question is either derived from something the
employer asked for or from something the candidate already claims, which is why
every question carries a `topic` and every scaffolded answer carries provenance.

The one invariant the whole file exists to serve: an answer scaffold may only be
built out of `verified=True` ProfileFact and FactBullet rows, and where the vault
has nothing, the pack declares a gap rather than writing a story.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from job_os.schemas.common import ORMModel, TimestampedRead

QuestionCategory = Literal["technical", "behavioral", "resume_probe", "candidate_ask"]
Difficulty = Literal["warmup", "core", "stretch"]
TopicStatus = Literal["evidenced", "gap"]
ReadinessBand = Literal["strong", "mixed", "thin", "not_scored"]
Confidence = Literal["shaky", "workable", "solid"]


class EvidenceCitation(BaseModel):
    """One verified row behind a scaffolded answer.

    `text` is the verified wording itself, not a paraphrase, so a user reading
    the scaffold can see exactly what they are entitled to say and where it came
    from. `fact_bullet_id` is null when the citation is the fact's own fields
    (a title, an org, a payload) rather than one of its bullets.
    """

    fact_id: str
    fact_bullet_id: str | None = None
    label: str
    text: str


class AnswerScaffold(BaseModel):
    """A STAR skeleton assembled from verified evidence.

    A skeleton, deliberately, not an answer: it names the situation, the task,
    the actions and the result the evidence supports, and the user says it in
    their own words. Every field is checked against the citations before it is
    stored.
    """

    situation: str = ""
    task: str = ""
    action: str = ""
    result: str = ""

    def joined(self) -> str:
        return " ".join(
            part for part in (self.situation, self.task, self.action, self.result) if part
        )


class GeneratedQuestion(BaseModel):
    """One question exactly as the model returns it, before any grounding.

    Evidence arrives as ids only. The model never writes a citation's text, so a
    citation can only ever be a row that exists: an id that is not in the
    verified set is dropped, the same way the tailor drops an invented
    `fact_bullet_id` before the writer ever sees it.
    """

    question: str
    topic: str = ""
    difficulty: Difficulty = "core"
    why_asked: str = ""
    fact_ids: list[str] = Field(default_factory=list)
    fact_bullet_ids: list[str] = Field(default_factory=list)
    scaffold: AnswerScaffold | None = None


class InterviewPrepOutput(BaseModel):
    """Schema the generating model returns verbatim, pydantic-validated.

    `readiness_estimate` is advisory and the prompt says so. The grade is
    computed by `interview_prep.readiness` from the requirement coverage, for the
    same reason the resume review derives its score from the issue list: a model's
    free-form 0-100 swung nine points across identical reviews of one document.
    """

    technical: list[GeneratedQuestion] = Field(default_factory=list)
    behavioral: list[GeneratedQuestion] = Field(default_factory=list)
    resume_probes: list[GeneratedQuestion] = Field(default_factory=list)
    candidate_asks: list[GeneratedQuestion] = Field(default_factory=list)
    readiness_estimate: int | None = None
    note: str = ""


class TopicReadiness(BaseModel):
    """One JD requirement, and whether the vault can answer a question about it.

    `citations` names where in the vault the requirement's own words appear, so
    the status is checkable rather than asserted. `alternatives` is every wording
    that would have satisfied it, which is what makes a `gap` explainable: the
    reader can see the tool looked for more than one spelling.
    """

    topic: str
    preferred: bool
    status: TopicStatus
    citations: list[str] = Field(default_factory=list)
    alternatives: list[str] = Field(default_factory=list)


class DefenceRisk(BaseModel):
    """A claim already on the resume that the candidate may struggle to defend.

    Deterministic and specific: a bullet whose provenance points at a fact bullet
    that is no longer in the verified set, or one carrying a number the vault
    marks as unverified. These are the bullets an interviewer probes and the
    candidate cannot back, which is the most expensive kind of surprise.
    """

    text: str
    where: str
    reason: str


class ReadinessReport(BaseModel):
    """Why the readiness number is what it is, topic by topic.

    Deterministic by construction: the score is a function of the parsed JD and
    the verified vault alone, so the same inputs produce the same number, and
    every point of it is traceable to a named topic. `model_estimate` is the
    generating model's own guess, kept for context and never the grade.
    """

    score: Decimal | None
    band: ReadinessBand
    scored_topics: int
    evidenced_topics: int
    topics: list[TopicReadiness] = Field(default_factory=list)
    defence_risks: list[DefenceRisk] = Field(default_factory=list)
    # Requirement sentences too long to word-match against anything, reported so
    # the reader knows they were seen and set aside rather than silently ignored.
    unscored_requirements: list[str] = Field(default_factory=list)
    formula: str = ""
    thresholds: dict[str, int] = Field(default_factory=dict)
    model_estimate: int | None = None


class InterviewQuestionRead(TimestampedRead):
    prep_id: UUID
    category: QuestionCategory
    position: int
    question: str
    topic: str | None = None
    difficulty: str
    why_asked: str
    scaffold: AnswerScaffold | None = None
    evidence: list[EvidenceCitation] = Field(default_factory=list)
    gap: bool
    gap_note: str | None = None
    removed_claims: list[str] = Field(default_factory=list)
    flagged: bool
    confidence: str | None = None
    times_reviewed: int
    last_reviewed_at: datetime | None = None
    next_review_at: datetime | None = None


class InterviewPrepSummary(TimestampedRead):
    application_id: UUID
    job_id: UUID | None = None
    resume_version_id: UUID | None = None
    readiness_score: Decimal | None = None
    model_estimate: int | None = None
    note: str = ""


class InterviewPrepRead(InterviewPrepSummary):
    # Typed as a dict on the way out rather than as ReadinessReport, because a
    # pack generated by an older build has an older report shape stored in JSONB
    # and a strict model would 500 the read instead of showing the pack.
    readiness_report: dict[str, Any] = Field(default_factory=dict)
    questions: list[InterviewQuestionRead] = Field(default_factory=list)
    # Denormalised for the UI, which shows the pack without loading the
    # application alongside it.
    job_title: str | None = None
    company_name: str | None = None


class InterviewPrepJobStart(BaseModel):
    job_id: str


class InterviewPrepJobStatus(BaseModel):
    """Polled while a prep-generation job runs in the background.

    "running" until the background task finishes; then either "done" with
    the same payload `/generate` used to return directly, or "error" with
    the message the caller would otherwise have gotten as an HTTP error.
    Same shape as resumes.py's `RenderReviewJobStatus`, for the same reason:
    Heroku's router kills any request still waiting past 30 seconds, and one
    real model pass over the JD, the vault and the resume routinely runs
    longer than that -- every real `/generate` call was failing this way,
    not occasionally.
    """

    status: Literal["running", "done", "error"]
    result: InterviewPrepRead | None = None
    error: str | None = None


class VaultBullet(BaseModel):
    id: str = Field(max_length=64)
    text: str = Field(max_length=2000)
    metric_verified: bool = True


class VaultFact(BaseModel):
    """One verified fact sent by a caller whose vault does not live in Postgres.

    Ids are strings because the two backends mint different shapes, the same
    reason the tailoring contract uses strings: Postgres mints UUIDs and the
    Appwrite workspace mints its own 20-character tokens, and a UUID-typed field
    would reject every fact the user added from the browser.
    """

    id: str = Field(max_length=64)
    kind: str = Field(max_length=64)
    title: str = Field(max_length=500)
    org: str | None = Field(default=None, max_length=500)
    start_date: date | None = None
    end_date: date | None = None
    location: str | None = Field(default=None, max_length=500)
    source_url: str | None = Field(default=None, max_length=2000)
    payload: dict[str, Any] = Field(default_factory=dict)
    verified: bool = False
    bullets: list[VaultBullet] = Field(default_factory=list, max_length=100)


class InterviewPrepGenerateRequest(ORMModel):
    application_id: UUID
    # The evidence vault, when the caller holds it and this database does not.
    #
    # Same reason `ResumeRenderReviewRequest` takes it: the Appwrite workspace
    # keeps the user's facts in Appwrite, so a pack generated from the Postgres
    # rows alone would find an empty vault, mark every topic a gap and score a
    # well-prepared candidate at zero. Optional, so the Postgres deployment is
    # unaffected. Unverified rows are dropped server-side whatever the caller
    # sends, because the honesty contract cannot depend on the client honouring
    # it.
    verified_facts: list[VaultFact] | None = Field(default=None, max_length=500)


class InterviewQuestionPatch(ORMModel):
    """A practice outcome, or a flag. Never a change to the question itself.

    Editing generated text would break the link between a question and the
    evidence it was grounded in, so the only writable fields are the user's own
    bookkeeping.
    """

    flagged: bool | None = None
    confidence: Confidence | None = None
