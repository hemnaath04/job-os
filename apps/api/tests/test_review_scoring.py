from __future__ import annotations

import io
import json
import os
from types import SimpleNamespace
from typing import Any

import pytest
from pypdf import PdfWriter

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://job_os:job_os@localhost/job_os"
)

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
    # No issues means no deductions, so a clean resume scores 100. The model's own
    # 91 is advisory now, kept as model_estimate, never the grade.
    assert result.score == 100
    assert result.model_estimate == 91
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


@pytest.mark.asyncio
async def test_the_grade_is_deterministic_across_model_moods(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whiplash fix: identical issues, different model self-scores, one grade.

    Three real container reviews of one document self-reported 85, 89 and 80 while
    the issue counts barely moved, and that swing was the score the user watched
    crater at finalize. The grade now comes from the weighted issues, not the mood,
    so two reviews that disagree only on the number land on the same score.
    """
    warning = {"severity": "warning", "code": "overclaim", "message": "x", "section": None}
    for model_score in (55, 92):
        _stub_model(
            monkeypatch,
            [json.dumps({"score": model_score, "issues": [warning], "strengths": [], "summary": "s"})],
        )
        result, _pdf = await resume_engine.review_resume(GOOD_RESUME)
        assert result.score == 95  # 100 - one warning, whatever the model said
        assert result.model_estimate == model_score


@pytest.mark.asyncio
async def test_many_small_suggestions_do_not_sink_a_clean_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A thorough reviewer listing nine polish notes must not fail a good resume.

    The suggestion total is capped, so being thorough about small things cannot
    score a resume down the way one real defect does.
    """
    suggestions = [
        {"severity": "suggestion", "code": f"polish_{i}", "message": "m", "section": None}
        for i in range(9)
    ]
    _stub_model(
        monkeypatch,
        [json.dumps({"score": 70, "issues": suggestions, "strengths": [], "summary": "s"})],
    )
    result, _pdf = await resume_engine.review_resume(GOOD_RESUME)
    assert result.score == 95  # nine suggestions capped at a 5-point deduction
    assert result.passed
    assert result.score_breakdown is not None
    assert result.score_breakdown["suggestion_penalty"] == 5


def test_model_score_tolerates_a_decimal_or_string() -> None:
    """A score written as 87.5 used to fail validation and sink the review."""
    assert ModelReview.model_validate({"score": 87.5}).score == 88
    assert ModelReview.model_validate({"score": "88"}).score == 88
    assert ModelReview.model_validate({"score": 88}).score == 88


@pytest.mark.asyncio
async def test_a_two_page_resume_passes_with_advice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A page and a bit is a document the user may want to send.

    The LaTeX renderer holds this content on one page in sb2nov and spills to two in
    the other templates, and it correctly refuses to shrink margins or fonts to fake
    a fit. So the review advises rather than vetoes: the score still reflects the
    miss, but the resume is not sent back for another editing round over it.
    """
    _stub_model(
        monkeypatch,
        [json.dumps({"score": 92, "issues": [], "strengths": [], "summary": "solid"})],
        rule_issues=[
            ResumeReviewIssue(
                severity="warning",
                code="page_count",
                message="Renders to 2 pages. One page is the target.",
            )
        ],
    )
    result, _pdf = await resume_engine.review_resume(GOOD_RESUME)

    assert result.passed, f"a two-page resume should pass, scored {result.score}"
    # The advice is still on the record, and the one warning deducts 5, so missing
    # one page is never free. The model's 92 is advisory.
    assert "page_count" in {issue.code for issue in result.issues}
    assert result.score == 95  # 100 - one page_count warning
    assert result.model_estimate == 92
