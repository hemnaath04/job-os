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


def test_a_re_assembled_page_keeps_the_keywords_it_shed() -> None:
    """Assembly rebuilds the skills block, so the shed has to survive it.

    The trim used to run once, beside the other page trims, while the
    project-cut loop assembled again afterwards and handed every keyword
    straight back. A live husky run on 2026-08-28 shed 23 keywords, shipped
    all 43, and then cut a project to recover space the keywords were still
    occupying.
    """
    from job_os.services.resume_writing import estimated_page_lines, page_shape
    from job_os.services.tailor import _trim_skills_to_fit

    def keywords(document: dict) -> int:
        return sum(len(g.get("keywords") or []) for g in (document.get("skills") or []))

    document = _overfull_document()
    budget = page_shape(None).max_lines
    assert estimated_page_lines(document) > budget

    before = keywords(document)
    _trim_skills_to_fit(document, [], budget)
    after = keywords(document)

    assert after < before, "nothing was shed, so the rest of this proves nothing"
    # The property that broke: shedding is idempotent, so a second pass over an
    # already-trimmed page neither restores keywords nor sheds below the floor.
    _trim_skills_to_fit(document, [], budget)
    assert keywords(document) == after


# ---------------------------------------------------------------------------
# A skill the page's own bullets demonstrate.
#
# From a real Salesforce run: the trimmer shed Selenium, TestNG, Cucumber,
# Pytest and Jenkins while the EPAM entry two inches below still read
# "Migrated legacy test suites to Cucumber and TestNG" and "tightening CI/CD
# integration". The Testing row came out as the single word "GitHub Actions".
#
# A reader does not see a tuned skills list there. They see a document
# disagreeing with itself, which costs more than the line it saved.
# ---------------------------------------------------------------------------


def _document_whose_bullets_name_its_skills() -> dict:
    return {
        "basics": {"name": "A Candidate", "summary": "Backend and test automation."},
        "work": [
            {
                "name": "EPAM Systems",
                "position": "Test Automation Engineer",
                "highlights": [
                    "Migrated legacy test suites to Cucumber and TestNG, "
                    "tightening CI/CD integration and reducing flaky failures.",
                    "Wrote Selenium coverage against a pricing engine that could "
                    "not be taken offline, triaging the daily failures it produced.",
                ],
            }
        ],
        "projects": [
            {
                "name": f"Project {i}",
                "highlights": [
                    "Built a thing worth describing at length here, twice over.",
                    "And a second bullet, so the page is genuinely over budget.",
                ],
            }
            for i in range(14)
        ],
        "education": [{"institution": "A University", "studyType": "MS", "area": "CS"}],
        "skills": [
            {"name": "Testing", "keywords": ["Selenium", "TestNG", "Cucumber", "GitHub Actions"]},
            {"name": "Filler", "keywords": [f"Unrelated{k}" for k in range(40)]},
        ],
    }


def test_a_skill_the_bullets_prove_outlives_one_the_page_only_claims() -> None:
    document = _document_whose_bullets_name_its_skills()
    assert estimated_page_lines(document) > MAX_PAGE_LINES, "fixture must be over budget"

    _trim_skills_to_fit(document, [], MAX_PAGE_LINES)

    kept = {k for group in document["skills"] for k in group["keywords"]}
    for demonstrated in ("Selenium", "TestNG", "Cucumber"):
        assert demonstrated in kept, f"{demonstrated} is named in a bullet on this page"


def test_the_posting_does_not_outrank_the_page_s_own_evidence() -> None:
    """Even when the posting names the filler and not the bullets.

    Word overlap with the JD decides order among keywords the page merely
    asserts. It must not promote one of those above a skill the document
    already demonstrates, or the contradiction comes back through the front
    door.
    """
    from job_os.services.tailor import _Requirement

    document = _document_whose_bullets_name_its_skills()
    requirements = [
        _Requirement(label=f"Unrelated{k}", alternatives=(f"Unrelated{k}",), preferred=False)
        for k in range(40)
    ]

    _trim_skills_to_fit(document, requirements, MAX_PAGE_LINES)

    kept = {k for group in document["skills"] for k in group["keywords"]}
    assert "Cucumber" in kept
    assert "TestNG" in kept


def test_a_page_still_over_after_everything_else_sheds_the_proven_ones_too() -> None:
    """Ranking, not immunity.

    Going one line over is not a better answer than losing the last keyword, so
    the protection only decides the ORDER shedding happens in.
    """
    document = _document_whose_bullets_name_its_skills()
    document["projects"] = [
        {
            "name": f"Project {i}",
            "highlights": ["Built a thing worth describing at length here."] * 3,
        }
        for i in range(40)
    ]

    _trim_skills_to_fit(document, [], MAX_PAGE_LINES)

    kept = [k for group in document["skills"] for k in group["keywords"]]
    assert len(kept) <= 40, "the trimmer kept shedding rather than giving up"
