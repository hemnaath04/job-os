"""Phase 9: test each documented finalize gate in isolation.

README.md:109-114 documents five conditions for finalizing:
    score >= 90, no blocking issue, PDF exactly one page,
    PDF contains selectable text, required contact fields + experience present.

`passed` is computed at resume_engine.py:916-920 as:
    model_review is not None and score >= PASS_SCORE and not any(blocking)
so a documented gate only blocks if it arrives as a *blocking* issue. This test
renders real PDFs and asks which severities the gates actually produce.

    cd apps/api && .venv/bin/python -m pytest ../../apps/api/tests/test_finalize_gates.py -v -s
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from job_os.services.resume_engine import (
    PASS_SCORE,
    _score_from_issues,
    deterministic_review,
)


def base_doc(n_roles: int = 1, bullets_per_role: int = 3) -> dict:
    return {
        "basics": {
            "name": "A Candidate",
            "email": "a@example.com",
            "phone": "+1 555 0100",
            "location": {"city": "Boston", "region": "MA"},
            "summary": "Test automation engineer with API and UI coverage experience.",
            # Links and a full graduation date: the reader-side checks treat
            # their absence as a warning apiece, and this fixture is here to
            # test the PDF gates, not to re-test those.
            "profiles": [
                {"network": "GitHub", "url": "https://github.com/acandidate"},
                {"network": "LinkedIn", "url": "https://linkedin.com/in/acandidate"},
            ],
        },
        "work": [
            {
                "name": f"Employer {i}",
                "position": "Test Automation Engineer",
                "startDate": "2021-01",
                "endDate": "2023-06",
                "highlights": [
                    f"Built and maintained API regression suites for service {i}-{j} "
                    "using TestNG and Selenium, covering authentication, pagination "
                    "and error handling across the public endpoints."
                    for j in range(bullets_per_role)
                ],
            }
            for i in range(n_roles)
        ],
        "education": [
            {"institution": "Northeastern University", "area": "Computer Science",
             "studyType": "MS", "startDate": "2026-01", "endDate": "2028-05"}
        ],
        # Only what the bullets above actually demonstrate. "pytest" was listed
        # here while no bullet used it, which is the "listed without showing how
        # it was used" gap the reader checks now catch.
        "skills": [{"name": "Testing", "keywords": ["TestNG", "Selenium"]}],
    }


def render(doc: dict) -> bytes:
    from job_os.services.latex_render import render_resume_pdf

    # render_resume_pdf returns a RenderedPdf wrapper; deterministic_review wants bytes.
    return render_resume_pdf(doc).bytes_


def severities(issues) -> dict[str, str]:
    return {i.code: i.severity for i in issues}


def passed_from(issues, *, model_review_present: bool = True) -> bool:
    """Reproduce resume_engine.py:916-920 exactly."""
    score, _ = _score_from_issues(issues)
    return (
        model_review_present
        and score >= PASS_SCORE
        and not any(i.severity == "blocking" for i in issues)
    )


@pytest.mark.slow
def test_one_page_document_is_clean() -> None:
    doc = base_doc(n_roles=1, bullets_per_role=3)
    pdf = render(doc)
    issues, page_count, selectable = deterministic_review(doc, pdf)
    print(f"\n1-page: pages={page_count} selectable={selectable} "
          f"issues={severities(issues)} score={_score_from_issues(issues)[0]}")
    assert page_count == 1
    assert selectable


@pytest.mark.slow
def test_multi_page_document_only_warns_and_still_passes() -> None:
    """The README calls one page a requirement. The code calls it advice."""
    doc = base_doc(n_roles=8, bullets_per_role=6)
    pdf = render(doc)
    issues, page_count, selectable = deterministic_review(doc, pdf)
    sev = severities(issues)
    score, _ = _score_from_issues(issues)
    print(f"\nmulti-page: pages={page_count} selectable={selectable} "
          f"issues={sev} score={score} passed={passed_from(issues)}")

    assert page_count > 1, "fixture did not produce a multi-page render"
    assert sev.get("page_count") == "warning", sev
    assert not any(s == "blocking" for s in sev.values()), sev
    assert score >= PASS_SCORE, score
    assert passed_from(issues) is True, (
        "a multi-page resume would finalize despite README.md:112 listing "
        "'a PDF that is exactly one page' as a requirement"
    )


def test_no_pdf_runtime_warns_and_reports_page_count_zero() -> None:
    """The Appwrite function path: no LaTeX engine, so no render."""
    doc = base_doc()
    issues, page_count, selectable = deterministic_review(doc, b"")
    sev = severities(issues)
    print(f"\nno-render: pages={page_count} selectable={selectable} issues={sev} "
          f"score={_score_from_issues(issues)[0]} passed={passed_from(issues)}")

    assert page_count == 0
    assert selectable is False
    assert sev.get("render_unavailable") == "warning"
    assert not any(s == "blocking" for s in sev.values()), sev
    assert passed_from(issues) is True, (
        "a runtime that cannot count pages would still finalize"
    )


def test_selectable_text_is_the_one_gate_that_blocks() -> None:
    """An image-only / textless PDF must be rejected. Simulated with a PDF that
    has pages but no extractable text layer."""
    import io

    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    writer.write(buf)

    doc = base_doc()
    issues, page_count, selectable = deterministic_review(doc, buf.getvalue())
    sev = severities(issues)
    print(f"\ntextless: pages={page_count} selectable={selectable} issues={sev} "
          f"passed={passed_from(issues)}")

    assert selectable is False
    assert sev.get("selectable_text") == "blocking"
    assert passed_from(issues) is False


@pytest.mark.parametrize("field", ["name", "email", "phone"])
def test_missing_contact_field(field: str) -> None:
    doc = base_doc()
    doc["basics"].pop(field, None)
    issues, _, _ = deterministic_review(doc, b"")
    sev = severities(issues)
    print(f"\nmissing {field}: issues={sev} passed={passed_from(issues)}")


def test_no_professional_experience() -> None:
    doc = base_doc()
    doc["work"] = []
    issues, _, _ = deterministic_review(doc, b"")
    sev = severities(issues)
    print(f"\nno work: issues={sev} score={_score_from_issues(issues)[0]} "
          f"passed={passed_from(issues)}")


def test_score_boundary_is_inclusive() -> None:
    """README says 'at least', code says >=. Confirm at exactly PASS_SCORE."""
    from job_os.schemas.resumes import ResumeReviewIssue

    five_warnings = [
        ResumeReviewIssue(severity="warning", code=f"w{i}", message="m") for i in range(5)
    ]
    assert _score_from_issues(five_warnings)[0] == Decimal(75) == PASS_SCORE
    assert passed_from(five_warnings) is True, "boundary must be inclusive"

    six_warnings = five_warnings + [
        ResumeReviewIssue(severity="warning", code="w6", message="m")
    ]
    assert _score_from_issues(six_warnings)[0] == Decimal(70)
    assert passed_from(six_warnings) is False


def test_blocking_issue_vetoes_even_at_score_100() -> None:
    from job_os.schemas.resumes import ResumeReviewIssue

    only_blocking = [ResumeReviewIssue(severity="blocking", code="b", message="m")]
    score, _ = _score_from_issues(only_blocking)
    assert score == Decimal(80)
    assert passed_from(only_blocking) is False

    # And with an empty issue list the score is a perfect 100 and it passes,
    # which is the control for the above.
    assert _score_from_issues([])[0] == Decimal(100)
    assert passed_from([]) is True


def test_model_review_absent_never_passes() -> None:
    assert passed_from([], model_review_present=False) is False
