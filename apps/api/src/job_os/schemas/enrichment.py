"""Precomputed, user-independent job facts produced once at ingest time.

Why this exists
---------------
The score a user reads while BROWSING was a hand-written keyword lexicon in the
web app, while the honest scorer (the requirement-coverage pipeline in
`services/tailor.py`) only ran after the user had already committed to tailoring
one specific job. The good scorer sat in the wrong place in the funnel.

The fix is the shape every serious job board converges on: spend one LLM call
per job at ingest, extract every fact that does not depend on who is looking,
and store it. Matching a user against those facts is then set arithmetic and
integer division. Enrichment cost is O(jobs). Matching cost is O(1) per job and
free, so scoring one profile against fifty thousand jobs costs zero LLM calls.

Everything in this module is deliberately user-independent. Nothing here knows
about a candidate. `services/job_match.py` is the half that does.

Provenance of the field list
----------------------------
Modelled on two measured references, not invented:

  * hiring.cafe attaches `v5_processed_job_data` to every job, 91 fields, a full
    sample of 96 jobs captured in
    `~/Documents/job-os-research/evidence/hiringcafe_ssr_payload.json`.
  * Jobright keeps requirements in BOTH readable prose (`qualifications`) and
    atomized form (`detailQualifications`), with skills carrying an importance
    score of 1 to 3. Sample in `evidence/jobright_job_detail.json`.

Keeping requirements in both forms is the detail that makes it work: the prose
is what a human reads on the card, the atomized list is what the matcher
intersects. Neither substitutes for the other.

What was cut from the 91, and why
---------------------------------
  * Every physical and shift field (physical_labor_intensity, physical_position,
    workplace_physical_environment, cognitive_demand, computer_usage,
    oral_communication_level, air/land_travel_requirement, on_call_requirement,
    overnight_work, morning/evening_shift_work, weekend/holiday availability,
    overtime_required). These carry real signal for the warehouse and clinical
    roles that dominate that corpus and none at all for CS/AI work.
  * Every benefit boolean (401k_matching, retirement_plan, generous_paid_time_off,
    generous_parental_leave, four_day_work_week, tuition_reimbursement,
    military_veterans). Each one is a separate judgment for the model to make,
    which is where enrichment cost actually goes, and not one of them moves a
    match score. They are filter facets for a mass-market board.
  * The county and continent tiers of the geo hierarchy, and the `number_of_*`
    count that accompanies each tier. Those counts exist to power their filter
    UI. One count is worth keeping (is this a single-site role or a fifty-city
    requisition) and the rest are storage.
  * `language_requirements` and `num_language_requirements`. All 96 sampled jobs
    said English, so the field carried zero information for its cost.
  * `position_employer_type` (internal versus external posting). That is a fact
    about their crawler, not about the job.
  * `job_category`, 29 mass-market values of which exactly one covers all of
    software. Replaced by `job_family`, which spends its resolution where this
    product needs it.
  * Company prose (tagline, activities, website). This repo already has a
    `Company` model; denormalizing it onto every job row is their index's
    problem, not ours.

Where this schema deliberately departs from the reference
--------------------------------------------------------
  * `visa_sponsorship` is tri-state, not boolean. Theirs is a bool, so "we
    explicitly do not sponsor" and "the posting never mentioned it" collapse to
    the same `False`. For an international candidate that distinction is the
    single most consequential fact in the whole schema, and collapsing it is
    the difference between "do not bother" and "worth asking".
  * The six compensation frequencies are derived in Python from the one figure
    the posting actually stated, not asked of the model. Arithmetic is free and
    exact here and merely likely there. It also avoids reproducing an
    inconsistency visible in the reference sample, where a job carrying
    `listed_compensation_frequency: "Hourly"` had its yearly figures populated
    and its hourly figures left null.
  * `commitment` includes `co-op`, which the reference has no value for. A co-op
    and a summer internship are different commitments with different eligibility
    rules, and this product's users apply to both.
  * `estimated_publish_date` keeps the reference's honesty about a crawled date
    being inferred, and adds `publish_date_is_estimated` so the honesty survives
    a consumer that only reads field values and never reads field names.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Bump when a field changes meaning or disappears. The reference is on its fifth
# revision (`v5_processed_job_data`), which is what a schema that ships looks
# like, so the version is here from the first row written rather than added later
# once there is already unversioned data in the table.
#
# Consumers must treat an unknown-but-higher version as readable and an unknown
# lower one as needing re-enrichment; `services/job_match.py` enforces that.
ENRICHMENT_SCHEMA_VERSION = 1

Necessity = Literal["required", "preferred"]
SkillKind = Literal["hard", "soft"]
DegreeStatus = Literal["required", "preferred", "not-mentioned"]
Tristate = Literal["yes", "no", "not-mentioned"]

# Seniority bands, ordered. The order is load-bearing: the experience axis of the
# scorer measures the signed distance between two bands, so inserting a value in
# the wrong place silently changes every score. Extends the vocabulary already
# used by `Job.level` and `jd_parse.ParsedJD` rather than inventing a parallel
# one, so an enriched job and an imported job stay comparable.
SENIORITY_ORDER: tuple[str, ...] = (
    "intern",
    "new-grad",
    "mid",
    "senior",
    "staff",
    "principal",
    "manager",
    "director",
)
Seniority = Literal[
    "intern",
    "new-grad",
    "mid",
    "senior",
    "staff",
    "principal",
    "manager",
    "director",
    "unknown",
]

# Ordered lowest to highest for the same reason as the seniority bands.
DEGREE_ORDER: tuple[str, ...] = (
    "none",
    "high-school",
    "associates",
    "bachelors",
    "masters",
    "doctorate",
)
DegreeLevel = Literal["none", "high-school", "associates", "bachelors", "masters", "doctorate"]

JobFamily = Literal[
    "software-engineering",
    "machine-learning-ai",
    "data",
    "platform-devops-sre",
    "security",
    "qa-test",
    "hardware-embedded",
    "research",
    "product",
    "design",
    "it-support",
    "other",
]

Commitment = Literal[
    "full-time",
    "part-time",
    "internship",
    "co-op",
    "contract",
    "temporary",
    "seasonal",
    "volunteer",
]

WorkplaceType = Literal["onsite", "hybrid", "remote", "field", "unknown"]

PayFrequency = Literal["yearly", "monthly", "bi-weekly", "weekly", "daily", "hourly"]

# The reference's own conversion basis, recovered from its numbers rather than
# assumed: a job listing $15/hour carried yearly 31200, monthly 2600, weekly 600,
# bi-weekly 1200 and daily 120, which is exactly 2080 hours a year, 52 weeks, 26
# fortnights, 12 months and an 8 hour day. Matching it keeps figures comparable
# across the two corpora.
HOURS_PER_YEAR = 2080
HOURS_PER_DAY = 8
WEEKS_PER_YEAR = 52
FORTNIGHTS_PER_YEAR = 26
MONTHS_PER_YEAR = 12

_NON_ALNUM = re.compile(r"[^a-z0-9+#]+")
_WHITESPACE = re.compile(r"\s+")
# Strips a trailing plural only when at least three letters precede it, which
# leaves "aws", "css" and "ios" alone. It happily produces non-words: "kubernetes"
# becomes "kubernete". That is fine and deliberate. The canonical key is never
# shown to anyone, and both the posting's skill and the candidate's skill are
# reduced by this same function, so an ugly key that both sides agree on beats a
# pretty key that only one side reaches.
_TRAILING_PLURAL = re.compile(r"(?<=[a-z]{3})(?<!s)s$")

# Surface forms that mean the same skill. This is the part of the web app's
# 118-entry lexicon that carries the value: not the list of skills (the model
# reads those straight off the posting now, so the list never has to be guessed
# in advance again) but the knowledge that "k8s" and "Kubernetes" are one thing.
#
# Every value here must be spelled the way `canonical_skill` would spell it:
# lowercase, single-spaced, punctuation stripped apart from `+` and `#`. Write
# "next js" and not "Next.js". An alias whose value does not survive its own
# normalizer is an alias that never matches anything, which is invisible in
# production and looks exactly like a candidate simply lacking the skill.
# `test_job_match.py::test_every_alias_value_is_already_canonical` is the guard,
# and it is the reason that class of bug cannot ship from here.
#
# Kept small on purpose. An alias only earns its place if the two forms are
# genuinely the same skill, never merely adjacent: "pytorch" and "tensorflow"
# are both deep learning frameworks and are not interchangeable on a resume.
SKILL_ALIASES: dict[str, str] = {
    # Languages and runtimes
    "golang": "go",
    "go lang": "go",
    "cpp": "c++",
    "js": "javascript",
    "ecmascript": "javascript",
    "ts": "typescript",
    "node": "node js",
    "nodejs": "node js",
    "shell scripting": "bash",
    "shell": "bash",
    # Frameworks
    "reactjs": "react",
    "react js": "react",
    "nextjs": "next js",
    "spring boot": "spring",
    "springboot": "spring",
    "spring framework": "spring",
    "fast api": "fastapi",
    "express js": "express",
    "expressjs": "express",
    # Data stores
    "postgres": "postgresql",
    "psql": "postgresql",
    "postgre": "postgresql",
    "mongo": "mongodb",
    "elastic search": "elasticsearch",
    "vector database": "vector db",
    "vector store": "vector db",
    "pinecone": "vector db",
    "weaviate": "vector db",
    "pgvector": "vector db",
    "faiss": "vector db",
    "milvus": "vector db",
    "qdrant": "vector db",
    "chroma": "vector db",
    "chromadb": "vector db",
    # Cloud and infrastructure
    "k8s": "kubernetes",
    "amazon web services": "aws",
    "google cloud": "gcp",
    "google cloud platform": "gcp",
    "microsoft azure": "azure",
    "containerization": "docker",
    "container": "docker",
    "cicd": "ci cd",
    "continuous integration": "ci cd",
    "continuous delivery": "ci cd",
    "continuous deployment": "ci cd",
    "infrastructure as code": "terraform",
    "cloudformation": "terraform",
    "pulumi": "terraform",
    "lambda": "serverless",
    "cloud function": "serverless",
    # Machine learning and AI
    "sklearn": "scikit learn",
    "scikit": "scikit learn",
    "ml": "machine learning",
    "ml model": "machine learning",
    "ml pipeline": "machine learning",
    "dl": "deep learning",
    "neural network": "deep learning",
    "natural language processing": "nlp",
    "large language model": "llm",
    "llms": "llm",
    "retrieval augmented generation": "rag",
    "fine tuning": "fine tune",
    "finetuning": "fine tune",
    "lora": "fine tune",
    "peft": "fine tune",
    "semantic search": "embedding",
    "vector search": "embedding",
    "agentic": "ai agent",
    "multi agent": "ai agent",
    "agent orchestration": "ai agent",
    "gpt 4": "openai",
    "gpt 5": "openai",
    "claude": "anthropic",
    "hugging face": "huggingface",
    "transformers library": "huggingface",
    "model serving": "mlops",
    "model deployment": "mlops",
    "model inference": "mlops",
    "computer vision": "cv",
    # APIs and systems
    "rest api": "rest",
    "restful": "rest",
    "restful api": "rest",
    "api design": "api",
    "api development": "api",
    "microservice": "microservices",
    "distributed system": "distributed systems",
    "message queue": "event driven",
    "pub sub": "event driven",
    "multithreading": "concurrency",
    "concurrent": "concurrency",
    "websocket": "websockets",
    # Testing and delivery
    "unit testing": "testing",
    "integration testing": "testing",
    "test automation": "testing",
    "automated testing": "testing",
    "qa automation": "testing",
    # Data and observability
    "etl": "data pipeline",
    "elt": "data pipeline",
    "pyspark": "spark",
    "monitoring": "observability",
    "prometheus": "observability",
    "grafana": "observability",
    "datadog": "observability",
    "tracing": "observability",
    "open telemetry": "observability",
    "opentelemetry": "observability",
}


def _normalize_surface(raw: str) -> str:
    """Lowercase, punctuation-stripped, single-spaced. No alias resolution."""
    text = _NON_ALNUM.sub(" ", raw.strip().lower())
    return _WHITESPACE.sub(" ", text).strip()


def _singularize(text: str) -> str:
    return " ".join(_TRAILING_PLURAL.sub("", word) for word in text.split(" "))


def _build_lookup() -> dict[str, str]:
    """Every surface form that resolves to a canonical key, including the keys.

    Built once at import. Each alias value registers as its own key, which is
    what makes the canonical form of a skill readable ("kubernetes", not
    "kubernete") while still letting the plural stripper handle the long tail
    the table does not enumerate. Without the self-registration the two paths
    disagree: "k8s" resolves through the table to "kubernetes" and "Kubernetes"
    falls through to the stripper and lands on "kubernete", so a posting and a
    profile naming the same skill share no key. That bug is silent in
    production, because it looks exactly like a candidate lacking the skill.
    """
    lookup: dict[str, str] = {}
    for surface, canon in SKILL_ALIASES.items():
        lookup[surface] = canon
        lookup.setdefault(_singularize(surface), canon)
    for canon in SKILL_ALIASES.values():
        lookup.setdefault(canon, canon)
        lookup.setdefault(_singularize(canon), canon)
    return lookup


_SKILL_LOOKUP = _build_lookup()


def canonical_skill(raw: str) -> str:
    """One skill string reduced to the key both sides of the match compare on.

    Both the job's requirement list and the candidate's profile pass through
    here, which is the only reason set intersection is a legitimate way to
    compare them. A skill that normalizes differently on the two sides is a
    skill that silently never matches, so this function is the contract, and it
    is the whole contract.

    Tries the table on the surface form, then on the de-pluralized form, then
    falls back to de-pluralizing. Idempotent by construction, which matters
    because a canonical key that changes when re-normalized would quietly split
    one skill into two across a re-enrichment.

    `+` and `#` survive normalization because dropping them would merge C, C++
    and C# into one skill.
    """
    text = _normalize_surface(raw)
    if not text:
        return ""
    hit = _SKILL_LOOKUP.get(text) or _SKILL_LOOKUP.get(_singularize(text))
    return hit if hit else _singularize(text)


class SkillRequirement(BaseModel):
    """One atomized ask, carrying how much it counts and how hard a gate it is.

    `importance` is Jobright's 1 to 3 scale, kept because a flat skill list makes
    "missing Kubernetes" and "missing Jira" cost the same, which is how a
    coverage ratio ends up ranking a role nobody wants above one they do.

    `evidence` holds the JD phrase the skill was lifted from. It exists so the
    attribution the scorer emits can quote the posting rather than assert.
    """

    model_config = ConfigDict(extra="forbid")

    skill: str = Field(description="Display form, as close to the posting's wording as possible.")
    canonical: str = Field(
        default="",
        description="Normalized match key. Derived in Python, never asked of the model.",
    )
    importance: Literal[1, 2, 3] = Field(
        default=2,
        description="3 the role is defined by it, 2 named as a real requirement, 1 mentioned.",
    )
    kind: SkillKind = "hard"
    necessity: Necessity = "required"
    evidence: str | None = Field(
        default=None, description="The JD phrase this was taken from, verbatim."
    )

    @model_validator(mode="after")
    def _fill_canonical(self) -> SkillRequirement:
        # Always recomputed, even when the model volunteered one. The canonical
        # key is how two independently written code paths agree, so it cannot be
        # something an LLM had an opinion about.
        self.canonical = canonical_skill(self.skill)
        return self


class RequirementsProse(BaseModel):
    """The same requirements as sentences, for a human to read.

    Kept alongside the atomized list rather than instead of it. Atomizing
    destroys the qualifier that makes a requirement negotiable: "5+ years of
    software engineering in a SaaS product company" becomes the skill
    "5+ years software engineering experience" plus the skill "SaaS", and the
    reader can no longer tell those came from one sentence, let alone which
    parts were joined by "or".
    """

    model_config = ConfigDict(extra="forbid")

    must_have: list[str] = Field(default_factory=list)
    preferred: list[str] = Field(default_factory=list)


class DegreeRequirement(BaseModel):
    """One degree level's status, stated per level rather than as a single floor.

    Atomized per level because a real posting asks for things a single "minimum
    degree" field cannot express. One job in the sample marked bachelors,
    masters AND doctorate all `Required`, which is a posting saying "BS plus
    currently enrolled in an MS or PhD programme", and any collapse of that to
    one number is wrong in one direction or the other.

    `not-mentioned` is a real value, never null. A posting that is silent about
    degrees is making a statement, and it is a different statement from a
    posting we failed to parse.
    """

    model_config = ConfigDict(extra="forbid")

    status: DegreeStatus = "not-mentioned"
    fields_of_study: list[str] = Field(default_factory=list)


class EducationRequirements(BaseModel):
    model_config = ConfigDict(extra="forbid")

    associates: DegreeRequirement = Field(default_factory=DegreeRequirement)
    bachelors: DegreeRequirement = Field(default_factory=DegreeRequirement)
    masters: DegreeRequirement = Field(default_factory=DegreeRequirement)
    doctorate: DegreeRequirement = Field(default_factory=DegreeRequirement)
    high_school_required: bool = False
    # A posting that says "currently enrolled" or "graduating in 2027" is
    # addressing students specifically, which changes what an in-progress degree
    # is worth. Neither reference exposes this and it decides whether a student
    # is eligible at all.
    enrolled_student_ok: bool = False

    def highest_required(self) -> DegreeLevel:
        """The tallest degree this posting genuinely gates on.

        `preferred` does not gate, so it is excluded here and handled as a
        softer deduction by the scorer.
        """
        for level in reversed(DEGREE_ORDER):
            if level in ("none", "high-school"):
                continue
            got = getattr(self, level, None)
            if isinstance(got, DegreeRequirement) and got.status == "required":
                return level  # type: ignore[return-value]
        if self.high_school_required:
            return "high-school"
        return "none"

    def highest_preferred(self) -> DegreeLevel:
        for level in reversed(DEGREE_ORDER):
            if level in ("none", "high-school"):
                continue
            got = getattr(self, level, None)
            if isinstance(got, DegreeRequirement) and got.status == "preferred":
                return level  # type: ignore[return-value]
        return "none"

    def all_fields_of_study(self) -> list[str]:
        out: list[str] = []
        for level in ("associates", "bachelors", "masters", "doctorate"):
            got = getattr(self, level)
            if isinstance(got, DegreeRequirement):
                out.extend(got.fields_of_study)
        return out


class Compensation(BaseModel):
    """Pay normalized to every frequency at once, from the one figure stated.

    The reference's insight is worth taking whole: normalize once at ingest and
    a filter on any basis is a comparison, never a re-derivation. A user asking
    for "$60/hour or better" and a user asking for "$120k or better" hit the
    same index.

    The derivation is done here in Python rather than by the model. It is exact,
    it is free, and asking a language model for six multiplications per job buys
    six chances to be wrong about something arithmetic settles.

    `is_transparent` is first class because its absence is itself the answer to
    a question users ask constantly, and because "no salary" and "salary we
    could not parse" must not look alike.
    """

    model_config = ConfigDict(extra="forbid")

    is_transparent: bool = False
    currency: str | None = None
    listed_frequency: PayFrequency | None = None
    listed_min: float | None = None
    listed_max: float | None = None

    yearly_min: int | None = None
    yearly_max: int | None = None
    monthly_min: int | None = None
    monthly_max: int | None = None
    bi_weekly_min: int | None = None
    bi_weekly_max: int | None = None
    weekly_min: int | None = None
    weekly_max: int | None = None
    daily_min: int | None = None
    daily_max: int | None = None
    hourly_min: int | None = None
    hourly_max: int | None = None

    equity_mentioned: bool = False
    bonus_mentioned: bool = False

    @model_validator(mode="after")
    def _derive_frequencies(self) -> Compensation:
        """Fill all six frequency pairs from whichever one the posting stated.

        Runs on every construction, including a reload from storage, so a row
        written by an older worker is self-repairing rather than half-filled.
        """
        if self.listed_frequency is None:
            return self
        for bound in ("min", "max"):
            listed = getattr(self, f"listed_{bound}")
            if listed is None:
                continue
            yearly = to_yearly(float(listed), self.listed_frequency)
            if yearly is None:
                continue
            hourly = yearly / HOURS_PER_YEAR
            for field_name, value in (
                (f"yearly_{bound}", yearly),
                (f"monthly_{bound}", yearly / MONTHS_PER_YEAR),
                (f"bi_weekly_{bound}", yearly / FORTNIGHTS_PER_YEAR),
                (f"weekly_{bound}", yearly / WEEKS_PER_YEAR),
                (f"daily_{bound}", hourly * HOURS_PER_DAY),
                (f"hourly_{bound}", hourly),
            ):
                setattr(self, field_name, int(round(value)))
        # A stated figure is the definition of transparent pay, whatever the
        # model thought when it also had to answer the question directly.
        if self.listed_min is not None or self.listed_max is not None:
            self.is_transparent = True
        return self


def to_yearly(amount: float, frequency: PayFrequency) -> float | None:
    if frequency == "yearly":
        return amount
    if frequency == "monthly":
        return amount * MONTHS_PER_YEAR
    if frequency == "bi-weekly":
        return amount * FORTNIGHTS_PER_YEAR
    if frequency == "weekly":
        return amount * WEEKS_PER_YEAR
    if frequency == "daily":
        # 260 working days, which is what 2080 hours at 8 a day comes to. Derived
        # rather than written as a literal so the six frequencies cannot drift
        # apart if the basis is ever changed.
        return amount * (HOURS_PER_YEAR / HOURS_PER_DAY)
    if frequency == "hourly":
        return amount * HOURS_PER_YEAR
    return None


class JobLocation(BaseModel):
    """One place the job can be done, parsed rather than left as a display string.

    The reference's five-tier hierarchy (city, county, state, country,
    continent) is flattened to three. Counties never appear in a CS/AI search
    and continents are recoverable from the country when anyone needs one.
    """

    model_config = ConfigDict(extra="forbid")

    city: str | None = None
    state: str | None = None
    country: str | None = None

    def label(self) -> str:
        return ", ".join(part for part in (self.city, self.state, self.country) if part)


class Workplace(BaseModel):
    """Where the work happens, including the genuinely-remote-anywhere case.

    `remote_anywhere` and `remote_countries` are kept separate from `locations`
    for the reason the reference separates them: a posting listing "Remote,
    United States" and a posting listing "Remote, anywhere on earth" are not
    the same offer, and folding the second into a country list loses it.
    """

    model_config = ConfigDict(extra="forbid")

    workplace_type: WorkplaceType = "unknown"
    locations: list[JobLocation] = Field(default_factory=list)
    # The one count from the reference worth keeping. A fifty-city requisition
    # and a one-office role read identically once you have the list, and this is
    # a cheap sort key for "actually near me".
    location_count: int = 0
    remote_anywhere: bool = False
    remote_countries: list[str] = Field(default_factory=list)
    relocation_assistance: bool = False

    @model_validator(mode="after")
    def _count_locations(self) -> Workplace:
        self.location_count = len(self.locations)
        return self


class Eligibility(BaseModel):
    """The gates that make an application impossible rather than unlikely.

    Grouped and named as gates rather than scattered among the other booleans,
    because a candidate who fails one of these should never be shown a 90%
    match. The scorer treats them as blockers, not as points.
    """

    model_config = ConfigDict(extra="forbid")

    # Tri-state on purpose; see the module docstring.
    visa_sponsorship: Tristate = "not-mentioned"
    work_authorization_required: bool = False
    citizenship_required: bool = False
    security_clearance: Literal["none", "required", "preferred", "not-mentioned"] = "not-mentioned"
    driver_license_required: bool = False
    fair_chance: bool = False
    certifications: list[str] = Field(default_factory=list)


class JobEnrichment(BaseModel):
    """Everything one LLM pass extracts from one posting, ready to match against.

    Stored on `Job.jd_parsed` under the `enrichment` key rather than in new
    columns. That is a deliberate choice: `jd_parsed` is already JSONB, so
    landing here needs no migration and no downtime, the whole document round
    trips as one value, and the fields that turn out to deserve indexing can be
    promoted to real columns later with evidence about which ones those are.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = ENRICHMENT_SCHEMA_VERSION
    enriched_at: datetime | None = None
    model: str | None = Field(default=None, description="Which model produced this.")

    # Extraction honesty. A posting whose compensation is unparseable still has
    # to index, so a failure is recorded as a named gap and the field is left
    # null, never guessed at and never fatal to the row.
    extraction_gaps: list[str] = Field(
        default_factory=list,
        description="Sections the pass could not fill. Empty means fully extracted.",
    )

    # Identity and taxonomy
    core_job_title: str = Field(
        default="",
        description="Title with seniority, location and requisition noise stripped.",
    )
    job_family: JobFamily = "other"
    specialization: str | None = Field(
        default=None, description="Finer role name, for example Cloud Engineer."
    )
    company_industry: str | None = None
    company_domains: list[str] = Field(
        default_factory=list,
        description="Product domains, for example fintech or developer tools.",
    )

    # Seniority and experience
    seniority_level: Seniority = "unknown"
    role_type: Literal["individual-contributor", "people-manager", "unknown"] = "unknown"
    min_years_experience: int | None = None
    max_years_experience: int | None = None
    # The reference's `is_min_..._yoe_not_mentioned`, kept because the scorer
    # needs to distinguish "no experience required" from "the posting did not
    # say", and those are the same null otherwise.
    years_experience_mentioned: bool = False

    # Requirements, both forms
    requirements_summary: str = ""
    requirements_prose: RequirementsProse = Field(default_factory=RequirementsProse)
    skills: list[SkillRequirement] = Field(default_factory=list)
    responsibilities: list[str] = Field(default_factory=list)
    technical_tools: list[str] = Field(
        default_factory=list,
        description="Named tools anywhere in the posting, requirement or not.",
    )

    education: EducationRequirements = Field(default_factory=EducationRequirements)
    compensation: Compensation = Field(default_factory=Compensation)
    workplace: Workplace = Field(default_factory=Workplace)
    eligibility: Eligibility = Field(default_factory=Eligibility)

    commitment: list[Commitment] = Field(default_factory=list)

    # Dates. Named `estimated` because a crawled posting date is inferred, and
    # the flag repeats it for a consumer that reads values and not names.
    estimated_publish_date: datetime | None = None
    publish_date_is_estimated: bool = True

    @field_validator("skills")
    @classmethod
    def _drop_empty_skills(cls, value: list[SkillRequirement]) -> list[SkillRequirement]:
        """Discard requirements that normalize to nothing, and de-duplicate.

        A model asked for a skill list occasionally emits punctuation, an empty
        string, or the same skill twice under two spellings. Any of those would
        inflate the denominator of the skills axis with something no profile can
        ever match, which reads to the user as an unexplained missing point.
        """
        seen: dict[str, SkillRequirement] = {}
        for item in value:
            if not item.canonical:
                continue
            existing = seen.get(item.canonical)
            if existing is None:
                seen[item.canonical] = item
                continue
            # Same skill twice: keep the stronger claim, so "required" outranks
            # "preferred" and higher importance wins.
            better_necessity = existing.necessity == "preferred" and item.necessity == "required"
            if better_necessity or item.importance > existing.importance:
                seen[item.canonical] = item
        return list(seen.values())

    def required_skills(self) -> list[SkillRequirement]:
        return [item for item in self.skills if item.necessity == "required"]

    def preferred_skills(self) -> list[SkillRequirement]:
        return [item for item in self.skills if item.necessity == "preferred"]
