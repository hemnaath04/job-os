"""Culture copy is not a missing skill.

Almost every posting carries a paragraph about the kind of person the employer
hopes to like: collaboration, passion, a growth mindset, a values list, a DEI
statement. A parser drops those straight into `required_skills` alongside the
technologies, and the scorer counted each one as a must-have no resume can
answer. The effect is not cosmetic: a values paragraph runs to a dozen terms,
so a posting with one pushed a genuine match below half on wording alone, and
the "missing" panel told the user to go add "enthusiasm" to their profile.

What must NOT change is the other half. A named technology, a domain, a tool
and a measurable responsibility all still count against the score, including
the ones that sound soft read cold: accessibility is a frontend skill,
mentoring is a senior engineer's job, and reliability engineering is a
discipline. See `_CULTURE_FLUFF_RE` for the two rules that keep those in.

Fixtures are a generic posting and a generic candidate.
"""
from __future__ import annotations

import pytest

from job_os.services.tailor import (
    _compute_ats_from_document,
    _is_candidate_skill,
    _is_culture_fluff,
    _jd_requirements,
    jd_skill_order,
)

# A posting written the way most postings are: real asks, then a paragraph of
# copy about the team. Both halves arrive in the same list.
POSTING = {
    "required_skills": [
        "Python",
        "PostgreSQL",
        "REST APIs",
        "Docker",
        "collaboration",
        "passion for our mission",
        "growth mindset",
        "thrives in a fast-paced environment",
        "excellent communicator",
        "self-motivated",
    ],
    "qualifications": [
        "We value curiosity, humility and a bias for action.",
        "We are an equal opportunity employer and encourage applicants of all "
        "backgrounds to apply.",
    ],
    "preferred_skills": ["Kubernetes", "sense of humor"],
    "technologies": ["Redis"],
}

# Someone who genuinely does this job, written the way a person writes a resume:
# no sentence in here says "collaboration" or "growth mindset".
RESUME = {
    "basics": {"summary": "Backend engineer working in Python."},
    "work": [
        {
            "name": "A payments company",
            "highlights": [
                "Built REST APIs over PostgreSQL for a payments ledger.",
                "Packaged the services with Docker and cut cold start time.",
                "Added a Redis cache in front of the hottest read path.",
            ],
        }
    ],
}


def _labels(jd: dict) -> set[str]:
    reqs, _prose, _excluded = _jd_requirements(jd)
    return {req.label for req in reqs}


# --- what counts as culture copy ---------------------------------------------


@pytest.mark.parametrize(
    "term",
    [
        "collaboration",
        "cross-functional collaboration",
        "passion",
        "passionate about the mission",
        "curiosity",
        "humility",
        "empathy",
        "adaptability",
        "flexibility",
        "proactive",
        "self-motivated",
        "results-oriented",
        "bias for action",
        "customer obsession",
        "willingness to learn",
        "positive attitude",
        "sense of humor",
        "wear many hats",
        "entrepreneurial",
        "thrives in ambiguity",
        "strong communicator",
        "ability to work independently",
        "we value transparency",
        "our values",
        "culture fit",
        "work-life balance",
        # DEI statements, which arrive as their own entries just as often.
        "diversity",
        "diversity, equity and inclusion",
        "inclusive environment",
        "belonging",
        "underrepresented groups",
        "equal opportunity employer",
        "encouraged to apply",
    ],
)
def test_culture_copy_is_not_a_candidate_skill(term: str) -> None:
    assert _is_culture_fluff(term), term
    assert not _is_candidate_skill(term), term


@pytest.mark.parametrize(
    "term", ["team player", "fast-paced environment", "growth mindset", "work ethic"]
)
def test_the_soft_skill_boilerplate_already_filtered_stays_filtered(term: str) -> None:
    """These were already excluded by `_NON_SKILL_RE` before culture copy had a
    rule of its own. Pinned here because the two filters now sit next to each
    other and it must stay obvious that neither one dropped its half."""
    assert not _is_candidate_skill(term), term


@pytest.mark.parametrize(
    "term",
    [
        # Named technologies and concrete qualifications.
        "Python",
        "Kubernetes",
        "PostgreSQL",
        "TypeScript",
        "Figma",
        "distributed systems",
        "data structures",
        "incident response",
        "observability",
        "user research",
        "product analytics",
        "design systems",
        "stakeholder management",
        "prioritization",
        "leadership",
        "mentoring junior engineers",
        # The ones a broader rule would have eaten. Each is a real discipline
        # whose name contains a word the culture list also uses.
        "collaborative filtering",
        "accessibility",
        "accessibility testing",
        "data integrity",
        "event-driven architecture",
        "test-driven development",
        "data-driven experimentation",
        "reliability engineering",
        "resilient systems",
        "site reliability",
    ],
)
def test_a_real_qualification_still_counts(term: str) -> None:
    assert not _is_culture_fluff(term), term
    assert _is_candidate_skill(term), term


# --- what that does to a real posting ----------------------------------------


def test_culture_terms_never_reach_the_requirement_list() -> None:
    labels = _labels(POSTING)

    for noise in (
        "collaboration",
        "passion for our mission",
        "growth mindset",
        "thrives in a fast-paced environment",
        "excellent communicator",
        "self-motivated",
        "sense of humor",
    ):
        assert noise not in labels, noise
    # And the posting's actual asks are all still there.
    assert {"Python", "PostgreSQL", "Docker", "Kubernetes", "Redis"} <= labels


def test_a_values_sentence_contributes_no_requirement() -> None:
    """A whole sentence is never scored, and nothing is mined out of this one.

    It stays in `prose_requirements`, which is a record of what the posting
    said, not a list of gaps. What must not happen is "curiosity" or "humility"
    surfacing from inside it as a must-have.
    """
    reqs, prose, _excluded = _jd_requirements(POSTING)

    assert any("We value curiosity" in term for term in prose)
    for noise in ("curiosity", "humility", "bias for action", "all backgrounds"):
        assert not any(noise in req.label.casefold() for req in reqs), noise


def test_a_responsibility_that_sounds_like_culture_is_not_labelled_as_culture() -> None:
    """The guardrail on the sentence-level rule.

    "Collaborate with product managers to ship the React app" matches the
    culture vocabulary and is a responsibility naming a real technology.
    Setting the whole sentence aside as values copy would be a claim nobody
    checked, so the sentence path does not make one.
    """
    jd = {
        "required_skills": [
            "Collaborate with product managers and backend engineers to ship "
            "React features",
        ]
    }
    _reqs, _prose, excluded = _jd_requirements(jd)

    assert excluded == []


def test_the_score_measures_the_job_not_the_values_paragraph() -> None:
    score, report = _compute_ats_from_document(
        jd_parsed=POSTING,
        json_resume=RESUME,
        fallback_matched=[],
        fallback_missing=[],
    )
    scored = set(report["matched"]) | set(report["missing"])

    assert not any(_is_culture_fluff(term) for term in scored), sorted(scored)
    # This candidate answers every real must-have the posting names, so the
    # number says so instead of being dragged down by copy.
    assert report["required_met"] == report["required_total"]
    assert float(score) == 100.0


def test_a_missing_hard_skill_is_still_a_gap() -> None:
    """Fairer must not mean flattering."""
    posting = {
        "required_skills": ["Python", "Terraform", "passion", "collaboration"],
    }
    _score, report = _compute_ats_from_document(
        jd_parsed=posting,
        json_resume=RESUME,
        fallback_matched=[],
        fallback_missing=[],
    )

    assert "Terraform" in report["missing"]
    assert "Python" in report["matched"]
    assert report["required_total"] == 2, "only the two real asks are counted"


def test_culture_copy_does_not_order_the_skills_row_either() -> None:
    """`jd_skill_order` shares the same filter, so the printed skills row is
    ordered by what the posting asks for rather than by its values list."""
    order = jd_skill_order(POSTING)

    assert not any(_is_culture_fluff(term) for term in order), order
    assert order[0] == "Python"
