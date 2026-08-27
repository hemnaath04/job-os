"""How the tailored resume gets assembled from verified facts.

Each case is a defect a real run against the user's production profile put on the
page: a current project sorted below a finished one, eight skill rows where five
belonged, seven bullets on one role, and em dashes the global rule forbids.
"""
from __future__ import annotations

from datetime import date

from job_os.schemas.resumes import SelectedBullet, TailorAgentOutput
from job_os.services.tailor import (
    TailorBullet,
    TailorFact,
    _assemble_json_resume,
    _build_document,
    _consolidate_skills,
    _is_date_term,
    _merge_duplicate_facts,
    _quality_penalty,
)


def _project(fact_id: str, title: str, start: date, end: date | None) -> TailorFact:
    return TailorFact(
        id=fact_id, kind="project", title=title, start_date=start, end_date=end
    )


def test_an_ongoing_project_outranks_a_finished_one() -> None:
    """A missing end date means ongoing, which is the newest thing he has.

    Sorting it as date.min put a 2024 course project at the top of every
    tailored resume and buried this year's flagship work underneath it.
    """
    facts = [
        _project("old", "Infant Cry Detection", date(2024, 1, 1), date(2024, 5, 1)),
        _project("current", "BedRocked", date(2026, 6, 1), None),
    ]
    document, _ = _assemble_json_resume(
        master_json_resume={"basics": {}},
        all_facts=facts,
        selected_facts=facts,
        selected_bullets=[],
        bullets_by_fact={},
        summary_objective=None,
    )
    assert [p["name"] for p in document["projects"]] == [
        "BedRocked",
        "Infant Cry Detection",
    ]


def test_python_never_caps_how_many_selected_projects_render() -> None:
    """Ruling out a page-length cap as the cause of the thin-projects bug.

    The prompts ask the writer for 3 to 4 projects and Python caps BULLETS per
    project (`MAX_PROJECT_BULLETS`), but there is no corresponding cap on how
    many project ENTRIES render: `_facts_of("project", only_selected=True)`
    renders every fact the writer selected. If a run's page was thin on
    projects, the fix belongs in which facts get selected, not here.
    """
    facts = [
        _project("a", "job.os", date(2026, 1, 1), None),
        _project("b", "ClaimFarm", date(2025, 6, 1), date(2025, 7, 1)),
        _project("c", "RoleReveal", date(2025, 3, 1), date(2025, 4, 1)),
        _project("d", "BedRocked", date(2026, 6, 1), date(2026, 6, 14)),
    ]
    document, _ = _assemble_json_resume(
        master_json_resume={"basics": {}},
        all_facts=facts,
        selected_facts=facts,
        selected_bullets=[],
        bullets_by_fact={},
        summary_objective=None,
    )
    assert len(document["projects"]) == 4


def test_skill_categories_spelled_two_ways_become_one_row() -> None:
    groups = _consolidate_skills(
        {
            "AI / ML": ["LLM Integration", "RAG", "embeddings"],
            "AI & ML": ["AI agents", "rag", "Embeddings"],
        }
    )
    assert len(groups) == 1
    assert groups[0]["name"] == "AI / ML"
    # Same keyword, different case, appears once.
    assert groups[0]["keywords"] == ["LLM Integration", "RAG", "embeddings", "AI agents"]


def test_a_row_whose_every_keyword_is_already_listed_is_dropped() -> None:
    groups = _consolidate_skills(
        {
            "Testing & CI/CD": ["Selenium", "Pytest", "Jenkins"],
            "Infrastructure & Docs": ["Docker", "nginx"],
            "Testing, CI/CD & Infrastructure": ["Selenium", "Pytest", "Docker"],
        }
    )
    assert [g["name"] for g in groups] == ["Testing & CI/CD", "Infrastructure & Docs"]


def test_a_category_named_skills_is_relabelled_and_sorted_last() -> None:
    groups = _consolidate_skills(
        {"Skills": ["LangChain", "LoRA"], "Languages": ["Python", "Go"]}
    )
    assert [g["name"] for g in groups] == ["Languages", "Additional"]


def test_two_facts_for_one_job_become_one_fact_keeping_every_bullet() -> None:
    """The prompt must show one EPAM, not two.

    Showing both let the agent pick bullets from each, and nothing downstream
    could tell that two differently worded bullets described the same work.
    """
    facts = [
        TailorFact(
            id="rich",
            kind="experience",
            title="Junior Software Test Automation Engineer, Client: rideshare",
            org="EPAM Systems",
            start_date=date(2024, 7, 1),
            end_date=date(2025, 12, 1),
            payload={"keywords": ["Go"]},
        ),
        TailorFact(
            id="terse",
            kind="experience",
            title="Software Test Automation Engineer",
            org="EPAM Systems",
            start_date=date(2024, 7, 1),
            end_date=date(2025, 12, 1),
            location="Hyderabad, India",
        ),
    ]
    bullets = {
        "rich": [
            TailorBullet(id="b1", fact_id="rich", text="Migrated legacy suites to Cucumber."),
            TailorBullet(
                id="b2",
                fact_id="rich",
                text=(
                    "Worked on a team building an AI agent that generates test cases "
                    "from user stories, SRS, and FSDs on EPAM's internal LLM platform."
                ),
            ),
        ],
        "terse": [
            TailorBullet(
                id="b3",
                fact_id="terse",
                text=(
                    "Was part of a team building an AI agent generating test cases "
                    "from user stories, SRS and FSDs on EPAM's internal LLM platform; "
                    "demoed end to end."
                ),
            ),
            TailorBullet(id="b4", fact_id="terse", text="Tightened Jenkins CI/CD."),
        ],
    }
    merged_facts, merged_bullets = _merge_duplicate_facts(facts, bullets)
    assert len(merged_facts) == 1
    survivor = merged_facts[0]
    # Most evidence wins the scalar fields, and a field only the other variant
    # carried is filled in rather than lost.
    assert survivor.location == "Hyderabad, India"
    texts = [b.text for b in merged_bullets[survivor.id]]
    # The two AI-agent wordings collapse; the two distinct bullets survive.
    assert len(texts) == 3
    assert any("Cucumber" in t for t in texts)
    assert any("Jenkins" in t for t in texts)
    assert sum("AI agent" in t for t in texts) == 1


def test_a_role_renders_at_most_four_bullets_and_provenance_matches_the_page() -> None:
    fact = TailorFact(
        id="job",
        kind="experience",
        title="Engineer",
        org="Acme",
        start_date=date(2024, 1, 1),
        end_date=date(2025, 1, 1),
    )
    # Six genuinely distinct bullets, so nothing is dropped as a duplicate and
    # the cap is the only thing doing the cutting.
    texts = [
        "Migrated legacy suites to Cucumber and TestNG.",
        "Wrote automated tests for a Go pricing engine across city markets.",
        "Triaged daily failures and fixed flaky cases.",
        "Tightened Jenkins pipelines so regressions surfaced before release.",
        "Trained new joiners on the internal tooling.",
        "Verified multi-currency conversion behaviour per market.",
    ]
    bullets = {
        "job": [
            TailorBullet(id=f"b{n}", fact_id="job", text=text)
            for n, text in enumerate(texts)
        ]
    }
    agent = TailorAgentOutput(
        selected_fact_ids=["job"],
        selected_bullets=[
            SelectedBullet(
                fact_bullet_id=f"b{n}",
                rewritten_text=text,
                target_section="work",
            )
            for n, text in enumerate(texts)
        ],
    )
    document, provenance, _, _subs, _cuts, _trims = _build_document(
        agent,
        facts=[fact],
        bullets_by_fact=bullets,
        master_json_resume={"basics": {}},
        facts_payload=[],
    )
    highlights = document["work"][0]["highlights"]
    assert len(highlights) == 4
    # Provenance proves what is on the page, so it cannot list the two that were cut.
    assert len(provenance) == 4
    assert {entry.text for entry in provenance} == set(highlights)


def test_dashes_from_the_verified_facts_do_not_reach_the_page() -> None:
    facts = [
        TailorFact(
            id="edu",
            kind="education",
            title="Master of Science Computer Science",
            org="Northeastern University — Khoury College of Computer Sciences",
            payload={"studyType": "Master of Science", "area": "Computer Science"},
        ),
        _project("proj", "BedRocked — Civic Sewer-Sequencing Platform", date(2026, 6, 1), None),
    ]
    bullets = {
        "proj": [
            TailorBullet(
                id="b1",
                fact_id="proj",
                text="Trained a classifier via knowledge distillation — Claude Vision as teacher.",
            )
        ]
    }
    document, _ = _assemble_json_resume(
        master_json_resume={"basics": {}},
        all_facts=facts,
        selected_facts=facts,
        selected_bullets=[],
        bullets_by_fact=bullets,
        summary_objective=None,
    )
    rendered = str(document)
    assert "—" not in rendered
    assert "–" not in rendered
    assert document["education"][0]["institution"] == (
        "Northeastern University, Khoury College of Computer Sciences"
    )
    assert document["projects"][0]["name"] == "BedRocked: Civic Sewer-Sequencing Platform"


def test_a_start_date_is_not_an_ats_keyword() -> None:
    """The Point72 posting supplied "May 2028" and "June 1, 2027" as requirements.

    One was credited as a matched skill and the other counted against the score.
    Neither is something a resume can answer with a skill.
    """
    assert _is_date_term("May 2028")
    assert _is_date_term("June 1, 2027")
    assert _is_date_term("2027")
    assert not _is_date_term("Python")
    assert not _is_date_term("C++")
    assert not _is_date_term("3.0 GPA")


def test_writing_problems_cost_the_loop_more_than_nothing_but_are_capped() -> None:
    assert _quality_penalty({}) == 0
    assert _quality_penalty({"work: A": ["too_long(40w)", "first_person"]}) == 6
    # A pass cannot go arbitrarily negative and stop being comparable.
    assert _quality_penalty({"work: A": ["f"] * 50}) == 30
