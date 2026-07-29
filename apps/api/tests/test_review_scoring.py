from __future__ import annotations

import io
import json
from types import SimpleNamespace
from typing import Any

import pytest
from pypdf import PdfWriter

from job_os.schemas.resumes import ResumeReviewIssue
from job_os.services import resume_engine
from job_os.services.resume_engine import (
    PASS_SCORE,
    ModelReview,
    deterministic_review,
)

from _fake_llm import StreamingFakeMessages


def _one_page_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    stream = io.BytesIO()
    writer.write(stream)
    return stream.getvalue()


# A resume a person would actually send: one employer, real projects, no fluff.
GOOD_RESUME = {
    "basics": {"name": "A Candidate", "email": "a@b.com", "phone": "555-0100"},
    "work": [
        {
            "name": "Acme Corp",
            "position": "Backend Engineer",
            "highlights": ["Built a payments API serving 40 requests per second."],
        }
    ],
    "projects": [
        {"name": "Project One", "highlights": ["Shipped a scheduler."]},
        {"name": "Project Two", "highlights": ["Shipped a parser."]},
    ],
    "skills": [{"name": "Languages", "keywords": ["Python", "Go"]}],
}


def test_employer_name_is_not_scored() -> None:
    """No employer may be treated as the only permitted one.

    The old scorer emitted a blocking "unsupported_employer" for any employer
    that was not EPAM, so taking any new job made the resume unfinalizable.
    """
    issues, _pages, _selectable = deterministic_review(GOOD_RESUME, _one_page_pdf())
    codes = {issue.code for issue in issues}
    assert "unsupported_employer" not in codes


def test_specific_languages_are_not_scored() -> None:
    """Listing C++ or C# must not block a resume."""
    doc = {
        **GOOD_RESUME,
        "skills": [{"name": "Languages", "keywords": ["C++", "C#", "Python"]}],
    }
    issues, _pages, _selectable = deterministic_review(doc, _one_page_pdf())
    codes = {issue.code for issue in issues}
    assert "unsupported_skill" not in codes


def test_thin_projects_advise_rather_than_block() -> None:
    doc = {**GOOD_RESUME, "projects": [{"name": "Only One"}]}
    issues, _pages, _selectable = deterministic_review(doc, _one_page_pdf())
    depth = [issue for issue in issues if issue.code == "project_depth"]
    assert depth and depth[0].severity == "warning"


def test_structural_problems_still_block() -> None:
    """The review must stay meaningful: real defects still fail it."""
    issues, _pages, _selectable = deterministic_review(
        {"basics": {}, "work": []}, _one_page_pdf()
    )
    blocking = {issue.code for issue in issues if issue.severity == "blocking"}
    assert {"missing_name", "missing_email", "missing_phone", "missing_experience"} <= blocking


def _stub_model(
    monkeypatch: pytest.MonkeyPatch,
    replies: list[str],
    *,
    rule_issues: list[ResumeReviewIssue] | None = None,
) -> list[Any]:
    """Isolate review_resume's scoring from rendering and from GitHub.

    deterministic_review is stubbed so these tests exercise the scoring math and
    the model-review handling only. Its own rules are covered directly above,
    against a real PDF.
    """
    calls: list[Any] = []

    class FakeMessages(StreamingFakeMessages):
        async def create(self, **kwargs: Any) -> Any:
            calls.append(kwargs)
            body = replies[min(len(calls) - 1, len(replies) - 1)]
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=body)])

    monkeypatch.setattr(
        resume_engine, "_client", lambda: SimpleNamespace(messages=FakeMessages())
    )
    monkeypatch.setattr(
        resume_engine,
        "render_resume_pdf",
        lambda doc, **_kwargs: SimpleNamespace(bytes_=_one_page_pdf()),
    )
    monkeypatch.setattr(
        resume_engine,
        "deterministic_review",
        lambda doc, pdf: (list(rule_issues or []), 1, True),
    )

    async def no_github(*_a: Any, **_k: Any) -> Any:
        return {}, [], []

    monkeypatch.setattr(resume_engine, "load_github_context", no_github)
    return calls


@pytest.mark.asyncio
async def test_a_good_resume_can_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_model(
        monkeypatch,
        [json.dumps({"score": 88, "issues": [], "strengths": ["clear"], "summary": "solid"})],
    )
    result, _pdf = await resume_engine.review_resume(GOOD_RESUME)
    assert result.passed, f"score {result.score} did not reach {PASS_SCORE}"
    assert result.score >= PASS_SCORE


@pytest.mark.asyncio
async def test_unavailable_model_review_does_not_invent_a_mediocre_score(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exact production failure: score 70, passed False, on a clean resume.

    The old code substituted a flat ModelReview(score=70) when the call failed,
    and with a pass mark of 90 that made every resume permanently unfinalizable.
    An unavailable review is an unknown, so score the checks that did run.
    """
    # Mirror production, which also had one ordinary prose warning alongside it.
    _stub_model(
        monkeypatch,
        ["not json at all", "still not json"],
        rule_issues=[
            ResumeReviewIssue(
                severity="warning", code="prose_dash", message="Replace em dashes."
            )
        ],
    )
    result, _pdf = await resume_engine.review_resume(GOOD_RESUME)

    codes = {issue.code for issue in result.issues}
    assert "model_review_unavailable" in codes
    # Two warnings, so 90. Production produced 70 from a review that never ran,
    # and 70 could never clear the old pass mark of 90.
    assert result.score == 90
    # The score reports what was verified. The verdict does not: a real run scored
    # a resume 95 and reported passed=True on a review that returned no tokens at
    # all, which is a green light from a check that did not happen. An unknown is
    # not a pass, and re-running the review is one click.
    assert not result.passed
    assert "did not run" in result.model_summary


@pytest.mark.asyncio
async def test_review_retries_once_when_the_model_answers_with_prose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    good = json.dumps({"score": 91, "issues": [], "strengths": [], "summary": "ok"})
    calls = _stub_model(monkeypatch, ["Sure, here is my review of the resume.", good])
    result, _pdf = await resume_engine.review_resume(GOOD_RESUME)
    assert len(calls) == 2
    assert result.score == 91
    assert "model_review_unavailable" not in {i.code for i in result.issues}


@pytest.mark.asyncio
async def test_blocking_issues_still_fail_regardless_of_score(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_model(
        monkeypatch,
        [json.dumps({"score": 100, "issues": [], "strengths": [], "summary": "perfect"})],
        rule_issues=[
            ResumeReviewIssue(
                severity="blocking", code="missing_email", message="Email is missing."
            )
        ],
    )
    result, _pdf = await resume_engine.review_resume(GOOD_RESUME)
    assert not result.passed
    assert result.score == 80  # a perfect model score cannot outvote a blocker


def test_model_score_tolerates_a_decimal_or_string() -> None:
    """A score written as 87.5 used to fail validation and sink the review."""
    assert ModelReview.model_validate({"score": 87.5}).score == 88
    assert ModelReview.model_validate({"score": "88"}).score == 88
    assert ModelReview.model_validate({"score": 88}).score == 88
