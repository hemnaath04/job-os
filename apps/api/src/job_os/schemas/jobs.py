from datetime import datetime
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


class JobMatch(ORMModel):
    job_id: UUID
    score: float = Field(ge=0, le=100)
    matched_skills: list[str]
    missing_skills: list[str]
    rationale: str
