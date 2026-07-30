"""Building a template from an upload, including the compile-repair loop.

The loop is the point of this module: a model writing LaTeX gets it wrong often
enough that a single attempt would mean regularly telling the user their design
cannot be used. So the compiler's own error goes back to the model, up to a few
times, and a template that never compiles is never stored.
"""
from __future__ import annotations

import json
import os
from types import SimpleNamespace
from typing import Any

import pytest

# Settings are constructed on the first gateway call, and they require a
# database URL that nothing in this module touches.
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://job_os:job_os@localhost/job_os",
)
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from job_os.services import latex_from_document as lfd  # noqa: E402
from job_os.services.latex_render import LatexRenderError  # noqa: E402

from _fake_llm import StreamingFakeMessages  # noqa: E402


def _reply(latex_source: str, *, name: str = "Two Column Slate") -> Any:
    payload = json.dumps({"name": name, "latex_source": latex_source, "notes": "A note."})
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=payload)],
        stop_reason="end_turn",
        usage=SimpleNamespace(input_tokens=10, output_tokens=10),
    )


class _Replies(StreamingFakeMessages):
    """Hands back a scripted reply per call, recording what it was asked."""

    def __init__(self, replies: list[Any]) -> None:
        self.replies = replies
        self.prompts: list[str] = []

    async def create(self, **kwargs: Any) -> Any:
        messages = kwargs.get("messages") or []
        last = messages[-1]["content"]
        self.prompts.append(last if isinstance(last, str) else json.dumps(last, default=str))
        return self.replies[len(self.prompts) - 1]


@pytest.fixture
def fake_gateway(monkeypatch: pytest.MonkeyPatch):
    def install(replies: list[Any]) -> _Replies:
        messages = _Replies(replies)
        monkeypatch.setattr(
            lfd, "_client", lambda: SimpleNamespace(messages=messages)
        )
        return messages

    return install


GOOD_TEMPLATE = r"""
\documentclass[11pt,letterpaper]{article}
\usepackage[margin=1in]{geometry}
\begin{document}
\begin{center}{\Large\bfseries <<name>>}\end{center}
<% if summary %><<summary>><% endif %>
<% for job in work %>
\textbf{<<job.company>>} \hfill <<job.dates>>\\
<<job.position>>
<% for bullet in job.bullets %>
\begin{itemize}\item <<bullet>>\end{itemize}
<% endfor %>
<% endfor %>
\end{document}
"""

BROKEN_TEMPLATE = r"""
\documentclass[11pt,letterpaper]{article}
\begin{document}
<<name>> \thisCommandDoesNotExist
\end{document}
"""


def test_only_tex_and_pdf_are_accepted() -> None:
    for filename in ("design.docx", "design.png", "resume.doc"):
        with pytest.raises(lfd.TemplateBuildError, match="Upload a .tex"):
            lfd._document_blocks(b"x", filename)


def test_a_tex_upload_is_sent_as_source_and_a_pdf_as_a_document() -> None:
    blocks, instruction = lfd._document_blocks(b"\\documentclass{article}", "mine.tex")
    assert blocks[0]["type"] == "text"
    assert "Turn it into a template" in instruction

    blocks, instruction = lfd._document_blocks(b"%PDF-1.7 fake", "mine.pdf")
    assert blocks[0]["type"] == "document"
    assert "reconstructing a design" in instruction


@pytest.mark.parametrize(
    "source",
    [
        r"\documentclass{article}\immediate\write18{cat /etc/passwd}",
        r"\documentclass{article}\input{/etc/passwd}",
        r"\documentclass{article}\include{../../secrets}",
        r"\documentclass{article}\openin1=/etc/passwd",
    ],
)
def test_a_template_that_reads_files_is_refused(source: str) -> None:
    """The compile runs in our container, so this is not a style objection."""
    with pytest.raises(lfd.TemplateBuildError):
        lfd._reject_unsafe(source)


def test_the_contract_sent_to_the_model_matches_the_renderer() -> None:
    """Drift here means a model writing against names that do not exist."""
    from job_os.services.latex_render import build_render_model

    for key in build_render_model({}):
        assert key in lfd.CONTRACT, key
    # The engine's real limits, which the model has no other way to know.
    assert "fontawesome6" in lfd.CONTRACT
    assert "XeTeX" in lfd.CONTRACT


@pytest.mark.asyncio
async def test_a_compiling_template_is_returned_on_the_first_pass(fake_gateway) -> None:
    from job_os.services.latex_render import tectonic_binary

    if tectonic_binary() is None:
        pytest.skip("tectonic is not installed on this machine")

    fake_gateway([_reply(GOOD_TEMPLATE)])
    candidate = await lfd.build_template_from_upload(
        b"\\documentclass{article}", "mine.tex"
    )
    assert candidate.attempts == 1
    assert candidate.repairs == []
    assert candidate.pdf_bytes.startswith(b"%PDF")
    assert "<<name>>" in candidate.latex_source


@pytest.mark.asyncio
async def test_a_failing_template_is_repaired_with_the_compiler_error(
    fake_gateway,
) -> None:
    from job_os.services.latex_render import tectonic_binary

    if tectonic_binary() is None:
        pytest.skip("tectonic is not installed on this machine")

    messages = fake_gateway([_reply(BROKEN_TEMPLATE), _reply(GOOD_TEMPLATE)])
    candidate = await lfd.build_template_from_upload(
        b"\\documentclass{article}", "mine.tex"
    )
    assert candidate.attempts == 2
    assert candidate.repairs and "Undefined control sequence" in candidate.repairs[0]
    # The model was shown the compiler's own words, not a paraphrase.
    assert "did not compile" in messages.prompts[1]
    assert "thisCommandDoesNotExist" in messages.prompts[1]


@pytest.mark.asyncio
async def test_nothing_is_returned_when_every_attempt_fails(fake_gateway) -> None:
    from job_os.services.latex_render import tectonic_binary

    if tectonic_binary() is None:
        pytest.skip("tectonic is not installed on this machine")

    fake_gateway([_reply(BROKEN_TEMPLATE) for _ in range(lfd.MAX_ATTEMPTS)])
    with pytest.raises(lfd.TemplateBuildError, match="after 4 attempts"):
        await lfd.build_template_from_upload(b"\\documentclass{article}", "mine.tex")


@pytest.mark.asyncio
async def test_a_missing_engine_is_not_treated_as_a_bad_template(
    fake_gateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Do not spend three more model calls rediscovering a deployment fact."""
    messages = fake_gateway([_reply(GOOD_TEMPLATE) for _ in range(lfd.MAX_ATTEMPTS)])

    def no_engine(*_args: Any, **_kwargs: Any) -> bytes:
        raise lfd.TectonicUnavailableError("no tectonic here")

    monkeypatch.setattr(lfd, "compile_pdf", no_engine)
    with pytest.raises(lfd.TectonicUnavailableError):
        await lfd.build_template_from_upload(b"\\documentclass{article}", "mine.tex")
    assert len(messages.prompts) == 1


def test_an_empty_render_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lfd, "compile_pdf", lambda *a, **k: b"%PDF-1.7 tiny")
    with pytest.raises(lfd.TemplateBuildError, match="essentially empty"):
        lfd.validate_template(GOOD_TEMPLATE)


def test_a_compile_error_carries_the_log() -> None:
    """The repair prompt is only useful if the log survives the raise."""
    error = LatexRenderError("failed", log="! Undefined control sequence.")
    assert "Undefined control sequence" in lfd._failure_note(error)
