"""The page budget belongs to the template, not to resumes in general.

Both halves came off real renders of one AMD ML/AI co-op document on
2026-08-28. The estimator handed every template the same number and the same
section list, and was wrong in both directions at once:

  - The six Typst templates carry 47 lines comfortably; the tightest of them,
    jakes, only spills at 49. husky goes to two pages somewhere above 42.
  - husky has no resume-level summary section at all. Its sections are
    Education, Technical Skills, Professional Experience, Projects.

So on husky the trimmer counted summary lines the page would never draw, then
deleted the candidate's summary to recover them. Two runs did exactly that,
freed nothing, and still came out two pages.
"""
from __future__ import annotations

from job_os.services.resume_writing import (
    DEFAULT_PAGE_SHAPE,
    estimated_page_lines,
    page_shape,
)

SUMMARY = (
    "Backend engineer building evidence-backed tooling, with two years shipping "
    "test automation for a global rideshare platform and four shipped AI projects."
)


def _document() -> dict:
    return {
        "basics": {"name": "A Candidate", "summary": SUMMARY},
        "work": [{"name": "EPAM", "position": "Engineer", "highlights": ["Did a thing."]}],
    }


def test_husky_is_budgeted_tighter_than_the_default() -> None:
    assert page_shape("husky").max_lines < DEFAULT_PAGE_SHAPE.max_lines


def test_an_unknown_template_takes_the_default() -> None:
    # A template with no measured shape must not silently get husky's tighter
    # budget, which would trim pages that fit perfectly well.
    assert page_shape("jakes") == DEFAULT_PAGE_SHAPE
    assert page_shape(None) == DEFAULT_PAGE_SHAPE
    assert page_shape("some-template-added-later") == DEFAULT_PAGE_SHAPE


def test_husky_does_not_charge_the_page_for_a_summary_it_cannot_draw() -> None:
    document = _document()
    generic = estimated_page_lines(document)
    husky = estimated_page_lines(document, "husky")

    assert husky < generic, "husky counted summary lines it will never render"
    # And the difference is exactly the summary: strip it and the two agree.
    without = {**document, "basics": {"name": "A Candidate"}}
    assert estimated_page_lines(without, "husky") == husky


def test_a_template_with_a_summary_still_counts_it() -> None:
    document = _document()
    without = {**document, "basics": {"name": "A Candidate"}}
    assert estimated_page_lines(document, "jakes") > estimated_page_lines(without, "jakes")
