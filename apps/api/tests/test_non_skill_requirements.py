"""Terms a resume can never match must not count as must-haves.

The score is `met / total`, so anything in the denominator that no resume
could ever satisfy deflates it permanently and, worse, feeds the repair pass a
list of gaps to chase that no rewrite can close.

Every case here came off one real run: an AMD ML/AI co-op posting on
2026-08-28, which scored 9 of 27 must-haves. Four of those 27 were the posting
describing itself rather than asking for anything. The run's own gap questions
already knew, saying "role-type keyword, not a skill to match" and "employer
name from posting, not candidate experience", and scored them as misses anyway.
"""
from __future__ import annotations

import pytest

from job_os.services.tailor import _jd_requirements

# The keywords list from that posting, verbatim.
AMD_KEYWORDS = [
    "Machine Learning",
    "Artificial Intelligence",
    "AI",
    "ML",
    "Intern",
    "Co-op",
    "AMD",
    "Computer Vision",
    "Data Science",
    "Cloud",
    "Deep Learning",
]


def _required(jd: dict) -> list[str]:
    requirements, _prose, _excluded = _jd_requirements(jd)
    return [req.label for req in requirements if not req.preferred]


@pytest.mark.parametrize("term", ["Intern", "Co-op"])
def test_the_role_type_is_not_a_skill_to_match(term: str) -> None:
    """`internships?` caught the noun but not the bare word a keywords list
    carries, so an internship posting scored "Intern" as a skill the candidate
    had failed to demonstrate."""
    assert term not in _required({"keywords": AMD_KEYWORDS})


def test_parse_debris_is_not_a_requirement() -> None:
    """Splitting "Familiarity with cloud (e.g., AWS, GCP, Azure)" on its
    punctuation leaves "e.g" behind as its own must-have."""
    jd = {"required_skills": ["Familiarity with cloud (e.g., AWS, GCP, Azure)"]}
    labels = _required(jd)
    assert "e.g" not in labels
    assert not any(label.strip(". ").lower() in ("e.g", "i.e", "etc") for label in labels)
    # The real skills inside that sentence still have to survive the cleanup.
    joined = " ".join(labels)
    assert "AWS" in joined and "GCP" in joined and "Azure" in joined


def test_the_real_skills_in_that_posting_still_count() -> None:
    """The guard must not eat the requirements the posting actually makes."""
    labels = " ".join(_required({"keywords": AMD_KEYWORDS}))
    for kept in ("Machine Learning", "Computer Vision", "Deep Learning"):
        assert kept in labels


def test_internal_is_not_mistaken_for_intern() -> None:
    # Word-boundary matched, the same care `firm`/`firmware` already gets.
    assert "Internal Tooling" in _required({"keywords": ["Internal Tooling"]})
