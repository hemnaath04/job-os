from __future__ import annotations

from datetime import date

from job_os.schemas.resumes import SelectedBullet
from job_os.services.tailor import (
    TailorBullet,
    TailorFact,
    _assemble_json_resume,
    _collapse_duplicate_entries,
    _identity_text,
    _work_identity,
)

EPAM_START = date(2024, 7, 1)
EPAM_END = date(2025, 12, 1)


def _epam_facts() -> list[TailorFact]:
    """The real duplicate pair: one job, two facts, differently worded.

    Importing a resume twice with different phrasing produces both, and the
    tailored resume used to list EPAM Systems twice as a result.
    """
    return [
        TailorFact(
            id="fact-long",
            kind="experience",
            title=(
                "Junior Software Test Automation Engineer, Client: leading global "
                "rideshare platform (Fares team)"
            ),
            org="EPAM Systems",
            start_date=EPAM_START,
            end_date=EPAM_END,
        ),
        TailorFact(
            id="fact-short",
            kind="experience",
            title="Software Test Automation Engineer",
            org="EPAM Systems",
            start_date=EPAM_START,
            end_date=EPAM_END,
        ),
    ]


def _northeastern_facts() -> list[TailorFact]:
    """Same degree, same school, punctuated two different ways."""
    return [
        TailorFact(
            id="edu-dash",
            kind="education",
            title="Master of Science Computer Science",
            org="Northeastern University - Khoury College of Computer Sciences",
            start_date=date(2026, 1, 1),
            end_date=date(2028, 5, 1),
            payload={"studyType": "Master of Science", "area": "Computer Science"},
        ),
        TailorFact(
            id="edu-comma",
            kind="education",
            title="Master of Science Computer Science",
            org="Northeastern University, Khoury College of Computer Sciences",
            start_date=date(2026, 1, 1),
            end_date=date(2028, 5, 1),
            payload={"studyType": "Master of Science", "area": "Computer Science"},
        ),
    ]


def test_identity_text_ignores_punctuation_case_and_accents() -> None:
    dashed = _identity_text("Northeastern University - Khoury College")
    comma = _identity_text("Northeastern University, Khoury College")
    assert dashed == comma == "northeastern university khoury college"
    assert _identity_text(None) == ""


def test_one_job_worded_two_ways_renders_once_keeping_every_bullet() -> None:
    facts = _epam_facts()
    bullets_by_fact = {
        "fact-long": [
            TailorBullet(id="b1", fact_id="fact-long", text="Migrated suites to Cucumber."),
        ],
        "fact-short": [
            TailorBullet(id="b2", fact_id="fact-short", text="Tightened Jenkins CI."),
        ],
    }
    selected = [
        SelectedBullet(
            fact_bullet_id="b1",
            rewritten_text="Migrated suites to Cucumber.",
            target_section="work",
        ),
        SelectedBullet(
            fact_bullet_id="b2",
            rewritten_text="Tightened Jenkins CI.",
            target_section="work",
        ),
    ]

    document, _provenance = _assemble_json_resume(
        master_json_resume={"basics": {}},
        all_facts=facts,
        selected_facts=facts,
        selected_bullets=selected,
        bullets_by_fact=bullets_by_fact,
        summary_objective=None,
    )

    assert len(document["work"]) == 1
    entry = document["work"][0]
    assert entry["name"] == "EPAM Systems"
    # The more specific wording survives.
    assert entry["position"].startswith("Junior Software Test Automation Engineer")
    # Neither variant's bullet is lost in the merge.
    assert set(entry["highlights"]) == {
        "Migrated suites to Cucumber.",
        "Tightened Jenkins CI.",
    }


def test_one_degree_punctuated_two_ways_renders_once() -> None:
    facts = _northeastern_facts()
    document, _ = _assemble_json_resume(
        master_json_resume={"basics": {}},
        all_facts=facts,
        selected_facts=facts,
        selected_bullets=[],
        bullets_by_fact={},
        summary_objective=None,
    )
    assert len(document["education"]) == 1
    assert document["education"][0]["studyType"] == "Master of Science"


def test_two_real_stints_at_one_employer_stay_separate() -> None:
    """Deduping must not collapse a promotion or a return to the same company."""
    facts = [
        TailorFact(
            id="a",
            kind="experience",
            title="Engineer",
            org="EPAM Systems",
            start_date=date(2022, 1, 1),
            end_date=date(2023, 1, 1),
        ),
        TailorFact(
            id="b",
            kind="experience",
            title="Senior Engineer",
            org="EPAM Systems",
            start_date=EPAM_START,
            end_date=EPAM_END,
        ),
    ]
    document, _ = _assemble_json_resume(
        master_json_resume={"basics": {}},
        all_facts=facts,
        selected_facts=facts,
        selected_bullets=[],
        bullets_by_fact={},
        summary_objective=None,
    )
    assert len(document["work"]) == 2


def test_two_different_degrees_at_one_school_stay_separate() -> None:
    facts = [
        TailorFact(
            id="ms",
            kind="education",
            title="Master of Science Computer Science",
            org="Northeastern University",
            payload={"studyType": "Master of Science", "area": "Computer Science"},
        ),
        TailorFact(
            id="bs",
            kind="education",
            title="Bachelor of Science Mathematics",
            org="Northeastern University",
            payload={"studyType": "Bachelor of Science", "area": "Mathematics"},
        ),
    ]
    document, _ = _assemble_json_resume(
        master_json_resume={"basics": {}},
        all_facts=facts,
        selected_facts=facts,
        selected_bullets=[],
        bullets_by_fact={},
        summary_objective=None,
    )
    assert len(document["education"]) == 2


def test_duplicate_skill_keywords_are_listed_once() -> None:
    facts = [
        TailorFact(id="s1", kind="skill", title="Python", org="Languages",
                   payload={"category": "Languages"}),
        TailorFact(id="s2", kind="skill", title="Python", org="Languages",
                   payload={"category": "Languages"}),
    ]
    document, _ = _assemble_json_resume(
        master_json_resume={"basics": {}},
        all_facts=facts,
        selected_facts=facts,
        selected_bullets=[],
        bullets_by_fact={},
        summary_objective=None,
    )
    assert document["skills"][0]["keywords"] == ["Python"]


def test_collapse_prefers_the_variant_with_more_evidence() -> None:
    entries = [
        {"name": "Acme", "position": "A very long specific title", "startDate": "2024-01-01",
         "endDate": None, "highlights": []},
        {"name": "Acme", "position": "Eng", "startDate": "2024-01-01", "endDate": None,
         "highlights": ["did the thing"], "location": "Boston"},
    ]
    merged = _collapse_duplicate_entries(
        entries, _work_identity, list_fields=("highlights",)
    )
    assert len(merged) == 1
    # Most highlights wins the scalar fields, ahead of the wordier title.
    assert merged[0]["position"] == "Eng"
    assert merged[0]["highlights"] == ["did the thing"]
    # And a field only the other variant had is filled in, not dropped.
    assert merged[0]["location"] == "Boston"
