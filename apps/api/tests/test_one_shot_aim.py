"""The writer is told the number to reach, and reaching it costs no extra call.

The rubric was already in the writing prompt -- `_requirement_briefing` has always
handed the model the exact list the score is computed from. What it never did was
say what that list ADDS UP TO, so a pass could satisfy every instruction in the
rubric and still stop short of what the evidence supported, and noticing that was
the repair pass's job.

Naming the ceiling is prompt-only. `achievable` is computed before the prompt is
built, the stop rule is untouched, and a first pass that lands closer to the
ceiling can only make repairs rarer.
"""
from __future__ import annotations

from decimal import Decimal

from job_os.services.tailor import _Coverage, _Requirement, _requirement_briefing


def req(label: str, *, preferred: bool = False) -> _Requirement:
    return _Requirement(label=label, alternatives=(label,), preferred=preferred)


COVERED = _Coverage(free=("Skills row",), selectable=())
UNCOVERED = _Coverage(free=(), selectable=())


def test_the_briefing_names_the_ceiling_and_the_aim() -> None:
    requirements = [req("Python"), req("Kubernetes"), req("Rust")]
    coverage = {"Python": COVERED, "Kubernetes": COVERED, "Rust": UNCOVERED}
    text = _requirement_briefing(requirements, coverage, achievable=Decimal("66.7"))
    assert "THE NUMBER TO REACH" in text
    assert "2 of the 3 must-haves" in text
    assert "66.7" in text


def test_the_aim_tells_the_writer_there_is_no_second_pass() -> None:
    """The one-shot instruction. Without it the model may bank on a repair."""
    text = _requirement_briefing(
        [req("Python")], {"Python": COVERED}, achievable=Decimal("100")
    )
    assert "no second pass" in text


def test_the_aim_does_not_invite_padding_to_close_the_gap() -> None:
    """Raising the aim must not weaken the no-hallucination contract.

    The dangerous reading of "reach this number" is "make the words appear". The
    briefing has to say, in the same breath, that the distance to 100 is evidence
    the candidate lacks and is not the writer's to close.
    """
    text = _requirement_briefing(
        [req("Python"), req("Rust")],
        {"Python": COVERED, "Rust": UNCOVERED},
        achievable=Decimal("50"),
    )
    assert "not yours to close" in text
    assert "padding" in text


def test_an_unnamed_ceiling_changes_nothing() -> None:
    """Omitting `achievable` leaves the briefing exactly as it was."""
    requirements = [req("Python")]
    coverage = {"Python": COVERED}
    assert "THE NUMBER TO REACH" not in _requirement_briefing(requirements, coverage)


def test_a_posting_with_no_must_haves_gets_no_aim() -> None:
    """Nothing to reach, so naming a number would be noise."""
    text = _requirement_briefing(
        [req("Terraform", preferred=True)],
        {"Terraform": UNCOVERED},
        achievable=Decimal("100"),
    )
    assert "THE NUMBER TO REACH" not in text


def test_the_rubric_itself_is_still_there() -> None:
    """The aim is additive; it must not have displaced the scoring rubric."""
    text = _requirement_briefing(
        [req("Python")], {"Python": COVERED}, achievable=Decimal("100")
    )
    assert "HOW THIS PAGE IS SCORED" in text
    assert "MUST-HAVES" in text
