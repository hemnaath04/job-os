"""The writer may not quietly overrule the project ranking.

`_project_relevance` measures which project answers the JD, and that
measurement used to be advice: `selected_fact_ids` was accepted as given.

On a real run against an AI-engineer posting the writer dropped ClaimFarm
(top-ranked), job.os and RoleReveal for two projects scoring 1, and explained
itself with "lack verified bullets so were left out despite JD relevance".
No such rule exists: nothing in tailor.py reads `metric_verified`, and
`_sanitize_selected_bullets` falls back to the candidate's own source text
rather than dropping a bullet. The reason was invented.

These pin the deal: the deviation survives, the excuse gets checked.
"""
from __future__ import annotations

from job_os.services.tailor import (
    TailorBullet,
    _enforce_project_ranking,
    _ProjectScore,
    _selection_correction_note,
)


def score(fact_id: str, title: str, n: int) -> _ProjectScore:
    return _ProjectScore(fact_id=fact_id, title=title, score=n, matched=())


def bullet(fact_id: str) -> TailorBullet:
    return TailorBullet(id=f"b-{fact_id}", fact_id=fact_id, text="something real")


# The five projects from the run this exists because of, with their real scores.
RANKED = [
    score("claimfarm", "ClaimFarm: Agentic Crop-Insurance AI", 3),
    score("bedrocked", "BedRocked", 1),
    score("infantcry", "Infant Cry Sound Detection System", 1),
    score("rolereveal", "RoleReveal", 1),
    score("jobos", "job.os", 0),
]
WRITABLE = {f: [bullet(f)] for f in ("claimfarm", "bedrocked", "infantcry", "rolereveal", "jobos")}


def test_the_top_project_cannot_be_dropped_for_a_weaker_one():
    # Exactly what shipped: ClaimFarm scored 3 and was left off for two 1s.
    corrected, subs = _enforce_project_ranking(
        {"bedrocked", "infantcry"}, RANKED, WRITABLE
    )

    assert "claimfarm" in corrected, "the highest-scoring project has to be on the page"
    assert len(subs) == 1
    assert subs[0][1] == "ClaimFarm: Agentic Crop-Insurance AI"


def test_the_weakest_kept_project_is_the_one_displaced():
    # Not an arbitrary one. Ties break on title so a rerun of the same profile
    # against the same JD makes the same substitution.
    corrected, _ = _enforce_project_ranking({"bedrocked", "infantcry"}, RANKED, WRITABLE)

    assert "claimfarm" in corrected
    assert len(corrected) == 2, "a substitution, not an addition"


def test_a_higher_scoring_project_with_nothing_to_write_from_is_left_alone():
    # The one honest deviation, and now the only one. A project with no bullets
    # genuinely cannot be written, so passing it over is not overruled.
    no_bullets = {f: v for f, v in WRITABLE.items() if f != "claimfarm"}

    corrected, subs = _enforce_project_ranking(
        {"bedrocked", "infantcry"}, RANKED, no_bullets
    )

    assert "claimfarm" not in corrected
    assert subs == []


def test_a_selection_that_already_respects_the_ranking_is_untouched():
    corrected, subs = _enforce_project_ranking(
        {"claimfarm", "bedrocked"}, RANKED, WRITABLE
    )

    assert corrected == {"claimfarm", "bedrocked"}
    assert subs == []


def test_a_project_that_matched_nothing_is_never_forced_onto_the_page():
    # job.os scored 0 against this JD because its fact declared no technologies.
    # The answer to that is the profile edit, not shoving an unscored project in.
    corrected, _ = _enforce_project_ranking({"bedrocked", "infantcry"}, RANKED, WRITABLE)
    assert "jobos" not in corrected


def test_nothing_happens_when_no_project_scored():
    unscored = [score("a", "A", 0), score("b", "B", 0)]
    corrected, subs = _enforce_project_ranking({"b"}, unscored, WRITABLE)

    assert corrected == {"b"}
    assert subs == []


def test_reports_every_substitution_so_the_disagreement_is_visible():
    # The writer's attempt is logged rather than silently reversed: a model that
    # keeps trying to drop the top project is worth knowing about.
    _, subs = _enforce_project_ranking({"infantcry", "bedrocked"}, RANKED, WRITABLE)

    assert subs, "a correction has to be reportable"
    passed_over, restored = subs[0]
    assert restored == "ClaimFarm: Agentic Crop-Insurance AI"
    assert passed_over in {"BedRocked", "Infant Cry Sound Detection System"}



def test_the_note_says_what_shipped_when_the_ranking_overruled_the_writer():
    # The first real run after the check landed reported "Dropped ClaimFarm and
    # job.os this pass" while both were on the finished page. The correction
    # working, described as the correction failing.
    note = _selection_correction_note([("BedRocked", "ClaimFarm"), ("Infant Cry", "job.os")])

    assert "ClaimFarm" in note and "job.os" in note
    assert "out of date" in note, "the stale claim above has to be marked stale"


def test_a_run_that_needed_no_correction_says_nothing():
    # Nothing was overruled, so there is nothing to correct, and a note that
    # explained itself anyway would be noise on every clean run.
    assert _selection_correction_note([]) == ""


def test_a_project_restored_twice_is_named_once():
    note = _selection_correction_note([("A", "ClaimFarm"), ("B", "ClaimFarm")])
    assert note.count("ClaimFarm") == 1
