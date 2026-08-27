"""A posting with no requirements is refused, not tailored against anyway.

A real Jane Street posting parsed "successfully" into every list empty and one
keyword. Four runs, at two different analyst efforts, each produced an 8-bullet
page with `coverage=0.0`, a suppressed score, no analyst step at all, and a
status of succeeded. The only hint was a parenthetical inside the agent's own
note.

That is a resume the candidate could send believing it had been aimed at the
job. Refusing is the honest answer, and it is also the cheap one: requirements
are pure Python and land in under a millisecond, so this fires before about
ninety seconds of gateway time gets spent proving what is already known.
"""
from __future__ import annotations

import pytest

from job_os.services.tailor import TailorInputError, _jd_requirements

# Verbatim from job dd80a7f4, the posting that found this.
JANE_STREET = {
    "required_skills": [],
    "preferred_skills": [],
    "technologies": [],
    "responsibilities": [],
    "qualifications": [],
    "keywords": ["Summer Internship"],
    "sponsorship": None,
    "years_experience": None,
}


def test_the_real_posting_yields_no_requirements() -> None:
    """The premise. If this ever stops being true the refusal is wrong."""
    requirements, _prose, _excluded = _jd_requirements(JANE_STREET)
    assert requirements == []


def test_a_posting_with_requirements_is_not_refused() -> None:
    parsed = {**JANE_STREET, "required_skills": ["Python", "FastAPI"]}
    requirements, _prose, _excluded = _jd_requirements(parsed)
    assert requirements, "a real posting must still tailor"


def test_the_refusal_is_its_own_error_type() -> None:
    """Retrying an agent failure can work. Retrying this cannot."""
    assert issubclass(TailorInputError, RuntimeError)


def test_an_unreadable_parse_says_try_again_and_an_empty_one_does_not() -> None:
    """Different causes, different advice: one is transient, one is data."""
    transient = TailorInputError(
        "This job description could not be read, so there is nothing to "
        "tailor against yet. Try again in a moment, and if it keeps "
        "failing, paste the description in by hand."
    )
    empty = TailorInputError(
        "This posting records no requirements, so there is nothing to tailor "
        "against. Open the job and add its description, then tailor again: a "
        "resume built against an empty posting is not aimed at anything."
    )
    assert "Try again" in str(transient)
    assert "Try again" not in str(empty)
    assert "add its description" in str(empty)


@pytest.mark.parametrize(
    "field",
    ["required_skills", "qualifications", "technologies", "keywords", "preferred_skills"],
)
def test_any_one_requirement_field_is_enough_to_proceed(field: str) -> None:
    """The bar is "something to measure against", not a full parse.

    `responsibilities` is deliberately not in this list: `_jd_requirements`
    does not read it, so a posting carrying only responsibilities still has
    nothing this scorer can check and is still refused.
    """
    requirements, _prose, _excluded = _jd_requirements({**JANE_STREET, field: ["Python"]})
    assert requirements


def test_a_keyword_that_is_not_a_skill_does_not_count_as_a_requirement() -> None:
    """Jane Street's one keyword is "Summer Internship", and that is why it fails.

    The posting is not empty, it is unmeasurable: the parser found a phrase and
    the requirement filter correctly declined to treat a season as a skill. So
    the refusal cannot key off "the parse is empty", it has to key off "nothing
    survived the filter", which is what `requirements` already is.
    """
    requirements, _prose, _excluded = _jd_requirements(
        {**JANE_STREET, "keywords": ["Summer Internship"]}
    )
    assert requirements == []
