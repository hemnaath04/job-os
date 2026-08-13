"""A small verified vault, shared by the cover-letter tests.

Modelled on the real profile rather than invented, because the failures these
tests exist to catch are the ones a real run produced: an AI agent that was
demoed and pending approval being described as shipped, team-built work written
up as solo, and a metric that belongs to one project appearing in a sentence
about another.
"""
from __future__ import annotations

from typing import Any

from job_os.schemas.cover_letters import (
    CoverLetterAgentOutput,
    LetterParagraph,
    LetterSentence,
)
from job_os.services.tailor import TailorBullet, TailorFact

# Bullet ids, named so a test reads as prose rather than as a lookup.
EPAM_TESTS = "b-epam-tests"
EPAM_AGENT = "b-epam-agent"
BEDROCKED_SCORE = "b-bedrocked-score"
# A bullet the loader never returns, because its parent fact is unverified. Used
# to prove that an id from outside the verified vault cannot print.
UNVERIFIED_BULLET = "b-unverified-kubernetes"


def vault() -> tuple[list[TailorFact], dict[str, list[TailorBullet]]]:
    """Every verified fact and bullet, as `run_cover_letter` takes them."""
    facts = [
        TailorFact(
            id="f-epam",
            kind="experience",
            title="Software Test Automation Engineer",
            org="EPAM Systems",
            payload={"technologies": ["Python", "Go", "Jenkins"]},
        ),
        TailorFact(
            id="f-bedrocked",
            kind="project",
            title="BedRocked",
            payload={"technologies": ["FastAPI"], "url": "https://example.invalid"},
        ),
    ]
    bullets = {
        "f-epam": [
            TailorBullet(
                id=EPAM_TESTS,
                fact_id="f-epam",
                text=(
                    "Wrote Python and Go automated test suites for a rideshare "
                    "client's pricing engine, and triaged the daily failures."
                ),
            ),
            TailorBullet(
                id=EPAM_AGENT,
                fact_id="f-epam",
                text=(
                    "Was part of a team building an AI agent over internal "
                    "requirements documents, demoed end to end and pending "
                    "senior approval."
                ),
            ),
        ],
        "f-bedrocked": [
            TailorBullet(
                id=BEDROCKED_SCORE,
                fact_id="f-bedrocked",
                text=(
                    "Scored 2,404 sewer segments on a six-factor 0-100 index and "
                    "served the result from a FastAPI backend."
                ),
            ),
        ],
    }
    return facts, bullets


MASTER_RESUME: dict[str, Any] = {
    "basics": {
        "name": "Hemnaath Balasubramani",
        "email": "balasubramani.h@northeastern.edu",
        "phone": "+1 617 555 0134",
        "location": {"city": "Boston", "region": "MA"},
        "profiles": [
            {"network": "GitHub", "url": "github.com/hemnaath04"},
            {"network": "LinkedIn", "url": "linkedin.com/in/hemnaath"},
        ],
    }
}

# A posting the vault answers in part: Python and testing are covered, Kubernetes
# is nowhere in it, which is what a gap question is for.
JD_PARSED: dict[str, Any] = {
    "title": "Backend Engineer, Platform",
    "company": "Corvus Systems",
    "required_skills": ["Python", "test automation", "Kubernetes"],
    "preferred_skills": ["Terraform"],
}


def say(text: str, bullet_id: str | None = None) -> LetterSentence:
    return LetterSentence(text=text, fact_bullet_id=bullet_id)


def letter(
    *,
    opening: list[LetterSentence] | None = None,
    body: list[list[LetterSentence]] | None = None,
    closing: list[LetterSentence] | None = None,
    **kwargs: Any,
) -> CoverLetterAgentOutput:
    """One agent reply, with sensible defaults for the parts a test ignores."""
    return CoverLetterAgentOutput(
        opening=LetterParagraph(
            sentences=opening
            if opening is not None
            else [say("I am applying for the Backend Engineer, Platform role.")]
        ),
        body=[LetterParagraph(sentences=group) for group in (body or [])],
        closing=LetterParagraph(
            sentences=closing
            if closing is not None
            else [say("I would welcome a conversation about the role.")]
        ),
        **kwargs,
    )
