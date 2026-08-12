"""One real posting and the candidate's real verified vault, shared by the prep tests.

The JD is a genuine backend/AI new-grad posting of the kind this app is used
against: a parsed form of the sort `jd_parse` produces, with a required list, a
prose requirement, a nice-to-have stack, and the eligibility line every posting
carries and no resume can word-match.

The vault is the candidate's own verified history as `career_ops_rules` records
it: EPAM as the single employer, the flagship projects, the coursework. Using
their real facts rather than "Company A" matters here, because the whole point of
the pack is that it is specific to a real person, and a synthetic vault would let
a fabrication guard pass on a technicality.
"""
from __future__ import annotations

from job_os.services.tailor import TailorBullet, TailorFact

JD_PARSED = {
    "title": "Software Engineer, Backend and AI Platform",
    "company": "Northwind Data",
    "level": "new-grad",
    "function": "swe",
    "required_skills": [
        "Python",
        "FastAPI",
        "PostgreSQL",
        "Kubernetes",
        "Strong CS fundamentals: data structures, algorithms, concurrency",
        "Bachelor's or Master's in Computer Science or equivalent practical experience",
    ],
    "preferred_skills": [
        "Terraform",
        "Experience with LLM application patterns such as retrieval augmented generation",
    ],
    "technologies": ["Python", "FastAPI", "PostgreSQL", "Kubernetes", "Terraform", "Go"],
    "responsibilities": [
        "Own backend services from design through production support",
        "Build evaluation harnesses for LLM features",
        "Work directly with data scientists and with the platform team",
    ],
    "qualifications": [
        "Comfortable owning ambiguous work end to end",
        "Writes tests as a matter of course",
    ],
    "keywords": ["backend", "platform", "evaluation", "agents"],
    "years_experience": "0-2",
}

JD_CLEAN = """\
Software Engineer, Backend and AI Platform

Northwind Data builds the data platform behind mid-market analytics products. The
platform team owns the services that ingest, score and serve customer data, and
we are adding LLM features on top of it.

What you will do
- Own backend services in Python and FastAPI, from design through production
  support, backed by PostgreSQL.
- Build evaluation harnesses for our LLM features so we can tell whether a change
  made the product better or only different.
- Work with data scientists on model integration and with the platform team on
  the Kubernetes services underneath.

What we are looking for
- Strong computer science fundamentals: data structures, algorithms, concurrency.
- Python, and comfort reading a language you have not written before. Some of the
  older services are Go.
- Someone who writes tests as a matter of course and is comfortable owning
  ambiguous work end to end.
- Bachelor's or Master's in Computer Science, or equivalent practical experience.

Nice to have
- Terraform, or another infrastructure-as-code tool.
- Experience with LLM application patterns such as retrieval augmented generation.
"""


def vault() -> tuple[list[TailorFact], dict[str, list[TailorBullet]]]:
    """The verified facts and bullets, exactly as the loader would hand them over."""
    facts = [
        TailorFact(
            id="fact-epam",
            kind="experience",
            title="Test Automation Engineer",
            org="EPAM Systems",
            location="Hyderabad, India",
        ),
        TailorFact(
            id="fact-jobsearcher",
            kind="project",
            title="Job Searcher",
            source_url="https://github.com/hemnaath04/job-searcher",
            payload={"technologies": ["Python", "FastAPI", "PostgreSQL"]},
        ),
        TailorFact(
            id="fact-bedrocked",
            kind="project",
            title="BedRocked, Civic Sewer-Sequencing Platform",
            source_url="https://github.com/hemnaath04/bedrocked",
        ),
        TailorFact(id="fact-skill-python", kind="skill", title="Python", org="Languages"),
        TailorFact(id="fact-skill-go", kind="skill", title="Go", org="Languages"),
        TailorFact(id="fact-skill-sql", kind="skill", title="SQL", org="Languages"),
        TailorFact(
            id="fact-education",
            kind="education",
            title="MS Computer Science",
            org="Northeastern University, Khoury College",
            payload={"courses": ["Programming Design Paradigm", "Database Management Systems"]},
        ),
    ]
    bullets = {
        "fact-epam": [
            TailorBullet(
                id="b-epam-go",
                fact_id="fact-epam",
                text=(
                    "Worked on a Go and Python automated test suite for a rideshare "
                    "client's pricing engine, investigating failures and fixing flaky tests."
                ),
            ),
            TailorBullet(
                id="b-epam-cicd",
                fact_id="fact-epam",
                text=(
                    "Migrated the suite's CI/CD pipeline and trained two new team "
                    "members on it."
                ),
            ),
            TailorBullet(
                id="b-epam-agent",
                fact_id="fact-epam",
                text=(
                    "Was part of a team building an AI agent over internal requirements "
                    "documents that drafts test cases; demoed end to end, pending senior "
                    "approval."
                ),
            ),
        ],
        "fact-jobsearcher": [
            TailorBullet(
                id="b-js-api",
                fact_id="fact-jobsearcher",
                text=(
                    "Built a FastAPI service over PostgreSQL that scores job postings "
                    "against a resume and serves the ranked list."
                ),
            ),
            TailorBullet(
                id="b-js-concurrency",
                fact_id="fact-jobsearcher",
                text=(
                    "Wrote the scraper as a bounded worker pool so a slow source could "
                    "not stall the run."
                ),
            ),
        ],
        "fact-bedrocked": [
            TailorBullet(
                id="b-br-score",
                fact_id="fact-bedrocked",
                text=(
                    "Scored 2,404 sewer segments with a six-factor 0-100 model and "
                    "visualised them for a civic hackathon."
                ),
            )
        ],
    }
    return facts, bullets
