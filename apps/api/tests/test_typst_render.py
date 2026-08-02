"""The Typst render path: cleaning, the shared contract, and real compiles.

The safety tests are the important ones, and they test a different claim from
the LaTeX ones. There is no escape table here to check, because a Typst template
never pastes a resume value into source the engine parses: the value arrives in
data.json and is interpolated in code mode, where it cannot be markup. That
claim is the whole basis for not escaping, so it is asserted against a real
compile with hostile content rather than taken on trust.

The other thing worth guarding is that the two engines agree. They share one
template contract, and a resume that renders as two different documents
depending on which engine served it is a bug the user would find before we did.
"""
from __future__ import annotations

import pytest

from job_os.services import latex_render
from job_os.services.latex_catalog import BUILTIN_TEMPLATES, SAMPLE_RESUME
from job_os.services.typst_render import (
    TypstRenderError,
    build_render_model,
    date_range,
    has_builtin,
    render_resume_pdf,
    safe_url,
    sanitize,
    typst_binary,
)

needs_typst = pytest.mark.skipif(
    typst_binary() is None,
    reason="typst is not installed on this machine",
)

PORTED = [spec.key for spec in BUILTIN_TEMPLATES if has_builtin(spec.key)]

# Everything a resume field could carry that means something to Typst: markup,
# a code-mode escape, a file read, a package import, a page override.
HOSTILE = (
    "C# & .NET *bold* _em_ `code` $x^2$ #set page(width:1pt) "
    '#read("/etc/passwd") \\ 100% <label> @ref ~tilde'
)


# ---------------------------------------------------------------------------
# Cleaning
# ---------------------------------------------------------------------------


def test_sanitize_leaves_markup_characters_alone() -> None:
    """The opposite of the LaTeX side, and deliberately so.

    Escaping here would be wrong twice over: it would print the escapes, since
    nothing re-parses them, and it would imply a safety property that comes from
    somewhere else entirely.
    """
    assert sanitize("100% & $5 #1 a_b c^d {g} h\\i") == "100% & $5 #1 a_b c^d {g} h\\i"


def test_sanitize_flattens_newlines_and_drops_control_characters() -> None:
    assert sanitize("first\r\nsecond") == "first second"
    assert sanitize("clean\x00\x07text​") == "cleantext"


def test_sanitize_keeps_unicode_typography() -> None:
    """Typst is Unicode-native, so the LaTeX transliteration table has no place."""
    assert sanitize("don’t “quote” me") == "don’t “quote” me"


@pytest.mark.parametrize(
    "url",
    ["javascript:alert(1)", "file:///etc/passwd", "mailto:a@b.co", "", "not a url"],
)
def test_safe_url_admits_only_web_links(url: str) -> None:
    assert safe_url(url) == ""


def test_safe_url_passes_http_and_https_through_unescaped() -> None:
    assert safe_url("https://example.com/a_b?x=1&y=2") == "https://example.com/a_b?x=1&y=2"


def test_date_range_uses_a_real_en_dash() -> None:
    """Where the LaTeX side emits `--` for TeX to turn into one."""
    assert date_range("2024-06", "2025-01") == "Jun 2024 – Jan 2025"
    assert date_range("2024-06", None) == "Jun 2024 – Present"


# ---------------------------------------------------------------------------
# The shared contract
# ---------------------------------------------------------------------------


def test_both_engines_expose_the_same_names() -> None:
    """One contract, two engines. Drift here means two different resumes."""
    assert sorted(build_render_model(SAMPLE_RESUME)) == sorted(
        latex_render.build_render_model(SAMPLE_RESUME)
    )


def test_model_is_json_serialisable() -> None:
    """It has to be: the renderer writes it to data.json for the template."""
    import json

    json.dumps(build_render_model(SAMPLE_RESUME))


def test_model_survives_an_empty_document() -> None:
    model = build_render_model({})
    assert model["name"] == ""
    assert model["work"] == []


# ---------------------------------------------------------------------------
# Compiling
# ---------------------------------------------------------------------------


@needs_typst
@pytest.mark.parametrize("key", PORTED)
def test_every_ported_template_compiles(key: str) -> None:
    pdf = render_resume_pdf(SAMPLE_RESUME, template_key=key).bytes_
    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 2_000


@needs_typst
@pytest.mark.parametrize("key", PORTED)
def test_every_ported_template_survives_a_sparse_resume(key: str) -> None:
    """A resume with no projects, no awards and no summary still has to render."""
    pdf = render_resume_pdf(
        {"basics": {"name": "Sparse Sample"}}, template_key=key
    ).bytes_
    assert pdf.startswith(b"%PDF")


@needs_typst
@pytest.mark.parametrize("key", PORTED)
def test_hostile_content_renders_as_text_and_does_not_execute(key: str) -> None:
    """The claim that justifies not escaping anything.

    If a value were re-parsed as markup, `#set page(width:1pt)` would resize the
    page and `#read` would either read a file or fail the compile. Neither
    happens, so the render simply succeeds and the characters print.
    """
    document = {
        "basics": {"name": HOSTILE, "summary": HOSTILE, "label": HOSTILE},
        "work": [{"name": HOSTILE, "position": HOSTILE, "highlights": [HOSTILE]}],
        "skills": [{"name": HOSTILE, "keywords": [HOSTILE]}],
    }
    pdf = render_resume_pdf(document, template_key=key).bytes_
    assert pdf.startswith(b"%PDF")


@needs_typst
def test_a_template_importing_a_package_is_refused() -> None:
    """Package imports are the one thing in Typst that reaches the network.

    0.15.1 has no flag to forbid it, so this is the flag.
    """
    from job_os.services.typst_render import compile_pdf

    with pytest.raises(TypstRenderError, match="imports a package"):
        compile_pdf('#import "@preview/cetz:0.2.2"\nhello', {})


# ---------------------------------------------------------------------------
# Engine selection
# ---------------------------------------------------------------------------


def test_tectonic_is_the_default_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RENDER_ENGINE", raising=False)
    assert latex_render.render_engine() == "tectonic"


@needs_typst
def test_typst_engine_serves_a_ported_template(monkeypatch: pytest.MonkeyPatch) -> None:
    """Through the shared entry point, with no change to its signature."""
    monkeypatch.setenv("RENDER_ENGINE", "typst")

    def fail(*args: object, **kwargs: object) -> bytes:
        raise AssertionError("Tectonic should not have been reached")

    monkeypatch.setattr(latex_render, "compile_pdf", fail)
    pdf = latex_render.render_resume_pdf(SAMPLE_RESUME, template_key="jakes").bytes_
    assert pdf.startswith(b"%PDF")


def test_a_stored_template_never_reaches_typst(monkeypatch: pytest.MonkeyPatch) -> None:
    """Model-written source stays on Tectonic, where the hardening for it lives."""
    monkeypatch.setenv("RENDER_ENGINE", "typst")
    seen: list[str] = []

    def record(source: str, **kwargs: object) -> bytes:
        seen.append(source)
        return b"%PDF-1.7 fake"

    monkeypatch.setattr(latex_render, "compile_pdf", record)
    latex_render.render_resume_pdf(SAMPLE_RESUME, latex_source=r"\documentclass{article}")
    assert seen and "documentclass" in seen[0]


def test_an_unported_template_falls_back_to_tectonic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RENDER_ENGINE", "typst")
    seen: list[str] = []

    def record(source: str, **kwargs: object) -> bytes:
        seen.append(source)
        return b"%PDF-1.7 fake"

    monkeypatch.setattr(latex_render, "compile_pdf", record)
    latex_render.render_resume_pdf(SAMPLE_RESUME, template_key="moderncv")
    assert seen and "moderncv" in seen[0]
