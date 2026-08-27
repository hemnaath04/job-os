from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field, HttpUrl

from job_os.schemas.common import ORMModel, TimestampedRead


class CompanyRead(TimestampedRead):
    name: str
    domain: str | None = None
    linkedin_url: str | None = None
    industry: str | None = None
    size_bucket: str | None = None


class JobParsed(ORMModel):
    required_skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    responsibilities: list[str] = Field(default_factory=list)
    qualifications: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    sponsorship: str | None = None
    years_experience: str | None = None
    # Mirrors jd_parse.ParsedJD. Without it this schema dropped the flag on the
    # way out, so a job whose parse had timed out was served to the web app and
    # the MCP tools as six empty lists and two nulls: a confident "this posting
    # asks for nothing" where the truth was "we could not read it". Both look
    # identical to a reader, and only one of them is a fact. The scorer was
    # never fooled, since tailor.py reads the column off the ORM, but everything
    # downstream of the API was.
    parse_incomplete: bool = False


class JobRead(TimestampedRead):
    company: CompanyRead | None = None
    title: str
    level: str | None = None
    function: str | None = None
    location: str | None = None
    remote: str | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    salary_currency: str = "USD"
    source: str
    source_url: str | None = None
    posted_at: datetime | None = None
    closes_at: datetime | None = None
    active: bool
    jd_parsed: JobParsed | None = None


class JobCreateManual(ORMModel):
    company_name: str
    company_domain: str | None = None
    title: str
    location: str | None = None
    remote: str | None = None
    source_url: HttpUrl | None = None
    jd_text: str
    level: str | None = None
    function: str | None = None


class JobFromUrl(ORMModel):
    url: HttpUrl


class JobFromText(ORMModel):
    jd_text: str
    source_url: HttpUrl | None = None
    company_hint: str | None = None


class JobDescriptionPaste(ORMModel):
    """A job description pasted by hand onto a job that already exists."""

    jd_text: str = Field(min_length=1)


class JobEnrichResult(ORMModel):
    """The enriched job, plus what the paste actually earned.

    `filled` and `parse_used` are reported rather than left for the caller to
    diff, so the interface can say what it did instead of implying more than
    happened. A paste that parsed to nothing still saves the description, and
    this is how that comes back as the honest answer it is.
    """

    job: JobRead
    filled: list[str] = Field(default_factory=list)
    parse_used: bool = False


class JobFieldsForEnrich(ORMModel):
    """The job as the caller currently holds it, for planning a backfill.

    Only the fields the planner reads. The caller sends its own copy rather
    than a row id because the row it means may not exist in this database: the
    live pipeline keeps applications in Appwrite, and a card created there has
    no Postgres `jobs` row to look up.
    """

    # Not backfilled and never overwritten, but sent because the parser reads
    # it. A posting's own heading routinely carries the location and the
    # company when the body does not: BNY's says "Engineering (Developer) -
    # New York, NY", and parsing the body alone returned location=None on
    # every one of five runs against that exact text.
    title: str | None = None
    location: str | None = None
    remote: str | None = None
    level: str | None = None
    function: str | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    salary_currency: str | None = None
    jd_parsed: dict[str, Any] = Field(default_factory=dict)


class JobDescriptionParse(ORMModel):
    """A pasted description, plus the job it is meant to fill in."""

    jd_text: str = Field(min_length=1)
    job: JobFieldsForEnrich = Field(default_factory=JobFieldsForEnrich)


class JobEnrichPlan(ORMModel):
    """What the paste earned, for the caller to apply wherever the job lives.

    `updates` carries only fields the caller can hold. The description text
    itself is not returned: on the Appwrite path there is nowhere on the card
    that reads it, and a full JD in every card snapshot is weight that buys
    nothing.
    """

    updates: dict[str, Any] = Field(default_factory=dict)
    filled: list[str] = Field(default_factory=list)
    parse_used: bool = False


class JobMatch(ORMModel):
    job_id: UUID
    score: float = Field(ge=0, le=100)
    matched_skills: list[str]
    missing_skills: list[str]
    rationale: str
