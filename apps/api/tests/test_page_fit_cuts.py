"""A page that spills loses its weakest project, not everything's length.

`over_page` was a flag in the prompt, so the answer to a two-page draft was to
ask the writer to fix it. His Amex draft came out at 37 lines against a budget
of 30, and the cheapest way for a model to satisfy "make it fit" is to shorten
everything: four projects at six words each instead of the best three at a
readable length.

Cutting is the editorial decision, and the ranking already knows which one to
cut. These pin who goes, who never goes, and that it is said out loud.
"""
from __future__ import annotations

from job_os.services.tailor import (
    MIN_PROJECTS_ON_PAGE,
    TailorFact,
    _page_cut_note,
    _ProjectScore,
    _weakest_project_first,
)


def fact(fact_id: str, title: str) -> TailorFact:
    return TailorFact(id=fact_id, kind="project", title=title)


def score(fact_id: str, title: str, n: int) -> _ProjectScore:
    return _ProjectScore(fact_id=fact_id, title=title, score=n, matched=())


# His real five, with the scores the ranker measured against the Amex JD.
FACTS = [
    fact("claimfarm", "ClaimFarm"),
    fact("bedrocked", "BedRocked"),
    fact("infantcry", "Infant Cry Sound Detection System"),
    fact("rolereveal", "RoleReveal"),
    fact("jobos", "job.os"),
]
SCORES = [
    score("claimfarm", "ClaimFarm", 6),
    score("jobos", "job.os", 6),
    score("bedrocked", "BedRocked", 1),
    score("infantcry", "Infant Cry Sound Detection System", 1),
    score("rolereveal", "RoleReveal", 1),
]


def test_the_weakest_match_is_cut_first_not_the_last_one_added():
    order = [f.title for f in _weakest_project_first(FACTS, SCORES)]

    assert order[-2:] == ["ClaimFarm", "job.os"], "the strongest go last, so they go never"
    assert order[0] in {"BedRocked", "Infant Cry Sound Detection System", "RoleReveal"}


def test_a_project_the_ranker_never_scored_goes_before_one_it_did():
    # No measured overlap earns the slot least, so it is not protected by
    # simply being absent from the ranking.
    unscored = [*FACTS, fact("mystery", "Unscored Side Project")]
    order = [f.title for f in _weakest_project_first(unscored, SCORES)]

    assert order[0] == "Unscored Side Project"


def test_the_order_is_stable_for_the_same_profile_and_posting():
    # Same input, same cut, every run. Ties break on title rather than on
    # whatever order the facts arrived in.
    first = [f.id for f in _weakest_project_first(FACTS, SCORES)]
    shuffled = [FACTS[3], FACTS[0], FACTS[4], FACTS[2], FACTS[1]]
    second = [f.id for f in _weakest_project_first(shuffled, SCORES)]

    assert first == second


def test_only_projects_are_candidates():
    mixed = [*FACTS, TailorFact(id="job1", kind="experience", title="EPAM Systems")]
    titles = [f.title for f in _weakest_project_first(mixed, SCORES)]

    assert "EPAM Systems" not in titles


def test_the_floor_leaves_a_resume_that_still_makes_a_case():
    # A page still over length at the floor stays over length. Emptying it to
    # fit is not a better answer than spilling.
    assert MIN_PROJECTS_ON_PAGE >= 2


def test_a_cut_is_said_out_loud():
    # A project that silently disappears reads as the tailor having ignored it.
    note = _page_cut_note(["Infant Cry Sound Detection System", "RoleReveal"])

    assert "Infant Cry Sound Detection System" in note
    assert "RoleReveal" in note
    assert "space" in note.lower()


def test_a_page_that_fits_says_nothing():
    assert _page_cut_note([]) == ""
