"""Phase 8: property-test the review score against what the README claims.

Claim under test (README.md:105-108): "the 0 to 100 score is derived from that
weighted issue list, so the same issues always produce the same number."

Run from apps/api so the package resolves:
    cd apps/api && .venv/bin/python -m pytest tests/test_review_score_properties.py -v
"""
from __future__ import annotations

import itertools
import random
from decimal import Decimal

import pytest
from job_os.services.resume_engine import (
    _BLOCKING_PENALTY,
    _MAX_SUGGESTION_PENALTY,
    _SUGGESTION_PENALTY,
    _WARNING_PENALTY,
    PASS_SCORE,
    _score_from_issues,
)
from job_os.schemas.resumes import ResumeReviewIssue


def issue(severity: str, code: str = "x", message: str = "m") -> ResumeReviewIssue:
    return ResumeReviewIssue(severity=severity, code=code, message=message, section="work")


def test_weights_are_what_findings_recorded() -> None:
    assert (_BLOCKING_PENALTY, _WARNING_PENALTY, _SUGGESTION_PENALTY) == (20, 5, 1)
    assert _MAX_SUGGESTION_PENALTY == 5
    assert PASS_SCORE == Decimal("75")


def test_same_issue_list_100_times_is_one_distinct_score() -> None:
    """The README's central determinism claim."""
    issues = [issue("blocking"), issue("warning"), issue("warning"), issue("suggestion")]
    scores = {_score_from_issues(issues)[0] for _ in range(100)}
    assert len(scores) == 1, scores
    assert scores.pop() == Decimal(100 - 20 - 10 - 1)


def test_score_is_order_independent() -> None:
    issues = [issue("blocking"), issue("warning"), issue("suggestion"), issue("suggestion")]
    baseline = _score_from_issues(issues)[0]
    for permutation in itertools.permutations(issues):
        assert _score_from_issues(list(permutation))[0] == baseline


def test_duplicate_issues_are_double_counted() -> None:
    """Documents the actual behaviour: identical issues each deduct."""
    one = _score_from_issues([issue("warning", code="same", message="same")])[0]
    two = _score_from_issues(
        [issue("warning", code="same", message="same"), issue("warning", code="same", message="same")]
    )[0]
    assert one == Decimal(95)
    assert two == Decimal(90), "duplicates deduct twice; not deduplicated by code+message"


@pytest.mark.parametrize(
    ("blocking", "warning", "suggestion", "expected"),
    [
        (0, 0, 0, 100),
        (0, 0, 1, 99),
        (0, 2, 0, 90),
        (0, 2, 1, 89),
        (0, 1, 0, 95),
        (1, 0, 0, 80),
        (0, 5, 0, 75),        # exactly PASS_SCORE
        (0, 5, 1, 74),        # one under
        (5, 0, 0, 0),         # exactly floor
        (10, 0, 0, 0),        # would be -100, clamped
        (0, 100, 0, 0),       # clamped
    ],
)
def test_boundary_scale(blocking: int, warning: int, suggestion: int, expected: int) -> None:
    issues = (
        [issue("blocking")] * blocking
        + [issue("warning")] * warning
        + [issue("suggestion")] * suggestion
    )
    assert _score_from_issues(issues)[0] == Decimal(expected)


def test_never_below_zero_or_above_100_on_random_lists() -> None:
    rng = random.Random(1234)
    for _ in range(500):
        issues = [
            issue(rng.choice(["blocking", "warning", "suggestion"]))
            for _ in range(rng.randint(0, 40))
        ]
        score = _score_from_issues(issues)[0]
        assert Decimal(0) <= score <= Decimal(100), score


def test_suggestion_penalty_is_capped() -> None:
    """A thorough reviewer listing many small notes cannot sink a clean document."""
    assert _score_from_issues([issue("suggestion")] * 5)[0] == Decimal(95)
    assert _score_from_issues([issue("suggestion")] * 50)[0] == Decimal(95)


def test_score_is_decimal_not_float() -> None:
    score, breakdown = _score_from_issues([issue("warning")])
    assert isinstance(score, Decimal)
    assert all(isinstance(v, int) for v in breakdown.values())


def test_severity_is_constrained_by_the_type_so_a_typo_cannot_go_unscored() -> None:
    """Defence in depth worth recording: the scorer counts by string equality, so an
    unrecognised severity would silently deduct nothing. The Literal is what stops
    that being reachable -- pydantic rejects it at construction."""
    import pydantic

    for bad in ("critical", "error", "BLOCKING", "Blocking"):
        with pytest.raises(pydantic.ValidationError):
            issue(bad)

    # But the guarantee is only as strong as the validation: model_construct skips it,
    # and then the issue is free.
    unvalidated = ResumeReviewIssue.model_construct(
        severity="BLOCKING", code="x", message="m", section="work"
    )
    assert _score_from_issues([unvalidated])[0] == Decimal(100)
