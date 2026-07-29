"""The writing rules the tailored resume is held to.

Every case here is something a real run produced against the user's own profile,
so these tests are a record of what went out the door before they existed.
"""
from __future__ import annotations

from job_os.services.resume_writing import (
    bullet_flags,
    dedupe_bullets,
    document_quality_flags,
    normalize_dashes,
    section_flags,
    similarity,
)

# The real pair: one EPAM fact and its re-imported twin, describing the same
# AI-agent work in different words. Both reached the page.
AGENT_LONG = (
    "In the latter half of the role, was part of a team building an AI agent "
    "that generates test cases directly from user stories, SRS, and FSDs, built "
    "on EPAM's internal LLM and in-house agent-creation platform. Demoed "
    "end-to-end; pending senior approval at the time I left."
)
AGENT_SHORT = (
    "Worked on a team building an AI agent that generates test cases from user "
    "stories, SRS, and FSDs on EPAM's internal LLM platform; demoed end-to-end."
)


def test_a_reworded_bullet_is_recognised_as_the_same_accomplishment() -> None:
    assert similarity(AGENT_LONG, AGENT_SHORT) > 0.6


def test_two_wordings_of_one_accomplishment_collapse_to_the_richer_one() -> None:
    kept = dedupe_bullets([AGENT_SHORT, AGENT_LONG])
    assert kept == [AGENT_LONG]


def test_distinct_accomplishments_both_survive() -> None:
    kept = dedupe_bullets(
        [
            "Migrated legacy suites to Cucumber and TestNG and tightened Jenkins CI/CD.",
            "Scored 2,404 sewer segments for dig-readiness by fusing scan data with GIS.",
        ]
    )
    assert len(kept) == 2


def test_em_dashes_leave_the_document_without_stray_punctuation() -> None:
    assert (
        normalize_dashes("Northeastern University — Khoury College")
        == "Northeastern University, Khoury College"
    )
    # A title reads better with a colon, which is how the user writes it.
    assert (
        normalize_dashes("BedRocked — Civic Sewer-Sequencing Platform", separator=": ")
        == "BedRocked: Civic Sewer-Sequencing Platform"
    )
    # The template's own replace produced "FSDs , built on"; a comma followed by
    # punctuation must not survive either.
    assert normalize_dashes("shipped it — . done") == "shipped it. done"
    # Hyphens inside words are not dashes and must be left alone.
    assert normalize_dashes("cents-per-asset inference at 0-100") == (
        "cents-per-asset inference at 0-100"
    )
    assert normalize_dashes(None) is None


def test_the_real_overlong_first_person_bullet_is_flagged() -> None:
    flags = bullet_flags(AGENT_LONG)
    assert any(flag.startswith("too_long") for flag in flags)
    assert "first_person" in flags
    assert "weak_opener" in flags


def test_jd_padding_is_caught_only_when_the_evidence_lacks_it() -> None:
    source = (
        "Ran daily root-cause analysis with developers on failing tests, raising "
        "coverage on the pricing engine."
    )
    padded = (
        "Ran daily root-cause analysis directly with engineers and product owners "
        "on failing tests, raising pricing-engine coverage and shortening "
        "time-to-fix on regressions in a fast-paced environment."
    )
    flags = bullet_flags(padded, source_text=source)
    assert any(flag.startswith("jd_padding") for flag in flags)
    assert any(flag.startswith("inflated_rewrite") for flag in flags)
    # The same phrase already in the evidence is the candidate's own wording, so
    # keeping it is not stuffing.
    assert not any(
        flag.startswith("jd_padding")
        for flag in bullet_flags(padded, source_text=padded)
    )


def test_a_good_bullet_has_nothing_to_say_about_it() -> None:
    assert bullet_flags(
        "Migrated legacy suites to Cucumber and TestNG and tightened Jenkins "
        "CI/CD, cutting flaky failures."
    ) == []


def test_three_bullets_opening_with_built_are_flagged() -> None:
    flags = section_flags(
        [
            "Built a dig-readiness score for 2,404 sewer segments.",
            "Built a parallel fetcher with atomic MongoDB worker claims.",
            "Hardened the FastAPI backend behind nginx and TLS.",
        ]
    )
    assert any(flag.startswith("repeated_opening_verb") for flag in flags)


def test_a_role_with_seven_bullets_is_flagged_where_it_lives() -> None:
    document = {
        "work": [
            {
                "name": "EPAM Systems",
                "position": "Software Test Automation Engineer",
                "highlights": [AGENT_LONG, AGENT_SHORT] + [
                    f"Wrote suite number {n}." for n in range(5)
                ],
            }
        ]
    }
    flags = document_quality_flags(document)
    key = "work: Software Test Automation Engineer"
    assert key in flags
    assert "near_duplicate_bullets" in flags[key]
    assert any(flag.startswith("too_many_bullets") for flag in flags[key])


def test_a_clean_document_reports_nothing() -> None:
    document = {
        "work": [
            {
                "position": "Engineer",
                "highlights": [
                    "Migrated legacy suites to Cucumber and TestNG.",
                    "Wrote automated tests for a Go pricing engine.",
                ],
            }
        ],
        "projects": [{"name": "One", "highlights": ["Shipped a scheduler."]}],
        "skills": [{"name": "Languages", "keywords": ["Python"]}],
    }
    assert document_quality_flags(document) == {}
