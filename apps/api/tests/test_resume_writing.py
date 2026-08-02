"""The writing rules the tailored resume is held to.

Every case here is something a real run produced against the user's own profile,
so these tests are a record of what went out the door before they existed.
"""
from __future__ import annotations

from job_os.services.resume_writing import (
    bullet_flags,
    dedupe_bullets,
    document_quality_flags,
    estimated_page_lines,
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


def test_two_bullets_closing_the_same_way_are_flagged() -> None:
    """Neither is a duplicate of the other, but the shared clause reads as machine work.

    The real pair: an EPAM role whose second and third bullets both ended
    "adding regression coverage as pricing rules shipped".
    """
    flags = section_flags(
        [
            "Worked on the Fares team's Go test suite, triaging daily failures and "
            "adding regression coverage as pricing rules shipped.",
            "Migrated legacy suites to Cucumber and TestNG and tightened Jenkins "
            "CI/CD, adding regression coverage as pricing rules shipped.",
        ]
    )
    assert any(flag.startswith("repeated_phrase") for flag in flags)


def test_bullets_that_merely_share_vocabulary_are_not_flagged() -> None:
    assert not any(
        flag.startswith("repeated_phrase")
        for flag in section_flags(
            [
                "Wrote automated tests for a Go pricing engine.",
                "Migrated legacy suites to Cucumber and TestNG.",
            ]
        )
    )


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
                    "Investigated failing tests daily with developers.",
                    "Trained new joiners on the internal tooling.",
                ],
            }
        ],
        "projects": [
            {
                "name": "One",
                "highlights": [
                    "Shipped a scheduler.",
                    "Tuned the cache eviction policy.",
                    "Designed the retry semantics.",
                ],
            },
            {
                "name": "Two",
                "highlights": ["Wrote the parser.", "Deployed behind nginx."],
            },
        ],
        "skills": [{"name": "Languages", "keywords": ["Python"]}],
    }
    assert document_quality_flags(document) == {}


def test_a_resume_that_stops_short_of_the_page_is_flagged() -> None:
    """Page fill varied run to run while it lived only in the prompt.

    One pass selected three projects and eight bullets, the next two projects and
    six, and the six-bullet resume ended a third of the way up the page.
    """
    thin = {
        "work": [{"position": "Engineer", "highlights": ["Wrote the parser."]}],
        "projects": [{"name": "One", "highlights": ["Shipped a scheduler."]}],
    }
    assert "page" in document_quality_flags(thin)
    assert document_quality_flags(thin)["page"] == ["thin_page(2 bullets)"]


def test_a_full_page_is_not_flagged_as_thin() -> None:
    document = {
        "work": [
            {
                "position": "Engineer",
                "highlights": [f"Did distinct thing number {n}." for n in range(4)],
            }
        ],
        "projects": [
            {"name": "One", "highlights": [f"Built subsystem {n}." for n in range(3)]},
            {"name": "Two", "highlights": ["Wrote the parser.", "Tuned the cache."]},
        ],
    }
    assert "page" not in document_quality_flags(document)


def test_a_resume_that_spills_onto_a_second_page_is_flagged() -> None:
    """A page that overflows is not a fuller resume, it is a two-page one.

    Counting bullets cannot see this coming, which is how a real tailored run
    shipped two pages while passing every other check: 11 bullets over 4 entries
    rendered to two pages where 10 over 3 had fitted on one. The budget is
    measured in estimated rendered lines and calibrated against Tectonic.
    """
    two_rows = " ".join(["word"] * 26)
    three_rows = " ".join(["word"] * 27)
    # Four entry headings, twelve two-row bullets and one three-row bullet: 31.
    document = {
        "work": [
            {"position": "Engineer", "highlights": [three_rows, *[two_rows] * 3]}
        ],
        "projects": [
            {"name": str(n), "highlights": [two_rows] * 3} for n in range(3)
        ],
    }
    assert document_quality_flags(document)["page"] == ["over_page(31 of 30 lines)"]


def test_the_line_estimate_counts_the_entry_heading_and_the_wrap() -> None:
    single = {"work": [{"position": "Engineer", "highlights": ["Wrote the parser."]}]}
    # One heading row plus one bullet row.
    assert estimated_page_lines(single) == 2
    wrapped = {
        "work": [{"position": "Engineer", "highlights": [" ".join(["word"] * 27)]}]
    }
    # 27 words wrap onto three rows at 13 words each, under the same heading.
    assert estimated_page_lines(wrapped) == 4
