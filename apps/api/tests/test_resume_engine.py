from __future__ import annotations

import io
import json
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from pypdf import PdfWriter

from job_os.schemas.resumes import SelectedBullet
from job_os.services.resume_engine import (
    _github_repositories,
    _repo_from_url,
    deterministic_review,
    generate_latex_source,
    validate_json_resume_document,
)
from job_os.services.tailor import _sanitize_selected_bullets

from _fake_llm import StreamingFakeMessages


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


def test_a_second_page_is_advice_and_unreadable_text_is_still_a_defect() -> None:
    """Two pages is a judgement call; a PDF an ATS cannot read is not.

    Blocking on page count alone sent a resume that is a page and a bit back for
    another round of editing, which is the same over-blocking that the false
    fabrication flags caused. The renderer will not shrink margins or fonts to fake
    a one-page fit, so the honest options are to trim or to change template, and
    the message names both.
    """
    issues, page_count, text_selectable = deterministic_review(
        {"basics": {}, "work": []},
        _blank_pdf(2),
    )

    assert page_count == 2
    assert text_selectable is False
    by_code = {issue.code: issue for issue in issues}
    assert by_code["page_count"].severity == "warning"
    assert "sb2nov" in by_code["page_count"].message
    # Everything else keeps the severity it had.
    assert by_code["selectable_text"].severity == "blocking"
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


def test_json_resume_validation_rejects_malformed_sections() -> None:
    with pytest.raises(ValueError, match="work"):
        validate_json_resume_document({"basics": {}, "work": {"name": "EPAM"}})
    with pytest.raises(ValueError, match="highlights"):
        validate_json_resume_document(
            {"basics": {}, "projects": [{"name": "Bad", "highlights": "not-a-list"}]}
        )


def test_deterministic_review_does_not_police_employer_or_skills() -> None:
    """The scorer checks the document, not the truth of the career history.

    It used to emit blocking issues for any employer other than one hardcoded
    name and for specific languages, which meant a new job or a newly learned
    language permanently blocked finalizing. Grounding bullets in verified facts
    is what enforces honesty; see career_ops_rules for the model's guardrail.
    """
    issues, _, _ = deterministic_review(
        {
            "basics": {
                "name": "A Candidate",
                "email": "a@b.com",
                "phone": "+1 555 0100",
            },
            "work": [{"name": "Invented Corp"}],
            "projects": [{"name": "One"}, {"name": "Two"}],
            "skills": [{"name": "Languages", "keywords": ["Python", "C++"]}],
        },
        _blank_pdf(),
    )
    codes = {issue.code for issue in issues}
    assert "unsupported_employer" not in codes
    assert "unsupported_skill" not in codes


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


def _fake_revision_client(replies: list[str], calls: list[Any]) -> Any:
    """Anthropic stand-in that returns `replies` in order and records requests."""

    class FakeMessages(StreamingFakeMessages):
        async def create(self, **kwargs: Any) -> Any:
            calls.append(kwargs)
            body = replies[min(len(calls) - 1, len(replies) - 1)]
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text=body)]
            )

    return SimpleNamespace(messages=FakeMessages())


_MINIMAL_RESUME = {
    "basics": {"name": "A", "email": "a@b.c", "phone": "1"},
    "work": [],
}


@pytest.mark.asyncio
async def test_revision_recovers_when_the_model_answers_with_prose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shape that 400'd in production must now retry and succeed."""
    from job_os.services import resume_engine

    good = json.dumps(
        {
            "assistant_message": "Trimmed the summary.",
            "suggestions": [],
            "json_resume": _MINIMAL_RESUME,
        }
    )
    prose = '**Assistant message:**\nSure, I will run the "Review" action myself'
    calls: list[Any] = []
    monkeypatch.setattr(
        resume_engine, "_client", lambda: _fake_revision_client([prose, good], calls)
    )

    async def no_github(*_a: Any, **_k: Any) -> Any:
        return {}, [], []

    monkeypatch.setattr(resume_engine, "load_github_context", no_github)

    output = await resume_engine.revise_resume(
        _MINIMAL_RESUME, message="shorten the summary", verified_facts=[]
    )

    assert output.assistant_message == "Trimmed the summary."
    # One corrective retry, and it showed the model its own bad reply.
    assert len(calls) == 2
    retry_messages = calls[1]["messages"]
    assert retry_messages[-2]["role"] == "assistant"
    assert "Review" in retry_messages[-2]["content"]
    assert "not valid JSON" in retry_messages[-1]["content"]


@pytest.mark.asyncio
async def test_revision_accepts_json_wrapped_in_prose_without_retrying(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from job_os.services import resume_engine

    chatty = "Here you go:\n```json\n" + json.dumps(
        {
            "assistant_message": "Done.",
            "suggestions": ["tighten bullets"],
            "json_resume": _MINIMAL_RESUME,
        }
    ) + "\n```\nLet me know."
    calls: list[Any] = []
    monkeypatch.setattr(
        resume_engine, "_client", lambda: _fake_revision_client([chatty], calls)
    )

    async def no_github(*_a: Any, **_k: Any) -> Any:
        return {}, [], []

    monkeypatch.setattr(resume_engine, "load_github_context", no_github)

    output = await resume_engine.revise_resume(
        _MINIMAL_RESUME, message="tidy it", verified_facts=[]
    )

    assert output.assistant_message == "Done."
    assert len(calls) == 1  # extraction handled it, no retry needed


@pytest.mark.asyncio
async def test_revision_raises_a_readable_error_when_retry_also_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from job_os.services import resume_engine

    calls: list[Any] = []
    monkeypatch.setattr(
        resume_engine,
        "_client",
        lambda: _fake_revision_client(["nope", "still nope"], calls),
    )

    async def no_github(*_a: Any, **_k: Any) -> Any:
        return {}, [], []

    monkeypatch.setattr(resume_engine, "load_github_context", no_github)

    with pytest.raises(ValueError, match="could not produce a usable revision"):
        await resume_engine.revise_resume(
            _MINIMAL_RESUME, message="x", verified_facts=[]
        )
    assert len(calls) == 2


def test_github_evidence_follows_the_resume_not_a_hardcoded_username() -> None:
    """Any owner works: a handle change must not silently stop evidence loading.

    This used to accept a repository only when the owner matched one hardcoded
    username, so hosting a project under an organisation dropped its evidence
    and cost the review points via github_evidence_unavailable.
    """
    repos = _github_repositories(
        {
            "projects": [
                {"name": "Org Project", "url": "https://github.com/some-org/thing"},
                {"name": "Personal", "url": "https://github.com/a-new-handle/other"},
            ]
        }
    )
    assert set(repos.values()) == {
        ("some-org", "thing"),
        ("a-new-handle", "other"),
    }


def test_non_github_project_urls_are_ignored() -> None:
    repos = _github_repositories(
        {"projects": [{"name": "Site", "url": "https://example.com/not-a-repo"}]}
    )
    assert repos == {}
