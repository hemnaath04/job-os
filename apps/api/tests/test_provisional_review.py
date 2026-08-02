"""The rules-only review the Appwrite tailor writes while the real one is in flight.

The agent function's runtime has no LaTeX engine, so the full `review_resume` it
used to run there had no PDF behind it and the browser overwrote its verdict
about a hundred seconds later anyway. Measured, that model call cost ~86s per
tailor for a number with a guaranteed lifespan. `provisional_review` replaces it
with the deterministic half, which costs about a millisecond.

What these tests protect is the honesty of the substitution: it must never claim
to have passed, it must agree with the full review's scoring model, and it must
say out loud which checks did not run.
"""
from __future__ import annotations

import json
import os

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://job_os:job_os@localhost/job_os"
)

from job_os.services.resume_engine import (  # noqa: E402
    _score_from_issues,
    deterministic_review,
    provisional_review,
)

CLEAN_RESUME = {
    "basics": {"name": "A Candidate", "email": "a@b.com", "phone": "555-0100"},
    "work": [
        {
            "name": "Acme Corp",
            "position": "Backend Engineer",
            "startDate": "2024-07-01",
            "endDate": "2025-12-01",
            "highlights": [
                "Built a payments API serving 40 requests per second.",
                "Migrated the release pipeline to GitHub Actions.",
            ],
        }
    ],
    "projects": [
        {
            "name": "Job Searcher",
            "highlights": ["Wrote a background worker that polls job boards."],
        }
    ],
    "education": [{"institution": "A University", "studyType": "MS", "area": "CS"}],
    "skills": [{"name": "Languages", "keywords": ["Python", "Go"]}],
}


def test_it_never_reports_a_pass() -> None:
    # The independent review has not happened. An unknown is not a pass, which is
    # the same stance review_resume takes when its own model call fails.
    assert provisional_review(CLEAN_RESUME).passed is False


def test_it_says_which_checks_did_not_run() -> None:
    review = provisional_review(CLEAN_RESUME)
    codes = {issue.code for issue in review.issues}
    assert "render_unavailable" in codes
    assert review.page_count == 0
    assert review.text_selectable is False
    assert "provisional" in review.model_summary.lower()


def test_the_score_matches_the_full_review_scoring_model() -> None:
    # Not a second opinion with its own arithmetic: the same weighted issue model,
    # restricted to what a rule can see without a render.
    review = provisional_review(CLEAN_RESUME)
    issues, _pages, _selectable = deterministic_review(CLEAN_RESUME, b"")
    expected, breakdown = _score_from_issues(issues)
    assert review.score == expected
    assert review.score_breakdown == breakdown


def test_it_still_reports_real_document_problems() -> None:
    bad = json.loads(json.dumps(CLEAN_RESUME))
    bad["basics"]["email"] = None
    bad["basics"]["phone"] = None
    review = provisional_review(bad)
    assert review.score < provisional_review(CLEAN_RESUME).score
    assert review.issues


def test_it_carries_no_model_estimate() -> None:
    # No model ran, so there is nothing to report as the model's own guess. A
    # leftover number here would read as a review that happened.
    review = provisional_review(CLEAN_RESUME)
    assert review.model_estimate is None
    assert review.strengths == []
    assert review.github_projects_checked == []


def test_the_result_survives_the_appwrite_snapshot_round_trip() -> None:
    # The agent function stores this as JSON on the version row.
    review = provisional_review(CLEAN_RESUME)
    restored = json.loads(json.dumps(review.model_dump(mode="json")))
    assert restored["passed"] is False
    assert restored["score_breakdown"]["total_penalty"] >= 0
