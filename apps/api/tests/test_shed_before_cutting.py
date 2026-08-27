"""A page sheds cheap lines before it sheds a project.

#45 went straight to removing a project. His real page came in at 55 lines
against a budget of 47, six over, and the two fattest non-project blocks were a
35-word summary and 43 skill keywords across six groups. So the loop was
prepared to spend a whole project, three bullets of real evidence, to keep a
sentence and thirty keywords the posting never asked about.

A project is the work. A summary is a sentence about the work. A keyword the
posting did not ask about is a word. The order they come off in should follow
that, and now it does.
"""
from __future__ import annotations

from job_os.services.resume_writing import MAX_PAGE_LINES, estimated_page_lines
from job_os.services.tailor import _drop_summary, _jd_requirements, _trim_skills_to_fit

AMEX = {
    "required_skills": ["Python", "LLM APIs", "prompt-based interactions", "model training"],
    "preferred_skills": ["machine learning", "generative AI", "embeddings", "agentic AI"],
    "technologies": ["Python", "LLM APIs", "embeddings", "AI agents"],
    "keywords": ["Machine Learning", "Generative AI", "LLM", "AI Agents"],
    "qualifications": [],
}


def page(summary: str = "", keywords_per_group: int = 8) -> dict:
    return {
        "basics": {"summary": summary},
        "work": [{"position": "Engineer", "highlights": ["Did a thing."] * 4}],
        "projects": [
            {"name": f"Project {i}", "highlights": ["Built a thing."] * 2}
            for i in range(3)
        ],
        "skills": [
            {
                "name": group,
                "keywords": [f"{group}Skill{n}" for n in range(keywords_per_group)],
            }
            for group in ("Languages", "AI", "Backend", "Testing", "Infra", "Docs")
        ],
    }


def test_the_summary_goes_before_anything_else() -> None:
    document = page(summary=" ".join(["word"] * 35))
    before = estimated_page_lines(document)
    assert _drop_summary(document) is True
    assert estimated_page_lines(document) < before


def test_dropping_a_summary_that_is_not_there_is_not_a_change() -> None:
    document = page()
    assert _drop_summary(document) is False
    assert _drop_summary({"basics": {}}) is False
    assert _drop_summary({}) is False


def test_skills_are_shed_only_until_the_page_fits() -> None:
    """Not "delete everything unmatched". Shed what the page needs, no more."""
    document = page()
    # Genuinely over budget, which is the only state where shedding applies.
    document["work"] = [{"position": "Engineer", "highlights": ["Did a thing."] * 30}]
    assert estimated_page_lines(document) > MAX_PAGE_LINES
    requirements, _prose, _excluded = _jd_requirements(AMEX)
    total = sum(len(g["keywords"]) for g in document["skills"])
    dropped = _trim_skills_to_fit(document, requirements, MAX_PAGE_LINES)
    kept = sum(len(g.get("keywords") or []) for g in document.get("skills") or [])
    assert dropped > 0
    assert kept == total - dropped
    assert estimated_page_lines(document) <= MAX_PAGE_LINES


def test_a_page_that_already_fits_loses_no_skills() -> None:
    document = page(keywords_per_group=1)
    requirements, _prose, _excluded = _jd_requirements(AMEX)
    assert estimated_page_lines(document) <= MAX_PAGE_LINES
    assert _trim_skills_to_fit(document, requirements, MAX_PAGE_LINES) == 0


def test_the_relevant_keywords_are_the_ones_that_survive() -> None:
    """The whole point: an AI posting keeps the AI skills and sheds the rest."""
    document = page()
    document["skills"] = [
        {"name": "AI / ML", "keywords": ["LLM Integration", "Embeddings", "AI Agents"]},
        {"name": "Testing", "keywords": [f"QaTool{n}" for n in range(20)]},
    ]
    requirements, _prose, _excluded = _jd_requirements(AMEX)
    _trim_skills_to_fit(document, requirements, MAX_PAGE_LINES)
    surviving = {k for g in document["skills"] for k in g["keywords"]}
    for keyword in ("LLM Integration", "Embeddings", "AI Agents"):
        assert keyword in surviving, f"{keyword} is what the posting asked for"


def test_the_skills_floor_still_holds() -> None:
    """#44's floor: a block gutted below this stops being a skills block."""
    document = page()
    document["work"] = [{"position": "E", "highlights": ["Did a thing."] * 40}]
    requirements, _prose, _excluded = _jd_requirements(AMEX)
    _trim_skills_to_fit(document, requirements, MAX_PAGE_LINES)
    kept = sum(len(g.get("keywords") or []) for g in document.get("skills") or [])
    assert kept >= 8, "a page that cannot fit still keeps a usable skills block"


def test_his_real_page_fits_without_losing_a_project() -> None:
    """His real Amex draft: 55 lines against 47, six over.

    Rebuilt here from its real shape rather than read from disk, so it runs in
    CI: a 35-word summary, 43 skill keywords across six groups, one role with
    four bullets, three projects with five bullets between them, two degrees and
    a certificate. The fix is the summary and the tail of the skills, and no
    project has to go.
    """
    document = {
        "basics": {"summary": " ".join(["word"] * 35)},
        "work": [
            {
                "position": "Test Automation Engineer",
                "highlights": [" ".join(["word"] * 26)] * 5,
            }
        ],
        "projects": [
            {"name": "ClaimFarm", "highlights": [" ".join(["word"] * 26)] * 3},
            {"name": "BedRocked", "highlights": [" ".join(["word"] * 26)] * 3},
            {"name": "Infant Cry", "highlights": [" ".join(["word"] * 24)]},
        ],
        "education": [{"institution": "Northeastern"}, {"institution": "Sathyabama"}],
        "certificates": [{"name": "Machine Learning"}],
        "skills": [
            {"name": "Languages", "keywords": [f"Lang{n}" for n in range(6)]},
            {"name": "AI / ML", "keywords": ["LLM Integration", "Embeddings", "AI Agents"]
             + [f"Ai{n}" for n in range(8)]},
            {"name": "Backend & Data", "keywords": [f"Be{n}" for n in range(9)]},
            {"name": "Testing & CI/CD", "keywords": [f"Qa{n}" for n in range(7)]},
            {"name": "Infrastructure", "keywords": [f"Infra{n}" for n in range(8)]},
            {"name": "Docs", "keywords": [f"Doc{n}" for n in range(2)]},
        ],
    }
    requirements, _prose, _excluded = _jd_requirements(AMEX)
    projects_before = len(document["projects"])
    assert estimated_page_lines(document) > MAX_PAGE_LINES, "the fixture must be over"

    _drop_summary(document)
    _trim_skills_to_fit(document, requirements, MAX_PAGE_LINES)

    assert estimated_page_lines(document) <= MAX_PAGE_LINES
    assert len(document["projects"]) == projects_before, "no project was spent"
    surviving = {k for g in document["skills"] for k in g["keywords"]}
    for keyword in ("LLM Integration", "Embeddings", "AI Agents"):
        assert keyword in surviving
