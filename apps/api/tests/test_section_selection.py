"""What reaches the page, and what stays in the vault without printing.

Both decisions here are the user's, recorded in the career-ops playbook: the
Languages row is fixed and excludes R and JS/TS, and certificates compete for
page space rather than occupying it by default.
"""
from __future__ import annotations

from datetime import date

from job_os.schemas.resumes import TailorAgentOutput
from job_os.services.tailor import (
    TailorFact,
    _assemble_json_resume,
    _build_document,
    _consolidate_skills,
)


def test_the_languages_row_withholds_r_and_javascript() -> None:
    groups = _consolidate_skills(
        {"Languages": ["Python", "Java", "Go", "R", "SQL", "Bash", "TypeScript"]}
    )
    assert groups[0]["keywords"] == ["Python", "Java", "Go", "SQL", "Bash"]


def test_a_withheld_skill_is_not_deleted_from_the_profile() -> None:
    """Withholding is a rendering decision, so the fact itself is untouched.

    The verified skill stays in the vault; the resume simply does not claim it.
    """
    facts = [
        TailorFact(id="s1", kind="skill", title="R", payload={"category": "Languages"}),
        TailorFact(
            id="s2", kind="skill", title="Python", payload={"category": "Languages"}
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
    assert document["skills"][0]["keywords"] == ["Python"]
    # The fact list handed in is unchanged, so nothing was removed at source.
    assert {f.title for f in facts} == {"R", "Python"}


def _certificate(fact_id: str, title: str) -> TailorFact:
    return TailorFact(
        id=fact_id, kind="certification", title=title, org="Some MOOC",
        start_date=date(2023, 1, 1),
    )


def test_certificates_appear_only_when_the_agent_selects_them() -> None:
    facts = [_certificate("c1", "Machine Learning"), _certificate("c2", "Python Core")]
    chosen, _prov, _reason, _subs, _cuts, _trims = _build_document(
        TailorAgentOutput(selected_fact_ids=["c1"]),
        facts=facts,
        bullets_by_fact={},
        master_json_resume={"basics": {}},
        facts_payload=[],
    )
    assert [c["name"] for c in chosen["certificates"]] == ["Machine Learning"]


def test_leaving_every_certificate_out_is_a_normal_outcome() -> None:
    """No fallback here, unlike projects. An empty section is the point.

    Three undated MOOC certificates printed on every tailored resume whatever the
    role, and the review asked for that space to go to a project instead.
    """
    facts = [_certificate("c1", "Machine Learning"), _certificate("c2", "Python Core")]
    document, _prov, _reason, _subs, _cuts, _trims = _build_document(
        TailorAgentOutput(selected_fact_ids=[]),
        facts=facts,
        bullets_by_fact={},
        master_json_resume={"basics": {}},
        facts_payload=[],
    )
    assert document["certificates"] == []
