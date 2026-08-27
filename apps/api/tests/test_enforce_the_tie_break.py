"""The tie-break decided the order and nothing enforced it.

#66 taught the ranking to break a tie on evidence, a reachable URL and still
being worked on, and changed the order everywhere the order is read. It did not
change `_enforce_project_ranking`, the one place that ACTS on the order when the
writer picks projects, and that compared `p.score < candidate.score`.

So a tie was never a violation. On the first run after #66 deployed, job.os and
Infant Cry both scored 3 of 66, the writer picked Infant Cry, and nothing
corrected it: a live 2026 platform lost its slot to a 2024 class project because
the enforcement could not see the difference the ranking had just learned.
"""
from __future__ import annotations

from job_os.services.tailor import (
    TailorBullet,
    _enforce_project_ranking,
    _ProjectScore,
    _ranking_key,
)


def score(title: str, n: int, *, url: bool = False, ongoing: bool = True, start: int = 0):
    return _ProjectScore(
        fact_id=title, title=title, score=n, matched=(),
        live_url=url, ongoing=ongoing, started_at=start,
    )


BEDROCKED = score("BedRocked", 3, url=True, ongoing=True, start=20260601)
JOBOS = score("job.os", 3, url=True, ongoing=True, start=0)
INFANT = score("Infant Cry", 3, url=False, ongoing=False, start=20240101)
CLAIMFARM = score("ClaimFarm", 5, url=False, ongoing=True, start=20260601)
ALL = [BEDROCKED, JOBOS, INFANT, CLAIMFARM]


def bullets(*titles: str) -> dict[str, list[TailorBullet]]:
    return {t: [TailorBullet(id=f"{t}b", fact_id=t, text="Built a thing.")] for t in titles}


def test_the_real_run_job_os_takes_the_slot_back() -> None:
    selected = {"BedRocked", "ClaimFarm", "Infant Cry"}
    corrected, substitutions = _enforce_project_ranking(
        selected, ALL, bullets("BedRocked", "job.os", "Infant Cry", "ClaimFarm")
    )
    assert corrected == {"BedRocked", "ClaimFarm", "job.os"}
    assert substitutions == [("Infant Cry", "job.os")]


def test_a_tie_is_now_a_violation_when_the_evidence_differs() -> None:
    """It was not, and that is the whole bug."""
    assert _ranking_key(INFANT) < _ranking_key(JOBOS)
    assert JOBOS.score == INFANT.score


def test_two_projects_alike_in_every_signal_are_left_alone() -> None:
    """No evidence to prefer one, so the writer's pick stands."""
    a = score("Alpha", 3, url=True, ongoing=True, start=20260101)
    b = score("Beta", 3, url=True, ongoing=True, start=20260101)
    corrected, substitutions = _enforce_project_ranking(
        {"Beta"}, [a, b], bullets("Alpha", "Beta")
    )
    assert substitutions == [] or corrected == {"Beta"}


def test_a_project_with_no_bullets_is_still_the_one_legitimate_escape() -> None:
    """#39's rule survives: a project that cannot be written from stays out."""
    corrected, substitutions = _enforce_project_ranking(
        {"BedRocked", "ClaimFarm", "Infant Cry"},
        ALL,
        bullets("BedRocked", "Infant Cry", "ClaimFarm"),  # job.os has none
    )
    assert "job.os" not in corrected
    assert substitutions == []


def test_a_stronger_score_still_wins_regardless_of_evidence() -> None:
    corrected, _subs = _enforce_project_ranking(
        {"Infant Cry"}, [CLAIMFARM, INFANT], bullets("ClaimFarm", "Infant Cry")
    )
    assert corrected == {"ClaimFarm"}


def test_selection_and_the_page_fit_cut_agree_by_construction() -> None:
    """One key, so the two cannot disagree about which project is stronger."""
    ordered = sorted(ALL, key=lambda p: (_ranking_key(p), p.title.casefold()))
    assert [p.title for p in ordered] == ["Infant Cry", "job.os", "BedRocked", "ClaimFarm"]


def test_the_alphabet_is_not_grounds_to_override_the_writer() -> None:
    """The title breaks sort ties so a run repeats. It is not a reason.

    An earlier draft of this fix put the title in the merit comparison, which
    made "comes later in the alphabet" an enforceable violation. That is the
    exact thing #66 exists to stop, and #39's own tests caught it.
    """
    a = score("Alpha", 1)
    z = score("Zulu", 1)
    assert _ranking_key(a) == _ranking_key(z)
    corrected, substitutions = _enforce_project_ranking(
        {"Zulu"}, [a, z], bullets("Alpha", "Zulu")
    )
    assert substitutions == []
    assert corrected == {"Zulu"}, "the writer's pick stands on a genuine tie"
