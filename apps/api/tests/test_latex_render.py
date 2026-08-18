"""The LaTeX render path: escaping, the template contract, and real compiles.

The escaping tests are the important ones. A resume field is data, and the
consequence of treating it as markup is not a broken layout, it is a document
that silently loses half a job title because somebody's employer had a `%` in it,
or a template that executes something a model wrote.

WHICH COMPILES RUN BY DEFAULT. Tectonic takes fifteen to thirty seconds per
document, so compiling all seven templates twice is minutes of a test run
nobody will wait for. Six of the seven also have a Typst port that renders the
same page in about a tenth of a second, and `render_resume_pdf` already chooses
between the two on `RENDER_ENGINE`. So the default run renders through that
switch, which is a real compile of a real template and additionally proves the
switch reaches the fast path for every template the catalogue marks ready.

The Tectonic compiles are still here, marked `slow` and run by `pytest -m slow`.
They are not redundant: the LaTeX sources are separate files from the Typst
ports, moderncv has no port at all, and escaping is a LaTeX property that has no
counterpart on the other engine. What changes is when they run, not whether.
"""
from __future__ import annotations

import io
from typing import Any

import pytest
from pypdf import PdfReader

from job_os.services.latex_catalog import BUILTIN_TEMPLATES, SAMPLE_RESUME
from job_os.services.latex_render import (
    LatexRenderError,
    build_render_model,
    builtin_directory,
    compile_pdf,
    fill_template,
    latex_escape,
    latex_escape_url,
    load_builtin_source,
    render_resume_pdf,
    tectonic_binary,
)
from job_os.services.typst_render import typst_binary

needs_tectonic = pytest.mark.skipif(
    tectonic_binary() is None,
    reason="tectonic is not installed on this machine",
)
needs_typst = pytest.mark.skipif(
    typst_binary() is None,
    reason="typst is not installed on this machine",
)

# The templates the catalogue says render honestly on the fast engine, and so
# the ones a deployment with RENDER_ENGINE=typst will really serve that way.
TYPST_READY = [spec.key for spec in BUILTIN_TEMPLATES if spec.typst_ready]


@pytest.fixture
def typst_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    """Render as a deployment with the fast path switched on, and prove it.

    Failing the Tectonic entry point rather than just setting the variable: a
    test that only asserted `%PDF` would still pass if the switch silently fell
    through, and it would take twenty seconds while doing it.
    """
    monkeypatch.setenv("RENDER_ENGINE", "typst")

    def unreachable(*_args: Any, **_kwargs: Any) -> bytes:
        raise AssertionError("Tectonic was reached for a template with a Typst port")

    monkeypatch.setattr(
        "job_os.services.latex_render.compile_pdf", unreachable
    )


def pdf_text(pdf: bytes) -> str:
    return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(pdf)).pages)


def test_escapes_every_reserved_character() -> None:
    assert latex_escape("100% & $5 #1 a_b c^d e~f {g} h\\i") == (
        r"100\% \& \$5 \#1 a\_b c\textasciicircum{}d e\textasciitilde{}f "
        r"\{g\} h\textbackslash{}i"
    )


def test_escape_neutralises_a_command() -> None:
    """A field carrying LaTeX must print as text, not run."""
    assert latex_escape(r"\input{/etc/passwd}") == (
        r"\textbackslash{}input\{/etc/passwd\}"
    )
    assert latex_escape(r"\write18{rm -rf /}") == (
        r"\textbackslash{}write18\{rm -rf /\}"
    )


def test_escape_maps_typography_to_latex_spellings() -> None:
    # Two of the bundled templates set Computer Modern, which has no glyph for
    # these, and a missing glyph prints nothing at all.
    assert latex_escape("2024–2025") == "2024--2025"
    assert latex_escape("“quoted” and ‘this’") == "``quoted'' and `this'"
    assert latex_escape("a…b") == r"a\dots{}b"


def test_escape_flattens_newlines_and_strips_control_characters() -> None:
    # A blank line in a field would end the enclosing item or table cell.
    assert latex_escape("one\n\ntwo") == "one  two"
    assert latex_escape("bad\x00char\x07here") == "badcharhere"


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "file:///etc/passwd",
        "data:text/html,<script>",
        r"https://example.com/\href{x}{y}",
        "https://example.com/a b",
        "not a url",
        "",
    ],
)
def test_only_web_links_survive(url: str) -> None:
    """A resume link is a web link. Anything else is dropped, not mangled."""
    assert latex_escape_url(url) == ""


def test_web_links_keep_working_and_escape_what_hyperref_needs() -> None:
    assert latex_escape_url("https://example.com/a_b?x=1&y=2#z") == (
        r"https://example.com/a\_b?x=1\&y=2\#z"
    )


def test_render_model_escapes_every_string_it_exposes() -> None:
    """Nothing in the model may still contain a live reserved character.

    This is the property the whole design rests on: templates receive escaped
    values only, so a template cannot leak an unescaped one no matter who wrote
    it.
    """
    hostile = "100% & $x #y _z ~w ^v {q} \\cmd"
    model = build_render_model(
        {
            "basics": {
                "name": hostile,
                "email": "a@b.com",
                "phone": hostile,
                "summary": hostile,
                "location": {"city": hostile, "region": hostile},
                "profiles": [{"network": hostile, "username": hostile, "url": "https://x.com/a"}],
            },
            "work": [
                {
                    "name": hostile,
                    "position": hostile,
                    "location": hostile,
                    "highlights": [hostile, hostile],
                }
            ],
            "education": [{"institution": hostile, "area": hostile, "studyType": hostile}],
            "projects": [{"name": hostile, "description": hostile, "keywords": [hostile]}],
            "skills": [{"name": hostile, "keywords": [hostile]}],
            "certificates": [{"name": hostile, "issuer": hostile}],
            "awards": [{"title": hostile, "awarder": hostile}],
            "languages": [{"language": hostile, "fluency": hostile}],
        }
    )

    def walk(value: Any) -> None:
        if isinstance(value, str):
            for char in ("%", "$", "#", "~", "^"):
                # Escaped forms all begin with a backslash, so a bare reserved
                # character means something got through unescaped.
                assert f" {char}" not in f" {value}".replace(f"\\{char}", ""), value
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(model)


def test_render_model_drops_a_hostile_profile_url_but_keeps_the_label() -> None:
    model = build_render_model(
        {
            "basics": {
                "name": "A Candidate",
                "profiles": [{"network": "GitHub", "username": "x", "url": "javascript:alert(1)"}],
            }
        }
    )
    assert model["profiles"][0]["url"] == ""
    assert model["profiles"][0]["username"] == "x"


def test_missing_sections_render_as_nothing_rather_than_failing() -> None:
    model = build_render_model({"basics": {"name": "A Candidate"}})
    assert model["work"] == []
    assert model["github"] is None
    for spec in BUILTIN_TEMPLATES:
        # Filling is where a template that forgot to guard a section explodes,
        # and it needs no LaTeX engine, so this runs everywhere.
        filled = fill_template(load_builtin_source(spec.key), model)
        # Three of the six split the name across \name{first}{last}, so look
        # for the surname rather than the whole string.
        assert "Candidate" in filled


def test_template_cannot_reach_into_python_internals() -> None:
    """Stored templates are untrusted, so the sandbox is load-bearing."""
    from jinja2.exceptions import SecurityError

    with pytest.raises(SecurityError):
        fill_template(
            "<<name.__class__.__mro__[1].__subclasses__()>>",
            build_render_model({"basics": {"name": "x"}}),
        )


def test_unknown_template_key_is_refused() -> None:
    for key in ("../../etc", "/etc/passwd", "does-not-exist", "jakes/../deedy"):
        with pytest.raises(LatexRenderError):
            render_resume_pdf({"basics": {"name": "x"}}, template_key=key)


def test_a_template_key_cannot_walk_out_of_the_templates_tree() -> None:
    for key in ("../services", "..", "/etc", "jakes/../../services"):
        with pytest.raises(KeyError):
            builtin_directory(key)


# The least a resume can be and still be a resume. A template that only ever
# saw the sample document passes every other test in here and then fails on a
# real one: AltaCV did exactly that, because a resume with no headline left the
# class with an undefined \@tagline and nothing compiled.
SPARSE_RESUME = {
    "basics": {"name": "A Candidate", "email": "a@example.com", "phone": "+1 555 0100"},
    "work": [
        {
            "name": "One Employer",
            "position": "Engineer",
            "startDate": "2024-01",
            "highlights": ["Did one thing that can be described in a sentence."],
        }
    ],
}


# The resume the reserved-character tests send through a real compile, on
# either engine. Every character in it means something to LaTeX, and `\newpage`
# is the one that would be visible if it ran: the page count would change.
HOSTILE_RESUME = {
    "basics": {
        "name": "A. Candidate",
        "email": "a_b@example.com",
        "phone": "+1 555 0100",
        "summary": r"Shipped 100% of it & saved $5m. \newpage was not run.",
        "location": {"city": "Boston", "region": "MA"},
    },
    "work": [
        {
            "name": "R&D #2 Ltd",
            "position": "C# / .NET Engineer",
            "location": "Remote",
            "startDate": "2024-01",
            "highlights": [r"Cut costs 30% using a_b_c and {braces}"],
        }
    ],
    "skills": [{"name": "Languages", "keywords": ["C#", "F#", "C++"]}],
}


# ---------------------------------------------------------------------------
# The fast path: real compiles, in the default run
# ---------------------------------------------------------------------------


@needs_typst
@pytest.mark.parametrize("key", TYPST_READY)
def test_the_fast_path_renders_every_template_it_claims(
    key: str, typst_engine: None
) -> None:
    """A `typst_ready` flag is a promise the shared entry point has to keep.

    Only `jakes` was ever asserted through this switch, so a template flagged
    ready with a missing or misnamed port would have fallen back to Tectonic and
    the only symptom would have been a slow request.
    """
    pdf = render_resume_pdf(SAMPLE_RESUME, template_key=key).bytes_
    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 2_000


@needs_typst
@pytest.mark.parametrize("key", TYPST_READY)
def test_the_fast_path_renders_a_sparse_resume(key: str, typst_engine: None) -> None:
    """No headline, no location, no links, no projects, no skills, no dates."""
    pdf = render_resume_pdf(SPARSE_RESUME, template_key=key).bytes_
    assert pdf.startswith(b"%PDF")
    # Upper-cased because AltaCV sets the name in caps, and the point of the
    # assertion is that the page is not blank rather than how it is styled.
    assert "CANDIDATE" in pdf_text(pdf).upper()


@needs_typst
def test_reserved_characters_survive_the_fast_path_as_text(typst_engine: None) -> None:
    """The same end-to-end claim as the Tectonic test below, in milliseconds.

    Different mechanism, same thing the user cares about: the characters they
    typed come out of the PDF, and nothing in the document executed.
    """
    text = pdf_text(render_resume_pdf(HOSTILE_RESUME, template_key="jakes").bytes_)
    assert "R&D #2 Ltd" in text
    assert "100% of it" in text
    assert "newpage" in text


# ---------------------------------------------------------------------------
# The slow path: Tectonic, on demand
# ---------------------------------------------------------------------------


@needs_tectonic
@pytest.mark.slow
@pytest.mark.parametrize("key", [spec.key for spec in BUILTIN_TEMPLATES])
def test_every_bundled_template_compiles_a_sparse_resume(key: str) -> None:
    """No headline, no location, no links, no projects, no skills, no dates."""
    pdf = compile_pdf(
        fill_template(load_builtin_source(key), build_render_model(SPARSE_RESUME)),
        assets_dir=builtin_directory(key),
    )
    assert pdf.startswith(b"%PDF")


@needs_tectonic
@pytest.mark.slow
@pytest.mark.parametrize("key", [spec.key for spec in BUILTIN_TEMPLATES])
def test_every_bundled_template_compiles(key: str) -> None:
    pdf = compile_pdf(
        fill_template(load_builtin_source(key), build_render_model(SAMPLE_RESUME)),
        assets_dir=builtin_directory(key),
    )
    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 10_000


@needs_tectonic
@pytest.mark.slow
def test_a_resume_full_of_reserved_characters_still_renders() -> None:
    """The end-to-end version of the escaping tests, through a real compile."""
    rendered = render_resume_pdf(HOSTILE_RESUME, template_key="jakes")
    assert rendered.bytes_.startswith(b"%PDF")

    text = pdf_text(rendered.bytes_)
    assert "R&D #2 Ltd" in text
    assert "100% of it" in text
    # The command was printed, not executed: a second page would mean it ran.
    assert "newpage" in text
    assert len(PdfReader(io.BytesIO(rendered.bytes_)).pages) == 1


@needs_tectonic
@pytest.mark.slow
def test_a_broken_template_fails_with_the_compiler_reason() -> None:
    """What the repair loop in latex_from_document.py is handed to work with.

    Asserting the shape and not just that a log exists, because the loop's fast
    tests stub the compiler with a recorded log of exactly this form: the
    headline line starts with `!`, and the tail names the command that failed.
    """
    with pytest.raises(LatexRenderError) as caught:
        compile_pdf(r"\documentclass{article}\begin{document}\thisIsNotACommand")
    log = caught.value.log
    assert "! Undefined control sequence." in log
    assert "thisIsNotACommand" in log
