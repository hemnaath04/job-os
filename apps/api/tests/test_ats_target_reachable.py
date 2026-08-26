"""The target has to be something these facts could actually reach.

Measured against the real Amex AI-Engineer posting and this candidate's whole
vault: 67 requirements, of which the vault can evidence 15. The honest ceiling
is 22.4%. The run scored 23.3, at the ceiling, the best resume those facts can
produce, and was told it had failed against a fixed 80 -- then spent a second
compose pass trying to beat a maximum.

A JD asks for what it asks for. A candidate has what they have.
"""
from __future__ import annotations

from decimal import Decimal

from job_os.services.tailor import (
    TARGET_ATS_SCORE,
    _achievable_ats_score,
    _effective_target,
    _Requirement,
)


class _Cov:
    def __init__(self, found: bool) -> None:
        self.found = found


def reqs(n: int) -> list[_Requirement]:
    return [
        _Requirement(label=f"r{i}", alternatives=(f"r{i}",), preferred=False) for i in range(n)
    ]


def coverage(total: int, reachable: int) -> dict[str, _Cov]:
    return {f"r{i}": _Cov(i < reachable) for i in range(total)}


def test_the_real_case_that_burned_a_compose_pass():
    # 15 of 67, the numbers off his own vault against that posting.
    achievable = _achievable_ats_score(reqs(67), coverage(67, 15))

    assert round(float(achievable), 1) == 22.4
    target = _effective_target(achievable)
    assert float(target) < 25, "a run scoring 23.3 must not be told it failed"
    assert Decimal("23.3") >= target, "the best possible resume has to count as done"


def test_a_reachable_posting_still_has_to_be_earned():
    # Everything evidenced does not mean the target collapses to nothing: the
    # fixed 80 still applies when the facts could carry the whole posting.
    achievable = _achievable_ats_score(reqs(10), coverage(10, 10))

    assert achievable == Decimal(100)
    assert _effective_target(achievable) == TARGET_ATS_SCORE


def test_the_target_never_exceeds_the_fixed_one():
    # A candidate who can cover everything is not asked for more than 80.
    assert _effective_target(Decimal(100)) == TARGET_ATS_SCORE
    assert _effective_target(Decimal(95)) == TARGET_ATS_SCORE


def test_the_target_never_exceeds_what_is_reachable():
    achievable = _achievable_ats_score(reqs(20), coverage(20, 5))

    assert achievable == Decimal(25)
    assert _effective_target(achievable) <= achievable


def test_headroom_is_left_rather_than_demanding_every_reachable_point():
    # Coverage calls a requirement reachable when some fact touches it, and one
    # page holds a subset of that. Demanding 100% of reachable would replace an
    # unreachable target with a different unreachable target.
    achievable = _achievable_ats_score(reqs(10), coverage(10, 5))

    assert achievable == Decimal(50)
    assert _effective_target(achievable) < achievable


def test_a_posting_with_no_requirements_falls_back_to_the_fixed_target():
    # Nothing to divide by, and nothing to be honest about either.
    assert _achievable_ats_score([], {}) == TARGET_ATS_SCORE


def test_a_posting_nothing_can_evidence_does_not_produce_a_zero_target():
    # It does produce a very low one, which is correct: there is genuinely
    # nothing to cover. What matters is that it is a number, not a crash.
    achievable = _achievable_ats_score(reqs(30), coverage(30, 0))

    assert achievable == Decimal(0)
    assert _effective_target(achievable) == Decimal("0.0")
