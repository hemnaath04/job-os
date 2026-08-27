"""Clearing a lowered target is not the same as having nothing left to do.

`target_score` moves with what the vault can reach, so on a stretch posting it
lands well under TARGET_ATS_SCORE. A pass that scrapes over it used to end the
run, and a measured A/B caught the cost: two runs whose first pass came in at
24.2 against a target of 22.8 stopped there and shipped 30.2, while two whose
first pass came in at 21.9, one point short, took a repair and shipped 34.9.

A two-point difference on the first pass decided a five-point difference in the
finished resume, and the run that did WORSE first got the better document. That
is noise deciding quality.

The rule is not "always take another pass". It is that a lowered floor only
ends the run when there is also nothing a repair could act on.
"""
from __future__ import annotations

from decimal import Decimal

from job_os.services.tailor import MAX_COMPOSE_PASSES, _run_is_done


def decide(**kwargs) -> bool:
    """The real stop rule, with a stretch-posting run as the baseline."""
    base = {
        "pass_target": Decimal("22.8"),
        "reachable": ["LLM APIs"],
        "chargeable": {},
        "passes": 1,
        "improved": True,
        "nothing_left": False,
    }
    return _run_is_done(**{**base, **kwargs})


def test_the_run_that_stopped_early_now_takes_its_repair() -> None:
    """24.2 over a 22.8 floor, with a requirement still reachable."""
    assert decide(score=Decimal("24.2")) is False


def test_the_run_that_fell_short_still_repairs() -> None:
    """21.9 under the floor. Unchanged, and it was always the right behaviour."""
    assert decide(score=Decimal("21.9")) is False


def test_clearing_the_real_target_still_ends_the_run() -> None:
    """80 is the aspiration, not a floor that moved. Nothing to second-guess."""
    assert decide(score=Decimal("81")) is True


def test_a_lowered_floor_with_nothing_left_still_ends_the_run() -> None:
    """The whole point of the achievable target: do not chase what is not there."""
    assert (
        decide(
            score=Decimal("24.2"), reachable=[], chargeable={}, nothing_left=True
        )
        is True
    )


def test_a_writing_flag_alone_is_enough_to_take_the_pass() -> None:
    """A flag the writer introduced is something a repair can genuinely fix."""
    assert (
        decide(
            score=Decimal("24.2"),
            reachable=[],
            chargeable={"projects: job.os": ["too_long(46w)"]},
        )
        is False
    )


def test_the_pass_budget_is_not_regressed() -> None:
    """Worth another pass, but there are no passes left."""
    assert decide(score=Decimal("24.2"), passes=MAX_COMPOSE_PASSES) is True


def test_a_pass_that_stopped_paying_for_itself_still_ends_the_run() -> None:
    assert decide(score=Decimal("24.2"), improved=False) is True


def test_an_unsettled_analysis_does_not_end_the_run_on_a_ceiling() -> None:
    """`nothing_left` waits on the analyst, and this must not route around it.

    Unsettled, the caller passes the fixed target instead of the lowered one and
    `nothing_left` stays False, so a mid-range score keeps the run alive.
    """
    assert (
        decide(
            score=Decimal("24.2"),
            pass_target=Decimal("80"),
            reachable=[],
            chargeable={},
            nothing_left=False,
        )
        is False
    )
