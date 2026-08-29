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
    MAX_PROJECT_KEYWORDS,
    MAX_PROJECT_META_CHARS,
    TypstRenderError,
    build_render_model,
    builtin_directory,
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


def test_location_leads_the_contact_line_not_trails_it() -> None:
    """jakes/deedy/dashline render `contact` as one run of boxed items joined
    by " | " and centered, wrapping wherever it runs out of width. Whichever
    item is last is what gets isolated onto its own line on a wrap, and a
    lone centered "Boston, MA" reads as an unstyled second heading under the
    name. Location goes first so a wrap isolates a link instead."""
    model = build_render_model(
        {
            "basics": {
                "name": "A Candidate",
                "phone": "+1 555 555 5555",
                "email": "a@example.com",
                "location": {"city": "Boston", "region": "MA"},
                "profiles": [{"network": "GitHub", "username": "x", "url": "https://github.com/x"}],
            }
        }
    )
    kinds = [item["kind"] for item in model["contact"]]
    assert kinds[0] == "location"
    assert kinds == ["location", "phone", "email", "github"]


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


@needs_typst
def test_a_package_fetch_fails_closed_even_past_the_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The backstop, tested with the guard deliberately disabled.

    A source screen is only as good as its regex, so the render subprocess also
    gets every proxy variable pointed at a closed port. This asserts the second
    layer alone: with `_reject_imports` neutered, an import that reaches Typst
    must fail to reach the network rather than quietly downloading a package on
    a request a user is waiting for.

    Matching on the refusal specifically, not just on failure. A test that
    accepted any error here would still pass if the fetch succeeded and the
    package merely failed to compile, which is the opposite of the point.
    """
    from job_os.services import typst_render as module

    monkeypatch.setattr(module, "_reject_imports", lambda source: None)
    with pytest.raises(TypstRenderError) as caught:
        module.compile_pdf('#import "@preview/oxifmt:0.2.0": strfmt\nhello', {})
    assert "failed to download package" in caught.value.log
    assert "Connection refused" in caught.value.log or "Connect error" in caught.value.log


# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------


@needs_typst
@pytest.mark.parametrize("key", PORTED)
def test_every_face_a_template_names_actually_resolves(key: str) -> None:
    """The failure this catches is silent, which is why it is a test.

    A Typst compile does not fail on a missing font. It substitutes one and
    carries on, so the resume renders, looks wrong, and nobody finds out until
    somebody opens it. Asking the binary which families it can see is the only
    way to know the render that was checked is the render that ships.
    """
    from job_os.services.typst_render import missing_fonts

    assert missing_fonts(key) == []


@needs_typst
def test_fonts_resolve_by_family_name_not_file_name() -> None:
    """Lato ships as Lato-Reg.ttf and has to resolve as `Lato`.

    Guards the vendored set as a whole, including the faces the templates not
    yet ported will need, so a font going missing is caught here rather than
    when somebody starts that port.
    """
    from job_os.services.typst_render import available_font_families

    available = available_font_families("jakes")
    for family in (
        "Lato",
        "Raleway",
        "Source Sans Pro",
        "Roboto",
        "Roboto Slab",
        "Font Awesome 5 Free Solid",
        "New Computer Modern",
    ):
        assert family in available, f"{family} did not resolve"


@needs_typst
def test_a_face_that_is_not_vendored_is_reported_missing() -> None:
    """The check has to be able to fail, or it is decoration."""
    from job_os.services import typst_render as module

    monkey = dict(module.FONT_REQUIREMENTS)
    monkey["jakes"] = ("Definitely Not A Real Typeface",)
    original = module.FONT_REQUIREMENTS
    module.FONT_REQUIREMENTS = monkey
    try:
        assert module.missing_fonts("jakes") == ["Definitely Not A Real Typeface"]
    finally:
        module.FONT_REQUIREMENTS = original


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


PROJECT_WITH_LINK = {
    "basics": {"name": "A Candidate", "email": "a@b.com"},
    "projects": [
        {
            "name": "job.os",
            "url": "https://jobs.hemnaath.tech",
            "keywords": ["Python", "FastAPI"],
            "highlights": ["Built a tailoring engine."],
        },
        {
            "name": "NoLink",
            "keywords": ["Go"],
            "highlights": ["A project with no url."],
        },
    ],
}


def test_a_project_link_is_text_and_not_only_a_hyperlink() -> None:
    """The defect: every template made the project NAME the clickable thing.

    A `link()` around a title puts the URL in the PDF's annotation layer and
    nowhere else. A human reading a printout, and every ATS parser, sees the
    word "job.os" and no address. The user reported it exactly that way, from a
    tailored resume where none of three real projects showed where to find
    them.

    So the URL is rendered as its own visible label alongside the tech line.
    Asserted on the render model rather than the compiled PDF because that is
    what all eight templates read, and the model is the thing they share.
    """
    model = build_render_model(PROJECT_WITH_LINK)
    linked, unlinked = model["projects"]
    assert "jobs.hemnaath.tech" in linked["meta_line"]
    assert linked["keywords_line"] in linked["meta_line"]
    # A project with no URL keeps exactly the line it had before, with no
    # separator left dangling where the link would have gone.
    assert unlinked["meta_line"] == unlinked["keywords_line"] == "Go"


def test_every_ported_template_prints_the_project_link() -> None:
    """The regression that would undo this quietly.

    A new template, or a revert of one line in an existing one, puts the URL
    back in the annotation layer where nobody sees it. Cheap to check by
    reading the sources.
    """
    for template in BUILTIN_TEMPLATES:
        if not has_builtin(template.key):
            continue
        source = (builtin_directory(template.key) / "resume.typ").read_text()
        if "project.keywords_line" in source:
            raise AssertionError(
                f"{template.key} prints keywords without the project link; "
                "use meta_line"
            )


KEYWORD_DUMP = {
    "basics": {"name": "A Candidate", "email": "a@b.com"},
    "projects": [
        {
            # Verbatim from the vault behind the resume that reported this.
            "name": "BedRocked",
            "startDate": "2026-06-01",
            "endDate": None,
            "keywords": [
                "Python", "FastAPI", "scikit-learn", "Anthropic Claude",
                "Autodesk APS", "Vercel", "Knowledge Distillation",
                "Computer Vision", "LLM Integration", "Generative AI",
                "Model Inference", "Classification",
            ],
            "highlights": ["Trained a distilled classifier."],
        },
        {
            "name": "Infant Cry Sound Detection",
            "startDate": "2024-01-01",
            "endDate": "2024-05-01",
            "keywords": ["Deep Learning", "Audio Classification"],
            "highlights": ["Classified infant cry types from raw audio."],
        },
    ],
}


def test_a_tech_line_is_a_stack_not_a_keyword_dump() -> None:
    """Twelve nouns in a row is not a tech stack.

    A reader skims past a line that long exactly as fast as an ATS dilutes it:
    the six technologies that matter are buried among six that do not. The
    tailor orders these by the posting first, so what this cuts is what the
    posting never asked for.
    """
    projects = build_render_model(KEYWORD_DUMP)["projects"]
    assert len(projects[0]["keywords"]) == MAX_PROJECT_KEYWORDS
    # The order is preserved, so the tailor's ranking survives the cut.
    assert projects[0]["keywords"][0] == "Python"
    # A project that never had too many is untouched.
    assert projects[1]["keywords"] == ["Deep Learning", "Audio Classification"]


def test_a_project_with_no_end_date_is_not_claimed_as_ongoing() -> None:
    """"Present" is a convention that belongs to employment.

    Leaving a job's end date blank means "I still work here" and the user
    asserts it. Projects have no such convention, and this app writes a null
    end date for every project nobody gave an end to. In the vault behind the
    reported resume all four projects had one, and at least two were weekend
    hackathons -- so "Jun 2026 - Present" was printed over a build that lasted
    two days.
    """
    projects = build_render_model(KEYWORD_DUMP)["projects"]
    assert projects[0]["dates"] == "Jun 2026"
    assert "Present" not in projects[0]["dates"]
    # A real range is untouched, which is the half of this that must not move.
    assert projects[1]["dates"] == f"Jan 2024 {chr(0x2013)} May 2024"


def test_a_job_still_says_present() -> None:
    """The distinction this rests on. Employment keeps the convention."""
    model = build_render_model(
        {
            "basics": {"name": "A Candidate"},
            "work": [
                {
                    "name": "Acme",
                    "position": "Engineer",
                    "startDate": "2024-07-01",
                    "endDate": None,
                    "highlights": ["Shipped things."],
                }
            ],
        }
    )
    assert "Present" in model["work"][0]["dates"]


def test_the_link_never_yields_to_a_sixth_technology() -> None:
    """The wrap the keyword cap alone did not prevent.

    Six keywords plus `sewershed-bedrocked.vercel.app` measured 101 characters
    and wrapped in a real compile, which is exactly the cost the cap exists to
    avoid. The whole line has a budget now and the keywords are what yields,
    because a URL cannot be abbreviated without becoming wrong, and a project a
    reader can open beats a sixth technology they cannot check.
    """
    long_url = dict(KEYWORD_DUMP)
    long_url["projects"] = [
        {**KEYWORD_DUMP["projects"][0], "url": "https://sewershed-bedrocked.vercel.app"}
    ]
    project = build_render_model(long_url)["projects"][0]

    assert len(project["meta_line"]) <= MAX_PROJECT_META_CHARS
    assert "sewershed-bedrocked.vercel.app" in project["meta_line"], (
        "the link is whole; it is the keywords that give way"
    )
    assert len(project["keywords"]) == MAX_PROJECT_KEYWORDS, (
        "the cut is to the printed line, not to the structured field"
    )


def test_a_project_with_no_link_spends_the_whole_line_on_its_stack() -> None:
    """Nothing is dropped to leave room for a link that is not there."""
    project = build_render_model(KEYWORD_DUMP)["projects"][0]
    assert project["meta_line"] == ", ".join(project["keywords"])
