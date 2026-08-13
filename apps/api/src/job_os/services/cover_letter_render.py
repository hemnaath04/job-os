"""Render a cover letter to PDF, styled to match the user's resume template.

WHICH ENGINE, AND WHY IT IS TYPST RATHER THAN TECTONIC. The question was worth
asking properly, because the resume path supports both and a letter that could
only render one way would be a worse feature. The LaTeX path CAN structurally
take a letter template: `latex_render.fill_template` and
`latex_render.compile_pdf` know nothing about resumes, and a `letter.tex.j2`
would fill and compile through them unchanged. The reason there is not one is
operational rather than structural, and it is specific:

  1. The container renders with `tectonic --only-cached`
     (`latex_render._only_cached`, set in Dockerfile.vercel), because a request a
     user is waiting on must not go to the network for a LaTeX package.
  2. That cache is warmed at image build time by compiling exactly the six
     bundled resume templates (`scripts/warm_latex_cache.py`). A letter template
     pulls a document class and font packages none of those six pull, so on a
     built image its first compile would fail closed.
  3. Fixing that means extending the warm script and rebuilding the image, which
     is a deployment change.

Production already sets `RENDER_ENGINE=typst` (Dockerfile.vercel:120), Typst
compiles this letter in milliseconds against Tectonic's fifteen to thirty
seconds, and its bundled fonts cover every family the six resume templates name.
So the letter renders through Typst on every deployment, whatever `RENDER_ENGINE`
says, and raises a clear error rather than a mystery when the binary is absent.

MATCHING THE RESUME. One template file, restyled by a per-resume-template record:
typeface, name treatment, margins, and whether the header carries a rule. That is
what makes two documents look like a set, and it is six values rather than six
documents. See `letter_templates/letter.typ`.
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog
from pypdf import PdfReader

from job_os.schemas.cover_letters import CoverLetterDocument
from job_os.services import typst_render
from job_os.services.latex_catalog import DEFAULT_TEMPLATE_KEY
from job_os.services.pdf_text_audit import audit_pdf_text
from job_os.services.typst_render import TypstRenderError, sanitize

log = structlog.get_logger(__name__)

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "letter_templates"
_TEMPLATE_FILE = TEMPLATE_DIR / "letter.typ"

# Enough selectable text that a parser has something to read. The resume review
# uses 200 characters against a full page of bullets; a letter is prose, so the
# same floor is if anything easier to clear, and keeping the number identical
# means one definition of "selectable" across both documents.
_MIN_SELECTABLE_CHARS = 200


@dataclass(frozen=True)
class LetterStyle:
    """How a letter should look so it belongs with one resume template.

    `font` and `name_font` are family names Typst must resolve with
    `--ignore-system-fonts`, which limits them to the families the bundled
    templates already vendor plus the ones the binary ships. Every value here was
    picked to echo the resume template it is named after, not to be pretty on its
    own.

    Margins are letter margins rather than the resume's. Jake's sets half an inch
    to fit a dense page of bullets, and half an inch of margin around a page of
    prose gives a seven and a half inch measure, which is roughly twice a
    comfortable reading line. Matching the typeface makes the pair look related;
    matching that margin would just make the letter hard to read.
    """

    font: str
    name_font: str
    base_size: float
    name_size: float
    margin_x: float
    margin_y: float
    align: str
    rule: bool
    small_caps: bool
    tracking: float


LETTER_STYLES: dict[str, LetterStyle] = {
    # Computer Modern, centred name over a full-width rule. Small caps off for the
    # same reason the Typst port turns them off on the resume: Computer Modern has
    # no bold small-caps face and LaTeX has always silently substituted bold
    # upright, so honouring them would change the most prominent thing on the page.
    "jakes": LetterStyle(
        font="New Computer Modern",
        name_font="New Computer Modern",
        base_size=11,
        name_size=19,
        margin_x=0.9,
        margin_y=0.9,
        align="center",
        rule=True,
        small_caps=False,
        tracking=0,
    ),
    "sb2nov": LetterStyle(
        font="New Computer Modern",
        name_font="New Computer Modern",
        base_size=11,
        name_size=18,
        margin_x=0.95,
        margin_y=0.9,
        align="center",
        rule=True,
        small_caps=False,
        tracking=0,
    ),
    # Awesome-CV sets the name in Roboto with wide tracking over Source Sans body
    # text, which is the pairing that makes it recognisable.
    "awesome-cv": LetterStyle(
        font="Source Sans Pro",
        name_font="Roboto",
        base_size=10.5,
        name_size=22,
        margin_x=1.0,
        margin_y=1.0,
        align="center",
        rule=False,
        small_caps=False,
        tracking=1.2,
    ),
    # AltaCV is left aligned with the name over the contact details and no rule.
    "altacv": LetterStyle(
        font="Lato",
        name_font="Lato",
        base_size=10.5,
        name_size=20,
        margin_x=1.0,
        margin_y=0.95,
        align="left",
        rule=False,
        small_caps=False,
        tracking=0,
    ),
    "deedy": LetterStyle(
        font="Lato",
        name_font="Raleway",
        base_size=10.5,
        name_size=21,
        margin_x=1.0,
        margin_y=0.95,
        align="center",
        rule=True,
        small_caps=False,
        tracking=0.6,
    ),
    # ModernCV has no Typst resume port, so a letter matching it is the one pairing
    # that cannot be exact. Latin Modern Sans is the family its LaTeX class uses,
    # which is as close as this gets, and it is closer than defaulting to a serif.
    "moderncv": LetterStyle(
        font="Latin Modern Sans",
        name_font="Latin Modern Sans",
        base_size=10.5,
        name_size=19,
        margin_x=1.0,
        margin_y=0.95,
        align="left",
        rule=True,
        small_caps=False,
        tracking=0,
    ),
}

DEFAULT_LETTER_STYLE = LETTER_STYLES[DEFAULT_TEMPLATE_KEY]


def style_for(template_key: str | None) -> LetterStyle:
    """The letter style matching a resume template, or the default look.

    An unknown key falls back rather than raising. A letter is worth rendering in
    the default look; it is not worth failing over a template the user has since
    deleted.
    """
    if template_key and template_key not in LETTER_STYLES:
        log.info("cover_letter.unknown_template_key", template_key=template_key)
    return LETTER_STYLES.get(template_key or DEFAULT_TEMPLATE_KEY, DEFAULT_LETTER_STYLE)


@dataclass(frozen=True)
class RenderedLetter:
    """The rendered letter and what its text layer looks like to a parser.

    `text_layer_issues` is empty when the audit found nothing OR could not run;
    the neutral reading of both is "do not flag this letter", which is the same
    contract `pdf_text_audit` states.
    """

    bytes_: bytes
    engine: str
    page_count: int
    text_selectable: bool
    text_layer_issues: tuple[str, ...] = ()


def build_letter_model(
    document: CoverLetterDocument, *, style: LetterStyle
) -> dict[str, Any]:
    """The data.json a letter template reads.

    Flattened on this side, so the template contains no logic about which
    contact details exist: an absent phone number is simply not in the list.
    """
    sender = document.sender
    contact = [
        sanitize(part)
        for part in (sender.location, sender.email, sender.phone, *sender.links)
        if str(part or "").strip()
    ]
    recipient = [
        sanitize(part)
        for part in (document.recipient_name, document.company)
        if str(part or "").strip()
    ]
    return {
        "sender": {"name": sanitize(sender.name), "contact": contact},
        "date": sanitize(document.date),
        "recipient": recipient,
        "subject": sanitize(document.subject),
        "greeting": sanitize(document.greeting),
        "paragraphs": [sanitize(p) for p in document.paragraphs if str(p or "").strip()],
        "signoff": sanitize(document.signoff),
        "style": {
            "font": style.font,
            "name_font": style.name_font,
            "base_size": style.base_size,
            "name_size": style.name_size,
            "margin_x": style.margin_x,
            "margin_y": style.margin_y,
            "align": style.align,
            "rule": style.rule,
            "small_caps": style.small_caps,
            "tracking": style.tracking,
        },
    }


def render_cover_letter_pdf(
    document: CoverLetterDocument, *, template_key: str | None = None
) -> RenderedLetter:
    """Render a letter to PDF, styled to match `template_key`'s resume look.

    Raises `TypstUnavailableError` when the runtime has no Typst binary, which is
    the honest failure: there is no second engine to fall back to here, and see
    the module docstring for why.
    """
    if not _TEMPLATE_FILE.is_file():  # pragma: no cover - packaging failure
        raise TypstRenderError(f"The cover-letter template is missing: {_TEMPLATE_FILE}")
    style = style_for(template_key)
    model = build_letter_model(document, style=style)
    pdf_bytes = typst_render.compile_pdf(
        _TEMPLATE_FILE.read_text(),
        model,
        # No assets directory: this template imports nothing and vendors no font
        # of its own. Fonts come from the directories the resume templates already
        # vendor, reused rather than copied so there is one copy of each face and
        # one copy of each licence.
        assets_dir=None,
        font_dirs=typst_render._font_dirs(template_key or DEFAULT_TEMPLATE_KEY),
    )
    page_count, selectable, issues = audit_letter_pdf(pdf_bytes, document)
    return RenderedLetter(
        bytes_=pdf_bytes,
        engine="typst",
        page_count=page_count,
        text_selectable=selectable,
        text_layer_issues=issues,
    )


def audit_letter_pdf(
    pdf_bytes: bytes, document: CoverLetterDocument
) -> tuple[int, bool, tuple[str, ...]]:
    """Page count, whether the text is selectable, and what a parser would see.

    The same audit the resume review runs, for the same reason: a letter an
    applicant tracking system cannot read is a letter that was never read.
    `pdf_text_audit` walks the values of whatever dict it is given, so the letter
    document works as its source vocabulary with no adapter, and coverage then
    measures whether this letter's own words survived into the text layer.
    """
    reader = PdfReader(io.BytesIO(pdf_bytes))
    page_count = len(reader.pages)
    extracted = "\n".join((page.extract_text() or "") for page in reader.pages).strip()
    selectable = len(extracted) >= _MIN_SELECTABLE_CHARS

    issues: list[str] = []
    if page_count > 1:
        issues.append(
            f"Renders to {page_count} pages. A cover letter is one page: cut a "
            "paragraph or shorten the longest sentences."
        )
    if not selectable:
        issues.append(
            "The rendered PDF has too little selectable text for a parser to read."
        )
    else:
        audit = audit_pdf_text(pdf_bytes, source_document=document.model_dump(mode="json"))
        if audit.available and not audit.clean:
            issues.extend(audit.artifacts)
    return page_count, selectable, tuple(issues)
