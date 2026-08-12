"""The quality gate has to score a real PDF, wherever the PDF came from.

Page count and selectable text are the two checks the gate exists for, and until
`rendered_pdf` existed they were skipped on every review that actually ran in
production. The reviewer rendered its own PDF, the Appwrite function has no Typst
or Tectonic binary, so it reviewed `b""` and reported "0 pages, selectable text
not checked" on every resume it ever scored. The browser had a real PDF the whole
time, from `/resumes/render`, and only needed a way to hand it over.

These tests pin that handover: bytes in means the checks run, no bytes and no
engine still degrades rather than raising, and finalize stores exactly what was
reviewed.
"""
from __future__ import annotations

import io
import json
import os
from types import SimpleNamespace
from typing import Any

import pytest

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://job_os:job_os@localhost/job_os"
)

from _fake_llm import StreamingFakeMessages  # noqa: E402
from job_os.services import resume_engine  # noqa: E402

DOC = {
    "basics": {"name": "A Candidate", "email": "a@b.com", "phone": "555-0100"},
    "work": [
        {
            "name": "Acme",
            "position": "Engineer",
            "highlights": ["Wrote the pricing test suite."],
        }
    ],
}

MODEL_REPLY = {
    "score": 90,
    "issues": [],
    "strengths": ["Reads clearly."],
    "summary": "Solid.",
}


def _one_page_pdf() -> bytes:
    """A real, parseable one-page PDF with no text in it.

    No text on purpose: it makes the selectable-text check fire, which is how a
    test tells "the check ran and failed" apart from "the check was skipped".
    """
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


@pytest.fixture()
def stub_review(monkeypatch: pytest.MonkeyPatch) -> None:
    """Everything the reviewer reaches for that is not the point of these tests."""

    class FakeMessages(StreamingFakeMessages):
        async def create(self, **_kwargs: Any) -> Any:
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text=json.dumps(MODEL_REPLY))]
            )

    monkeypatch.setattr(
        resume_engine, "_client", lambda: SimpleNamespace(messages=FakeMessages())
    )

    async def no_github(*_a: Any, **_k: Any) -> Any:
        return {}, [], {}

    monkeypatch.setattr(resume_engine, "load_github_context", no_github)


def _codes(review: Any) -> set[str]:
    return {issue.code for issue in review.issues}


@pytest.mark.asyncio
async def test_a_supplied_pdf_is_reviewed_without_rendering_anything(
    monkeypatch: pytest.MonkeyPatch, stub_review: None
) -> None:
    pdf = _one_page_pdf()

    def must_not_render(*_a: Any, **_k: Any) -> Any:
        raise AssertionError(
            "review_resume rendered its own PDF despite being handed one. "
            "The runtime that needs this has no engine to render with."
        )

    monkeypatch.setattr(resume_engine, "render_resume_pdf", must_not_render)

    review, pdf_bytes = await resume_engine.review_resume(DOC, rendered_pdf=pdf)

    # The checks ran: selectable_text fires on a blank page, and the "we could not
    # render, so we skipped them" warning is gone.
    assert "selectable_text" in _codes(review)
    assert "render_unavailable" not in _codes(review)
    # Finalize stores these bytes, so they have to be the ones just reviewed.
    assert pdf_bytes == pdf


@pytest.mark.asyncio
async def test_no_pdf_and_no_engine_still_degrades_rather_than_raising(
    monkeypatch: pytest.MonkeyPatch, stub_review: None
) -> None:
    # The old behaviour, kept deliberately: a tailored draft with no page count is
    # better than no draft at all, and the warning says which checks did not run.
    def unavailable(*_a: Any, **_k: Any) -> Any:
        raise resume_engine.TectonicUnavailableError("no engine here")

    monkeypatch.setattr(resume_engine, "render_resume_pdf", unavailable)

    review, pdf_bytes = await resume_engine.review_resume(DOC)

    assert "render_unavailable" in _codes(review)
    assert "selectable_text" not in _codes(review)
    assert pdf_bytes == b""


@pytest.mark.asyncio
async def test_an_empty_supplied_pdf_is_not_mistaken_for_a_real_one(
    monkeypatch: pytest.MonkeyPatch, stub_review: None
) -> None:
    # `b""` is what a failed upload reads back as. It must take the skipped-checks
    # path and say so, not claim a zero-page resume was measured.
    monkeypatch.setattr(
        resume_engine,
        "render_resume_pdf",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("should not render")),
    )

    review, pdf_bytes = await resume_engine.review_resume(DOC, rendered_pdf=b"")

    assert "render_unavailable" in _codes(review)
    assert pdf_bytes == b""
