from __future__ import annotations

import io

from pypdf import PdfWriter

from job_os.services.resume_engine import (
    _repo_from_url,
    deterministic_review,
    generate_latex_source,
)


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
