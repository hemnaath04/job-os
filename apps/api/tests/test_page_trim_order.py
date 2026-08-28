"""What a page sheds first when it will not fit.

Cheapest first: a keyword the posting never asked about costs a word and says
nothing, the summary is a written sentence about the candidate's work and the
first thing a reader reads, and a project is the work itself.

These ran the other way round. A real AMD ML/AI co-op run on 2026-08-28
deleted the summary for space while keeping all forty-three skill keywords,
which occupied roughly a quarter of the page. The same run then flagged
`thin_page(8 bullets)`: the page was full, but full of keywords rather than
evidence.
"""
from __future__ import annotations

from job_os.services.resume_writing import MAX_PAGE_LINES, estimated_page_lines
from job_os.services.tailor import _drop_summary, _trim_skills_to_fit


def _overfull_document() -> dict:
    """A page over budget, with a summary and a lot of unasked-for keywords."""
    return {
        "basics": {
            "name": "A Candidate",
            "summary": (
                "Backend engineer who builds evidence-backed tooling, with two "
                "years shipping test automation for a global rideshare platform."
            ),
        },
        "work": [
            {
                "name": "EPAM Systems",
                "position": "Test Automation Engineer",
                "highlights": [
                    f"Did a substantial piece of work number {i}, " * 4 for i in range(3)
                ],
            }
        ],
        "projects": [
            {
                "name": f"Project {i}",
                "highlights": ["Built a thing worth describing at length here."],
            }
            for i in range(7)
        ],
        "education": [{"institution": "A University", "studyType": "MS", "area": "CS"}],
        "skills": [
            {"name": f"Group {g}", "keywords": [f"Keyword{g}{k}" for k in range(12)]}
            for g in range(7)
        ],
    }


def test_the_page_is_actually_over_budget() -> None:
    # Guards the fixture: if this stops being over budget, the tests below stop
    # testing anything.
    assert estimated_page_lines(_overfull_document()) > MAX_PAGE_LINES


def test_shedding_unasked_keywords_is_tried_before_the_summary() -> None:
    document = _overfull_document()
    dropped = _trim_skills_to_fit(document, [], MAX_PAGE_LINES)

    assert dropped > 0, "no keyword was shed, so the summary would go instead"
    assert document["basics"]["summary"], "the summary must survive a skills trim"


def test_the_summary_still_goes_when_keywords_alone_cannot_save_it() -> None:
    # The order is a preference, not a refusal: a page that will not fit even
    # with every spare keyword gone still has to give something up.
    document = _overfull_document()
    document["projects"].extend(
        {
            "name": f"Extra {i}",
            "highlights": ["Another substantial line of evidence here."],
        }
        for i in range(12)
    )
    _trim_skills_to_fit(document, [], MAX_PAGE_LINES)
    if estimated_page_lines(document) > MAX_PAGE_LINES:
        assert _drop_summary(document) is True
        assert not document["basics"].get("summary")
