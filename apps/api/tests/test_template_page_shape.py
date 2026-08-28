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


def test_husky_keeps_its_summary_where_the_generic_budget_would_take_it() -> None:
    """The end-to-end point of the whole change.

    A page over the generic budget but carrying a summary husky cannot draw
    must not lose that summary: on husky those lines are not on the page, so
    deleting them frees nothing. Two production runs did it anyway.
    """
    from job_os.services.tailor import _drop_summary, _trim_skills_to_fit

    document = {
        "basics": {"name": "A Candidate", "summary": SUMMARY},
        "work": [
            {
                "name": "EPAM",
                "position": "Engineer",
                "highlights": [
                    f"Did a substantial piece of work number {i}, " * 4 for i in range(3)
                ],
            }
        ],
        "projects": [
            {"name": f"Project {i}", "highlights": ["Built a thing worth describing here."]}
            for i in range(7)
        ],
        "education": [{"institution": "A University", "studyType": "MS", "area": "CS"}],
        "skills": [
            {"name": f"Group {g}", "keywords": [f"Keyword{g}{k}" for k in range(12)]}
            for g in range(7)
        ],
    }
    shape = page_shape("husky")
    _trim_skills_to_fit(document, [], shape.max_lines, "husky")

    # husky has no summary section, so the trimmer must never reach for it.
    if not shape.renders_summary:
        assert document["basics"]["summary"] == SUMMARY
    else:  # pragma: no cover - guards the table changing under this test
        _drop_summary(document)


def test_the_over_page_flag_uses_the_template_budget_too() -> None:
    """The flag has to agree with the trimmer, or the report contradicts it.

    Flagging husky against 47 calls a page fine at a length that renders two,
    which is how a run reported no page problem and produced a second page.
    """
    from job_os.services.resume_writing import document_quality_flags

    document = {
        "basics": {"name": "A Candidate"},
        "work": [
            {
                "name": "EPAM",
                "position": "Engineer",
                "highlights": [
                    f"A substantial line of real evidence number {i}." for i in range(4)
                ],
            }
        ],
        "projects": [
            {"name": f"Project {i}", "highlights": ["Built a thing worth describing here."]}
            for i in range(9)
        ],
        "education": [{"institution": "A University", "studyType": "MS", "area": "CS"}],
        "skills": [
            {"name": f"Group {g}", "keywords": [f"Keyword{g}{k}" for k in range(10)]}
            for g in range(6)
        ],
    }
    husky_page = document_quality_flags(document, template_key="husky").get("page", [])
    default_page = document_quality_flags(document).get("page", [])

    husky_over = [f for f in husky_page if f.startswith("over_page")]
    default_over = [f for f in default_page if f.startswith("over_page")]
    if husky_over:
        # Whatever it reports, it must report husky's budget, not the generic one.
        assert f"of {page_shape('husky').max_lines} lines" in husky_over[0]
    # And husky is the stricter of the two: it can never miss a page the
    # default budget already considers over.
    assert not (default_over and not husky_over)


def test_husky_renders_through_typst_with_its_own_face() -> None:
    """The co-op template moved off Tectonic, and the move needs its font.

    `missing_fonts` returns nothing for a template it has no requirement for,
    so an unlisted template passes it vacuously. husky is listed now, which is
    what makes this assertion mean something: TeX Gyre Termes is really there
    and Typst is not quietly substituting a different face.
    """
    from job_os.services import typst_render
    from job_os.services.latex_catalog import builtin

    assert builtin("husky").typst_ready is True
    assert typst_render.has_builtin("husky") is True
    assert typst_render.missing_fonts("husky") == []
    assert "TeX Gyre Termes" in typst_render.available_font_families("husky")
