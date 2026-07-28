from __future__ import annotations

import io
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pypdf import PdfWriter

from job_os.schemas.resumes import SelectedBullet
from job_os.services.pdf_render import render_resume_html
from job_os.services.resume_engine import (
    _github_repositories,
    _repo_from_url,
    deterministic_review,
    generate_latex_source,
    validate_json_resume_document,
)
from job_os.services.tailor import _sanitize_selected_bullets


def _blank_pdf(pages: int = 1) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    stream = io.BytesIO()
    writer.write(stream)
    return stream.getvalue()


def test_generate_latex_source_keeps_json_resume_canonical() -> None:
    doc = {
        "basics": {
            "name": "Hemnaath Balasubramani",
            "email": "balasubramani.h@northeastern.edu",
            "phone": "+1 (857) 379-6762",
        },
        "projects": [
            {
                "name": "BedRocked",
                "description": "Civic sewer sequencing",
                "startDate": "2026-06",
                "endDate": "2026-06",
                "highlights": ["Scored 2,404 segments with a 6-factor model."],
            }
        ],
        "skills": [{"name": "Backend", "keywords": ["Python", "FastAPI"]}],
    }

    latex = generate_latex_source(doc)

    assert "Hemnaath Balasubramani" in latex
    assert "BedRocked" in latex
    assert r"2,404 segments with a 6-factor model" in latex
    assert r"\section{Technical Skills}" in latex


def test_deterministic_review_blocks_non_selectable_and_multi_page_pdf() -> None:
    issues, page_count, text_selectable = deterministic_review(
        {"basics": {}, "work": []},
        _blank_pdf(2),
    )

    assert page_count == 2
    assert text_selectable is False
    assert {issue.code for issue in issues} >= {
        "page_count",
        "selectable_text",
        "missing_name",
        "missing_email",
        "missing_phone",
        "missing_experience",
    }


def test_repo_parser_accepts_project_urls() -> None:
    assert _repo_from_url("https://github.com/hemnaath04/claimfarm") == (
        "hemnaath04",
        "claimfarm",
    )
    assert _repo_from_url("https://example.com/project") is None


def test_preview_escapes_markup_and_rejects_javascript_links() -> None:
    html = render_resume_html(
        {
            "basics": {
                "name": '<script>alert("name")</script>',
                "email": 'safe@example.com"><script>alert("email")</script>',
                "profiles": [
                    {
                        "network": "GitHub",
                        "username": "unsafe",
                        "url": "javascript:alert(1)",
                    }
                ],
            },
            "projects": [
                {
                    "name": "Project",
                    "description": "<img src=x onerror=alert(1)>",
                    "url": "javascript:alert(2)",
                }
            ],
        }
    )

    assert "<script>" not in html
    assert "<img src=x" not in html
    assert "href=\"javascript:" not in html
    assert "&lt;script&gt;" in html


def test_json_resume_validation_rejects_malformed_sections() -> None:
    with pytest.raises(ValueError, match="work"):
        validate_json_resume_document({"basics": {}, "work": {"name": "EPAM"}})
    with pytest.raises(ValueError, match="highlights"):
        validate_json_resume_document(
            {"basics": {}, "projects": [{"name": "Bad", "highlights": "not-a-list"}]}
        )


def test_deterministic_review_blocks_unknown_employer_and_skills() -> None:
    issues, _, _ = deterministic_review(
        {
            "basics": {
                "name": "Hemnaath Balasubramani",
                "email": "balasubramani.h@northeastern.edu",
                "phone": "+1 (857) 379-6762",
            },
            "work": [{"name": "Invented Corp"}],
            "projects": [{"name": "One"}, {"name": "Two"}],
            "skills": [{"name": "Languages", "keywords": ["Python", "C++"]}],
        },
        _blank_pdf(),
    )
    codes = {issue.code for issue in issues}
    assert "unsupported_employer" in codes
    assert "unsupported_skill" in codes


def test_role_reveal_resolves_extension_and_backend_evidence() -> None:
    repos = _github_repositories(
        {"projects": [{"name": "RoleReveal", "highlights": []}]}
    )
    assert set(repos.values()) == {
        ("hemnaath04", "rolereveal"),
        ("hemnaath04", "rolereveal-backend"),
    }


def test_tailor_reverts_rewrite_that_adds_unverified_technology() -> None:
    # Ids are strings across the tailoring contract so Appwrite ids work too.
    fact_id = str(uuid4())
    bullet_id = str(uuid4())
    source = SimpleNamespace(
        id=bullet_id,
        fact_id=fact_id,
        text="Built Python API tests from detailed specifications.",
    )
    fact = SimpleNamespace(
        id=fact_id,
        kind="experience",
        payload={"keywords": ["Python"]},
    )
    rewritten = SelectedBullet(
        fact_bullet_id=bullet_id,
        rewritten_text="Built Kubernetes and Python services.",
        target_section="work",
    )

    result = _sanitize_selected_bullets(
        [rewritten],
        bullets_by_id={bullet_id: source},
        facts_by_id={fact_id: fact},
    )

    assert result[0].rewritten_text == source.text
