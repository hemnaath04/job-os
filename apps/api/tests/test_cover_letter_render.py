"""Rendering a letter, and the claim that it looks like part of a set.

Two things are worth a real compile rather than an assertion about a string.

The first is that the text layer survives. A letter an applicant tracking system
cannot read is a letter that was never read, and that is not a hypothetical: a
user's resumes were flagged as spam by Ashby, so the text layer is checked with
the same `pdf_text_audit` the resume review uses rather than assumed.

The second is that the pairing is real. The whole argument for one letter
template restyled by seven records is that the typeface and the header treatment
are what make two documents look related. Typst substitutes a missing family
silently, so a letter whose font never resolved would compile, render, look
wrong, and pass every test that only checked it produced a PDF. So the fonts are
asked for by name, from the binary, the way `typst_render.missing_fonts` does it
for the resumes.
"""
from __future__ import annotations

import os
import subprocess

import pytest

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://job_os:job_os@localhost/job_os"
)

from job_os.schemas.cover_letters import CoverLetterDocument, CoverLetterSender  # noqa: E402
from job_os.services import typst_render  # noqa: E402
from job_os.services.cover_letter_render import (  # noqa: E402
    DEFAULT_LETTER_STYLE,
    LETTER_STYLES,
    build_letter_model,
    render_cover_letter_pdf,
    style_for,
)
from job_os.services.latex_catalog import BUILTIN_TEMPLATES, DEFAULT_TEMPLATE_KEY  # noqa: E402
from job_os.services.pdf_text_audit import audit_pdf_text  # noqa: E402
from job_os.services.typst_render import typst_binary  # noqa: E402

needs_typst = pytest.mark.skipif(
    typst_binary() is None,
    reason="typst is not installed on this machine",
)


def _letter() -> CoverLetterDocument:
    """A letter long enough to be a real page, with hostile content in it.

    The paragraphs carry everything a Typst source would read as markup, for the
    same reason the resume tests do: content reaches the template through
    data.json and is interpolated in code mode, so it can never be re-parsed, and
    that claim is asserted against a real compile rather than taken on trust.
    """
    return CoverLetterDocument(
        sender=CoverLetterSender(
            name="Hemnaath Balasubramani",
            email="balasubramani.h@northeastern.edu",
            phone="+1 617 555 0134",
            location="Boston, MA",
            links=["github.com/hemnaath04"],
        ),
        date="12 August 2026",
        company="Corvus Systems",
        role="Backend Engineer, Platform",
        greeting="Dear Hiring Team,",
        subject="Application for Backend Engineer, Platform at Corvus Systems",
        paragraphs=[
            "I am applying for the Backend Engineer, Platform role at Corvus "
            "Systems. The closest thing I have to the work you describe is two "
            "years of writing test automation against a pricing engine that "
            "could not be taken offline.",
            "I wrote Python and Go suites for a rideshare client's pricing "
            "engine and triaged the daily failures they produced. The hard part "
            "was never the assertions, it was that the engine's inputs drifted "
            "faster than the fixtures did, so a suite that passed on Monday "
            "reported nothing useful by Friday. #set page(width:1pt) *bold* "
            "$x^2$ 100% C# & .NET",
            "I would welcome a conversation about the platform team.",
        ],
        signoff="Sincerely,",
        word_count=101,
    )


def _families(template_key: str) -> set[str]:
    """The families Typst can resolve for a letter, asked of the binary.

    Deliberately not `typst_render.available_font_families`, which also passes
    the template's own asset directory. A letter vendors no font of its own and
    moderncv has no Typst resume directory at all, so that helper raises on the
    one template this most needs to check.
    """
    binary = typst_binary()
    assert binary is not None
    command = [binary, "fonts", "--ignore-system-fonts"]
    for font_dir in typst_render._font_dirs(template_key):
        command += ["--font-path", str(font_dir)]
    proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
        command, capture_output=True, text=True, timeout=30, check=False
    )
    assert proc.returncode == 0, proc.stderr
    return {line.strip() for line in proc.stdout.splitlines() if line.strip()}


def test_every_resume_template_has_a_letter_to_match() -> None:
    """A resume look with no letter look would silently render as the default.

    Which is the one outcome the feature is supposed to prevent: two documents
    that were meant to be a pair and are not.
    """
    assert {spec.key for spec in BUILTIN_TEMPLATES} == set(LETTER_STYLES)
    assert style_for(DEFAULT_TEMPLATE_KEY) is DEFAULT_LETTER_STYLE


def test_an_unknown_template_falls_back_instead_of_failing() -> None:
    """A letter is worth rendering in the default look.

    It is not worth failing over a template the user deleted after the version
    that named it was written.
    """
    assert style_for("a-template-that-was-deleted") is DEFAULT_LETTER_STYLE
    assert style_for(None) is DEFAULT_LETTER_STYLE


@needs_typst
@pytest.mark.parametrize("template_key", sorted(LETTER_STYLES))
def test_a_letter_style_names_fonts_that_actually_resolve(template_key: str) -> None:
    """Typst substitutes silently, so a wrong family name is invisible at render.

    This is the assertion that makes "matches your resume" a fact rather than an
    intention.
    """
    style = LETTER_STYLES[template_key]
    available = _families(template_key)
    assert style.font in available
    assert style.name_font in available


@needs_typst
def test_a_rendered_letter_is_one_page_of_selectable_text() -> None:
    """The two properties an employer's parser actually depends on."""
    rendered = render_cover_letter_pdf(_letter(), template_key="jakes")

    assert rendered.engine == "typst"
    assert rendered.page_count == 1
    assert rendered.text_selectable
    assert rendered.text_layer_issues == ()


@needs_typst
def test_the_letter_s_own_words_survive_into_the_text_layer() -> None:
    """Selectable is not the same as readable, so the audit runs on the words.

    `pdf_text_audit` walks the values of whatever document it is given, so the
    letter works as its own source vocabulary with no adapter, and coverage then
    measures whether this letter's sentences reached the page a parser sees.
    """
    document = _letter()
    rendered = render_cover_letter_pdf(document, template_key="jakes")
    audit = audit_pdf_text(
        rendered.bytes_, source_document=document.model_dump(mode="json")
    )

    if not audit.available:  # pragma: no cover - depends on an optional dependency
        pytest.skip("pdf_inspector is not available in this runtime")
    assert audit.clean, audit.artifacts


@needs_typst
@pytest.mark.parametrize("template_key", sorted(LETTER_STYLES))
def test_every_template_renders_hostile_content_as_text(template_key: str) -> None:
    """Markup in a paragraph prints as characters rather than doing anything.

    The paragraph carries a page override, a bold marker and a maths expression.
    If any of it were parsed as source the page would be one point wide or the
    compile would fail; instead the characters come back out of the text layer.
    """
    rendered = render_cover_letter_pdf(_letter(), template_key=template_key)
    assert rendered.page_count == 1
    assert rendered.text_selectable


def test_the_model_a_template_reads_omits_what_the_letter_does_not_have() -> None:
    """Absent contact details are absent, not empty strings on the page.

    The template contains no logic about which details exist, which is only safe
    if this side never hands it a blank to print.
    """
    document = _letter()
    document.sender.phone = ""
    document.recipient_name = ""
    model = build_letter_model(document, style=DEFAULT_LETTER_STYLE)

    assert model["sender"]["contact"] == [
        "Boston, MA",
        "balasubramani.h@northeastern.edu",
        "github.com/hemnaath04",
    ]
    # Only the company, since no recipient was named. A blank line addressed to
    # nobody is worse than no line.
    assert model["recipient"] == ["Corvus Systems"]
    assert model["style"]["font"] == DEFAULT_LETTER_STYLE.font
