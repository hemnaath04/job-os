"""A ranking tie is broken on evidence, not on the alphabet.

Against the real Amex posting his three strongest projects all scored 3 out of
66 requirements, so which one the page-fit cut removed was decided by the title
tie-break: alphabetically. That is how BedRocked, a deployed 2026 platform, got
cut from an AI-engineer resume, and how job.os stayed off it entirely.

BedRocked and job.os have reachable URLs and no end date. Infant Cry is a 2024
class project with neither. The keyword scorer could not tell them apart because
it counts words, and none of that difference is a word.

A cross-encoder was measured on this same data first. It produced the same
ordering for 568M parameters and about 2.3GB on the dyno, and it also floated an
old internship model suite above ClaimFarm, which this rule cannot do because it
never reorders across lexical tiers.
"""
from __future__ import annotations

from job_os.services.tailor import (
    TailorFact,
    _as_sortable_date,
    _evidence_rank,
    _ProjectScore,
    _weakest_project_first,
)


def score(title: str, n: int, *, url: bool = False, ongoing: bool = True, start: int = 0):
    return _ProjectScore(
        fact_id=title,
        title=title,
        score=n,
        matched=(),
        live_url=url,
        ongoing=ongoing,
        started_at=start,
    )


# The real tie, with the real signals.
BEDROCKED = score("BedRocked", 3, url=True, ongoing=True, start=20260601)
JOBOS = score("job.os", 3, url=True, ongoing=True, start=0)
INFANT = score("Infant Cry", 3, url=False, ongoing=False, start=20240101)


def test_the_real_tie_the_alphabet_used_to_decide() -> None:
    """A live, ongoing project outranks a finished one with no URL."""
    assert _evidence_rank(INFANT) < _evidence_rank(JOBOS)
    assert _evidence_rank(INFANT) < _evidence_rank(BEDROCKED)


def test_the_weakest_of_the_tie_is_the_one_with_no_evidence() -> None:
    facts = [
        TailorFact(id=t, kind="project", title=t)
        for t in ("BedRocked", "job.os", "Infant Cry")
    ]
    order = _weakest_project_first(facts, [BEDROCKED, JOBOS, INFANT])
    assert order[0].title == "Infant Cry", "the cut takes this one first now"


def test_a_higher_lexical_score_still_wins_outright() -> None:
    """The tie-break never reorders across tiers, which is what keeps it safe."""
    claimfarm = score("ClaimFarm", 5, url=False, ongoing=True, start=20260601)
    facts = [
        TailorFact(id=t, kind="project", title=t) for t in ("ClaimFarm", "job.os")
    ]
    order = _weakest_project_first(facts, [claimfarm, JOBOS])
    assert order[-1].title == "ClaimFarm", "5 beats 3 regardless of any URL"


def test_a_live_url_outranks_mere_recency() -> None:
    """job.os has NO start_date, so recency alone would have sunk it."""
    dated_but_dead = score("Old", 3, url=False, ongoing=False, start=20260101)
    assert _evidence_rank(dated_but_dead) < _evidence_rank(JOBOS)


def test_a_missing_date_is_the_weakest_recency_claim_not_the_strongest() -> None:
    assert _as_sortable_date(None) == 0
    assert _as_sortable_date("") == 0
    assert _as_sortable_date("2026-06-01") == 20260601
    assert _as_sortable_date("2024-05") == 202405
    assert _as_sortable_date(None) < _as_sortable_date("2024-01-01")


def test_the_title_is_still_the_last_word_so_a_run_repeats() -> None:
    """Two projects alike in every signal must still cut the same one twice."""
    a = score("Alpha", 3, url=True, ongoing=True, start=20260101)
    b = score("Beta", 3, url=True, ongoing=True, start=20260101)
    facts = [TailorFact(id=t, kind="project", title=t) for t in ("Beta", "Alpha")]
    assert [f.title for f in _weakest_project_first(facts, [a, b])] == ["Alpha", "Beta"]
