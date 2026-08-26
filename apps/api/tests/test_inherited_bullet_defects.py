"""A defect the page inherited from the vault is not one the writer committed.

Eleven of the fifteen bullets in this user's verified profile are over the
thirty-word cap, and seven of fifteen open with "Built". His tailored draft
printed job.os at 33/35/39 words and ClaimFarm at 46: those are his own saved
bullets, unchanged. The tailor inflated nothing.

The review could not see that. `bullet_flags` was called without the source, so
"the writer padded this" and "your saved fact is 46 words" reported in identical
words, and `_quality_penalty` charged the writer three points for each. Since
`_sanitize_selected_bullets` reverts any rewrite that adds a number or a
technology, and so correctly teaches the writer that verbatim source is the safe
answer, the loop was paying the model to depart from the verified text on almost
every bullet it printed.

These pin the attribution, that only an untouched bullet gets to claim it, and
the page-wide opener repetition that a per-entry check structurally cannot see.
"""
from __future__ import annotations

from decimal import Decimal

from job_os.services.resume_writing import (
    document_quality_flags,
    is_verbatim_source,
    page_opener_flags,
    section_flags,
)
from job_os.services.tailor import _quality_penalty

# His real bullets, at their real lengths.
JOB_OS_TRACKS = (
    "Built job.os (live at jobs.hemnaath.tech): tracks applications on a Kanban "
    "board, tailors a master resume to any posting, and crawls Greenhouse, "
    "Lever, Ashby, and SmartRecruiters overnight to score roles against a "
    "verified profile."
)
JOB_OS_ENGINE = (
    "Built a fact-grounded tailoring engine that rewrites bullets only from "
    "verified facts, with deterministic checks that strip unverified numbers, "
    "reject new employers or metrics, and cap bullet growth so it cannot invent "
    "experience or pad to a job description."
)
CLAIMFARM = (
    "Built an AI agent that turns a farmer's crop photo into a filed insurance "
    "claim in under a minute: a vision model grades damage, weather corroborates "
    "it, embeddings retrieve similar claims, and an LLM drafts a localized "
    "confirmation in 10 languages, behind a 6-signal fraud check."
)
EPAM_SHORT = (
    "Drove daily root-cause analysis with developers on failing tests, raising "
    "coverage on the pricing engine and shortening time-to-fix on regressions."
)
VAULT = [JOB_OS_TRACKS, JOB_OS_ENGINE, CLAIMFARM, EPAM_SHORT]


def flags_for(highlights: list[str], **kwargs: object) -> list[str]:
    document = {"projects": [{"name": "job.os", "highlights": highlights}]}
    return document_quality_flags(document, **kwargs).get("projects: job.os", [])


def test_his_own_forty_six_word_bullet_is_not_the_writers_fault() -> None:
    assert "too_long_verbatim(46w)" in flags_for([CLAIMFARM], verified_sources=VAULT)


def test_a_rewrite_that_came_back_long_is_still_the_writers_fault() -> None:
    """The point is attribution, not an excuse. Touch it and you own it."""
    padded = CLAIMFARM.replace("Built an AI agent", "Built an innovative AI agent")
    flags = flags_for([padded], verified_sources=VAULT)
    assert any(flag.startswith("too_long(") for flag in flags)
    assert not any(flag.startswith("too_long_verbatim") for flag in flags)


def test_a_near_match_is_a_rewrite() -> None:
    """One word off is authorship. Only an untouched bullet points at the vault."""
    assert not is_verbatim_source(CLAIMFARM.replace("filed", "submitted"), VAULT)


def test_assembly_reflowing_the_text_does_not_make_it_a_rewrite() -> None:
    """Whitespace and case survive `_normalize_document_text`; wording is what counts."""
    reflowed = "  BUILT an\n  AI agent" + CLAIMFARM[len("Built an AI agent") :] + " "
    assert is_verbatim_source(reflowed, VAULT)


def test_the_review_of_an_untailored_resume_is_unchanged() -> None:
    """`resume_engine` reviews uploads, where there is no vault to inherit from."""
    assert "too_long(46w)" in flags_for([CLAIMFARM])


def test_a_repeated_opener_carried_by_his_own_wording_is_inherited() -> None:
    flags = section_flags([JOB_OS_TRACKS, JOB_OS_ENGINE], verified_sources=VAULT)
    assert "repeated_opening_verb_verbatim(built)" in flags
    assert "repeated_opening_verb(built)" not in flags


def test_one_authored_bullet_makes_the_repetition_the_writers_again() -> None:
    """He cannot be blamed for a duplicate the writer chose to create."""
    flags = section_flags(
        [JOB_OS_TRACKS, "Built something the vault never said."],
        verified_sources=VAULT,
    )
    assert "repeated_opening_verb(built)" in flags


def test_an_inherited_defect_costs_the_pass_nothing() -> None:
    """The only move that clears it is to stop printing his verified wording."""
    assert _quality_penalty({"projects: job.os": ["too_long_verbatim(46w)"]}) == Decimal(
        "0"
    )


def test_an_authored_defect_still_costs() -> None:
    assert _quality_penalty({"projects: job.os": ["too_long(46w)"]}) > Decimal("0")


def test_the_page_sees_a_verb_that_no_single_entry_can() -> None:
    """Two "Built" in one project was flagged; five across five projects was not."""
    document = {
        "projects": [
            {"name": "job.os", "highlights": [JOB_OS_TRACKS]},
            {"name": "ClaimFarm", "highlights": [CLAIMFARM]},
            {"name": "RoleReveal", "highlights": ["Built and shipped an extension."]},
        ]
    }
    for entry in document["projects"]:
        assert not section_flags(entry["highlights"])
    assert page_opener_flags(document) == ["page_opener(built opens 3 of 3)"]


def test_a_page_that_merely_reuses_a_verb_twice_is_left_alone() -> None:
    """Across a whole resume one repeated verb is English, not a defect."""
    document = {
        "work": [{"position": "QA", "highlights": [EPAM_SHORT, "Drove a review."]}],
        "projects": [
            {"name": "a", "highlights": ["Built a thing.", "Wired a second thing."]},
            {"name": "b", "highlights": ["Trained a model.", "Replaced a service."]},
        ],
    }
    assert page_opener_flags(document) == []
