"""A skill is unevidenced when nothing backs it, not when this page skipped it.

The rule asked "does a bullet on this page demonstrate it", which is the right
question for a resume nobody tailored: an uploaded page is all the evidence
there is. It is the wrong question for a tailored one.

A one-page resume prints three projects and a dozen bullets out of a whole
career. A truthful, verified skills list will always name things those twelve
bullets had no room to show, and every one was flagged and charged: thirty-four
on a real run, and `unevidenced_skill` is a substantive flag, so a complete
vault made the review fail. The more honest the profile, the worse it scored.

These pin the new question, and that the case the check was written for still
fires.
"""
from __future__ import annotations

from job_os.services.resume_writing import document_quality_flags, unevidenced_skills

# His real testing stack, none of which the one project on this page shows.
TESTING = ["Selenium", "TestNG", "Cucumber", "Pytest", "Jenkins", "Postman"]

PAGE = {
    "projects": [
        {
            "name": "ClaimFarm",
            "highlights": [
                "Built an AI agent that turns a crop photo into a filed claim."
            ],
        }
    ],
    "skills": [{"name": "Testing & CI/CD", "keywords": TESTING}],
}


def test_a_verified_skill_this_page_had_no_room_for_is_not_a_defect() -> None:
    assert unevidenced_skills(PAGE, vault_evidence=TESTING) == []


def test_the_old_question_flagged_every_one_of_them() -> None:
    """Kept as the record of what changed, and of what an upload review still does."""
    assert unevidenced_skills(PAGE) == sorted(TESTING)


def test_a_skill_nothing_in_the_vault_backs_is_still_flagged() -> None:
    """The interview-collapsing case, and the whole reason the check survives."""
    page = {
        **PAGE,
        "skills": [{"name": "Testing & CI/CD", "keywords": [*TESTING, "Kubernetes"]}],
    }
    assert unevidenced_skills(page, vault_evidence=TESTING) == ["Kubernetes"]


def test_a_bullet_in_the_vault_backs_a_skill_the_page_never_printed() -> None:
    """Evidence is what the profile holds, not only what it lists as a skill."""
    vault = [
        "Migrated legacy test suites to Cucumber and TestNG, tightening CI/CD "
        "integration and reducing flaky failures across regression runs."
    ]
    assert unevidenced_skills(
        {**PAGE, "skills": [{"name": "T", "keywords": ["Cucumber", "TestNG"]}]},
        vault_evidence=vault,
    ) == []


def test_the_page_still_counts_as_its_own_evidence() -> None:
    """A skill this page demonstrates needs nothing else behind it."""
    page = {
        "projects": [{"name": "One", "highlights": ["Built a service in Go."]}],
        "skills": [{"name": "Languages", "keywords": ["Go"]}],
    }
    assert unevidenced_skills(page, vault_evidence=[]) == []


def test_an_uploaded_resume_review_is_unchanged() -> None:
    """`resume_engine` passes no vault, because for an upload there is none."""
    flags = document_quality_flags(PAGE).get("skills") or []
    assert any(flag.startswith("unevidenced_skill") for flag in flags)


def test_the_tailored_review_stops_failing_a_truthful_skills_list() -> None:
    flags = document_quality_flags(PAGE, vault_evidence=TESTING).get("skills") or []
    assert not any(flag.startswith("unevidenced_skill") for flag in flags)
