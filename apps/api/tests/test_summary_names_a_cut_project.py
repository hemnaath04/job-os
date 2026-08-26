"""The lede cited a project the page had just cut.

#45 makes a spilling page fit by removing the weakest project and reassembling,
but the summary is written against the selection as it stood BEFORE the cut. A
real post-deploy run opened by positioning him "via BedRocked's LLM and
classification work" on a page BedRocked had just been cut from.

That is worse than the spilling page it came from. A resume running long is
untidy; a resume whose first line names work it does not contain reads as
describing someone else, and it is the one line every reader reads first.

Checked against the assembled page, not against the cut list alone, so a name
the page still carries for some other reason is not a false positive.
"""
from __future__ import annotations

from job_os.services.tailor import (
    TailorFact,
    _project_short_name,
    _summary_names_absent_project,
)

BEDROCKED = TailorFact(
    id="bedrocked", kind="project", title="BedRocked — Civic Sewer-Sequencing Platform"
)
CLAIMFARM = TailorFact(
    id="claimfarm", kind="project", title="ClaimFarm: Agentic Crop-Insurance AI"
)

# The page that actually shipped: BedRocked cut, ClaimFarm kept.
PAGE = {
    "projects": [
        {
            "name": "ClaimFarm: Agentic Crop-Insurance AI",
            "highlights": ["Built an AI agent that files a crop-insurance claim."],
        }
    ]
}
REAL_SUMMARY = (
    "Backend and AI engineer positioned for LLM work via BedRocked's vision "
    "and classification pipeline."
)


def test_the_real_run_that_found_this() -> None:
    assert (
        _summary_names_absent_project(
            REAL_SUMMARY, cut=[BEDROCKED], json_resume=PAGE
        )
        == "BedRocked"
    )


def test_a_summary_about_what_is_on_the_page_is_left_alone() -> None:
    summary = "Backend and AI engineer, most recently building ClaimFarm."
    assert (
        _summary_names_absent_project(summary, cut=[BEDROCKED], json_resume=PAGE)
        is None
    )


def test_a_cut_name_the_page_still_shows_is_not_a_false_positive() -> None:
    """The question is whether the reader can find it, not which fact it came from."""
    page = {
        "projects": [
            {"name": "ClaimFarm", "highlights": ["Ported the BedRocked scorer."]}
        ]
    }
    assert (
        _summary_names_absent_project(
            REAL_SUMMARY, cut=[BEDROCKED], json_resume=page
        )
        is None
    )


def test_the_possessive_the_real_summary_used_still_matches() -> None:
    """It said "BedRocked's", so a naive word match would have missed it."""
    assert "BedRocked's" in REAL_SUMMARY


def test_nothing_cut_means_nothing_to_check() -> None:
    assert _summary_names_absent_project(REAL_SUMMARY, cut=[], json_resume=PAGE) is None


def test_no_summary_is_not_a_crash() -> None:
    assert _summary_names_absent_project(None, cut=[BEDROCKED], json_resume=PAGE) is None
    assert _summary_names_absent_project("", cut=[BEDROCKED], json_resume=PAGE) is None


def test_the_name_is_the_part_before_the_subtitle() -> None:
    """His real titles. The summary will use the name, not the description."""
    assert _project_short_name("BedRocked — Civic Sewer-Sequencing Platform") == "BedRocked"
    assert _project_short_name("ClaimFarm: Agentic Crop-Insurance AI") == "ClaimFarm"
    assert _project_short_name("job.os — AI Job-Search Platform") == "job.os"
    assert (
        _project_short_name("RoleReveal: AI Job-Match Chrome Extension (published)")
        == "RoleReveal"
    )


def test_a_dotted_name_is_matched_as_a_word() -> None:
    """"job.os" has to survive the word-boundary check that protects C++ and CI/CD."""
    jobos = TailorFact(id="jobos", kind="project", title="job.os — AI Job-Search Platform")
    summary = "Backend engineer who built job.os end to end."
    assert (
        _summary_names_absent_project(summary, cut=[jobos], json_resume=PAGE) == "job.os"
    )


def test_a_two_letter_name_is_left_alone() -> None:
    """Too short to be sure the summary means the project and not the word."""
    tiny = TailorFact(id="ab", kind="project", title="AB")
    assert (
        _summary_names_absent_project("Worked on AB testing.", cut=[tiny], json_resume=PAGE)
        is None
    )
