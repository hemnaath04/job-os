"""Merging duplicate facts was throwing away the candidate's edits.

Measured on the real vault. Three BedRocked duplicates carried 6, 6 and 12
keywords, the twelve being the AI terms he had just added specifically to fix
that project's ranking. `payload[key] = value` is last-writer-wins per key, so
the six-keyword variant won and the edit vanished.

The cost was not abstract. BedRocked then scored 2 against the Amex JD, tied
with three unrelated MSD projects, and the page-fit cut removed it on a title
tie-break: his strongest AI project, cut because the ranker never saw the
keywords he added to it. Unioned it scores 4 and leaves the bottom tie.
"""
from __future__ import annotations

from job_os.services.tailor import TailorFact, _merge_duplicate_facts


def project(fact_id: str, keywords: list[str], **payload) -> TailorFact:
    return TailorFact(
        id=fact_id,
        kind="project",
        title="BedRocked — Civic Sewer-Sequencing Platform",
        payload={"keywords": keywords, **payload},
    )


def merge(*facts: TailorFact) -> TailorFact:
    merged, _bullets = _merge_duplicate_facts(
        list(facts), {f.id: [] for f in facts}
    )
    assert len(merged) == 1, "these are duplicates and must collapse to one"
    return merged[0]


def test_the_real_case_no_keyword_is_lost() -> None:
    winner = merge(
        project("a", ["Python", "FastAPI", "scikit-learn"]),
        project("b", ["Python", "FastAPI", "scikit-learn"]),
        project("c", ["Knowledge Distillation", "Computer Vision", "Generative AI"]),
    )
    for keyword in (
        "Python",
        "FastAPI",
        "scikit-learn",
        "Knowledge Distillation",
        "Computer Vision",
        "Generative AI",
    ):
        assert keyword in winner.payload["keywords"], keyword


def test_a_keyword_on_any_duplicate_counts_for_all_of_them() -> None:
    """They are the same fact by construction, so an edit to one is an edit."""
    winner = merge(project("a", []), project("b", ["RAG"]))
    assert winner.payload["keywords"] == ["RAG"]


def test_the_union_does_not_duplicate() -> None:
    winner = merge(project("a", ["Python"]), project("b", ["Python", "Go"]))
    assert winner.payload["keywords"].count("Python") == 1


def test_a_scalar_conflict_still_takes_the_canonical_value() -> None:
    """Two different summaries are a real conflict, not something to concatenate."""
    winner = merge(
        project("a", ["Python"], description="The older wording."),
        project("b", ["Go"], description="The newer wording."),
    )
    assert winner.payload["description"] in {"The older wording.", "The newer wording."}
    assert sorted(winner.payload["keywords"]) == ["Go", "Python"]


def test_an_empty_list_does_not_erase_a_populated_one() -> None:
    winner = merge(project("a", ["Python", "Go"]), project("b", []))
    assert sorted(winner.payload["keywords"]) == ["Go", "Python"]
