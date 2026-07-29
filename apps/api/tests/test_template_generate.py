from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from job_os.services import template_generate
from job_os.services.pdf_render import render_resume_html, resume_context
from job_os.services.template_generate import (
    SAMPLE_RESUME,
    TemplateGenerationError,
    generate_template_from_document,
)

GOOD_HTML = (
    "<html><head></head><body>"
    "<h1>{{ basics.name }}</h1>"
    "{% if basics.summary %}<p>{{ basics.summary }}</p>{% endif %}"
    "{% for job in work %}<div>{{ job.position }} "
    "{{ fmt_range(job.startDate, job.endDate) }}</div>{% endfor %}"
    "{% for award in awards %}<div>{{ award.title }}</div>{% endfor %}"
    "</body></html>"
)


def _reply(html: str = GOOD_HTML, name: str = "Two Column Serif") -> str:
    return json.dumps(
        {"name": name, "html_source": html, "css_source": "h1 { font-size: 18pt; }",
         "notes": "A serif single column."}
    )


def _stub(
    monkeypatch: pytest.MonkeyPatch,
    replies: list[str],
    *,
    render_ok: bool = True,
) -> list[Any]:
    calls: list[Any] = []

    class FakeMessages:
        async def create(self, **kwargs: Any) -> Any:
            calls.append(kwargs)
            body = replies[min(len(calls) - 1, len(replies) - 1)]
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=body)])

    monkeypatch.setattr(
        template_generate, "_client", lambda: SimpleNamespace(messages=FakeMessages())
    )

    # WeasyPrint's native libs are not installed on every dev machine, so the
    # PDF step is stubbed. The Jinja render underneath it is exercised for real
    # by the tests below that call render_resume_html directly.
    def fake_render(doc: dict[str, Any], **kwargs: Any) -> Any:
        render_resume_html(
            doc,
            html_source=kwargs.get("html_source"),
            css_source=kwargs.get("css_source"),
        )
        body = b"%PDF-1.7" + b"x" * 2000 if render_ok else b"not a pdf"
        return SimpleNamespace(bytes_=body)

    monkeypatch.setattr(template_generate, "render_resume_pdf", fake_render)
    return calls


PDF_BYTES = b"%PDF-1.4 fake"


@pytest.mark.asyncio
async def test_a_working_template_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub(monkeypatch, [_reply()])
    candidate = await generate_template_from_document(PDF_BYTES, "design.pdf")
    assert candidate.name == "Two Column Serif"
    assert "{{ basics.name }}" in candidate.html_source
    assert candidate.pdf_bytes.startswith(b"%PDF")
    assert len(calls) == 1
    # The document went to the model as a PDF block so it can see the layout.
    assert calls[0]["messages"][0]["content"][0]["type"] == "document"


@pytest.mark.asyncio
async def test_a_broken_template_is_repaired_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A template referencing a name the renderer does not pass must not be stored."""
    broken = _reply("<html><body>{{ candidate.full_name }}</body></html>")
    calls = _stub(monkeypatch, [broken, _reply()])
    candidate = await generate_template_from_document(PDF_BYTES, "design.pdf")
    assert "{{ basics.name }}" in candidate.html_source
    assert len(calls) == 2
    # The repair turn showed the model its own output and the actual error.
    repair = calls[1]["messages"]
    assert repair[-2]["role"] == "assistant"
    assert "That did not work" in repair[-1]["content"]


@pytest.mark.asyncio
async def test_two_failures_raise_so_the_caller_can_fall_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broken = _reply("{% for x in nope %}{{ x.y }}{% endfor %}{{ nope.crash }}")
    _stub(monkeypatch, [broken, broken])
    with pytest.raises(TemplateGenerationError, match="Could not build a template"):
        await generate_template_from_document(PDF_BYTES, "design.pdf")


@pytest.mark.asyncio
async def test_prose_instead_of_json_is_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _stub(monkeypatch, ["Here is a lovely template for you!", _reply()])
    candidate = await generate_template_from_document(PDF_BYTES, "design.pdf")
    assert candidate.name == "Two Column Serif"
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_a_template_that_renders_blank_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub(monkeypatch, [_reply(), _reply()], render_ok=False)
    with pytest.raises(TemplateGenerationError):
        await generate_template_from_document(PDF_BYTES, "design.pdf")


@pytest.mark.asyncio
async def test_an_unsupported_file_type_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub(monkeypatch, [_reply()])
    with pytest.raises(TemplateGenerationError, match="Only PDF and DOCX"):
        await generate_template_from_document(b"...", "design.txt")


def test_the_sample_resume_exercises_every_context_section() -> None:
    """Otherwise a template could crash the first time a real resume has awards."""
    names = set(resume_context({})) - {"fmt_range"}
    for name in names:
        value = SAMPLE_RESUME.get(name)
        assert value, f"SAMPLE_RESUME is missing {name}"


def test_the_sample_resume_catches_a_template_that_ignores_a_section() -> None:
    # A template touching a field the sample does not populate would slip
    # through; this proves the sample is rich enough to drive every branch.
    html = "".join(
        "{% for item in " + name + " %}{{ item }}{% endfor %}"
        for name in sorted(set(resume_context({})) - {"fmt_range", "basics"})
    )
    rendered = render_resume_html(SAMPLE_RESUME, html_source=html, css_source="")
    assert "Acme Corp" in rendered
    assert "Example Award" in rendered
