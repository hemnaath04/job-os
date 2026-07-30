"""Turn an uploaded .tex or .pdf into a reusable LaTeX resume template.

Two paths, and they are not equally good, which the UI says out loud:

- **.tex** is the accurate one. The design already exists as LaTeX, so the model
  only has to replace one person's content with placeholders. Layout, spacing
  and font choices come through unchanged because they are not being guessed at.
- **.pdf** is a reconstruction. The model reads the document and writes LaTeX
  that aims at the same design. Close is achievable; identical is not.

Either way the output is code this service will store and later execute, so
nothing is taken on the model's word. Every candidate is compiled with sample
data by the same engine that will render real resumes, and a failure goes back
to the model with the compiler's own log for repair, up to a small number of
attempts. A template that never compiles is never returned.

This lives on the API container rather than in the Appwrite agent function
because validation means really compiling, and that runtime has no LaTeX engine.
Generating where it cannot be checked would defeat the point of checking.
"""
from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field

import anthropic
import structlog
from pydantic import BaseModel, Field

from job_os.services.latex_catalog import SAMPLE_RESUME
from job_os.services.latex_render import (
    TectonicUnavailableError,
    build_render_model,
    compile_pdf,
    fill_template,
)
from job_os.services.llm_json import (
    EMPTY_REPLY_RETRY,
    JSON_ONLY_RETRY,
    create_message,
    parse_model_json,
    response_diagnostics,
    response_text,
)
from job_os.settings import get_settings

log = structlog.get_logger(__name__)

# Four passes at most: the first draft plus three repairs. Past that the model is
# usually rewriting rather than fixing, and the user is still waiting.
MAX_ATTEMPTS = 4

# Big enough for a full resume template, which runs long once every section is
# guarded, plus the reasoning the gateway's model emits before its answer.
MAX_TOKENS = 16000


class TemplateBuildError(RuntimeError):
    """The upload could not be turned into a template that compiles.

    Callers keep the user's current template and say so plainly, rather than
    storing something that cannot render.
    """


class GeneratedLatexTemplate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    latex_source: str = Field(min_length=40)
    notes: str = ""


@dataclass(slots=True)
class TemplateCandidate:
    name: str
    latex_source: str
    notes: str
    pdf_bytes: bytes
    attempts: int = 1
    # One line per repair round, so the caller can be honest about a template
    # that needed three goes rather than implying it worked first time.
    repairs: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

# The names a template may use, taken from the renderer itself rather than
# written out by hand, so this prompt cannot drift from the actual contract.
_MODEL_KEYS = sorted(build_render_model({}))

CONTRACT = f"""\
PLACEHOLDER SYNTAX. LaTeX already owns braces and the percent sign, so the
template language uses different delimiters:
  a value        <<name>>
  a block        <% if work %> ... <% endif %>,  <% for job in work %> ... <% endfor %>
  a comment      <# not rendered #>
A LaTeX percent comment does NOT hide a placeholder. The template engine runs
first and does not know what a LaTeX comment is. Block tags close with `%>` and
never with `%}}`; getting that wrong leaves a stray brace in the LaTeX.

AVAILABLE NAMES, and there are no others:
  {", ".join(_MODEL_KEYS)}

SHAPES:
- name, first_name, last_name, headline, summary, location, city, region
- email, email_url (a mailto: target), phone
- website, website_label (the URL without its scheme)
- contact: an ordered list of the whole contact line. Each item has .text,
  .url (may be empty) and .kind (phone, email, website, location, or a network)
- profiles: list of .network, .username, .url, .label
- linkedin, github: the matching profile or nothing
- work: list of .company, .position, .location, .dates, .summary, .url,
  .bullets (list of strings)
- education: list of .institution, .degree, .study_type, .area, .location,
  .dates, .score, .bullets. NOTE: .degree is .study_type and .area already
  joined, so printing .degree AND .area repeats the subject twice
- projects: list of .name, .description, .url, .url_label, .dates, .keywords,
  .keywords_line, .bullets
- skills: list of .name, .level, .keywords, .keywords_line
- certificates: list of .name, .issuer, .date, .url
- awards: list of .title, .awarder, .summary
- publications: list of .name, .publisher, .summary
- languages: list of .language, .fluency
- interests: list of .name

Every value is ALREADY LaTeX-escaped. Do not escape anything again and do not
wrap a value in \\detokenize or \\verb.

THE ENGINE IS TECTONIC, which is XeTeX and nothing else:
- There is no pdflatex and no lualatex. \\ifPDFTeX is always false. pdfTeX
  primitives such as \\pdfgentounicode do not exist. Do not use them.
- Its package set is TeX Live 2022. fontawesome5 exists; fontawesome6 and
  fontawesome7 do NOT. simpleicons does NOT.
- Shell escape is off. Anything that shells out, pdfx in particular, fails.
- There is no way to add a .cls or .sty file. The template must be ONE
  self-contained .tex file that uses only packages from that distribution.
- Fonts must be named by FILE, never by family: \\setmainfont{{Roboto-Regular.otf}}
  works, \\setmainfont{{Roboto}} does not, because there are no fonts installed on
  the rendering host. Files available include Roboto-*.otf,
  SourceSansPro-{{Regular,It,Bold,BoldIt,Light,LightIt}}.otf (note: Pro, and the
  italic is RegularIt), RobotoSlab-{{Regular,Bold}}.otf, Lato-*.ttf,
  Raleway-*.otf, EBGaramond-*.otf, FontAwesome.otf. Plain Computer Modern needs
  no fontspec at all and is always safe.

HARD RULES:
1. Output ONE JSON object and nothing else. No prose, no markdown fences.
2. Guard every optional section. A resume may have no awards, no publications,
   no summary and no projects. Wrap each section in a check. A sparse resume
   must still compile.
3. Never put a person's real details in the template. Every piece of content
   comes from a placeholder, so the same design renders anybody's resume.
4. Target one printed page on US Letter.
5. Do not use \\input, \\include or \\write18, and do not read any file.
6. name is a short label for the design, two or three words, about the look and
   not about the person: "Two Column Slate", not "Priya's Resume".
"""

TEX_PROMPT = """\
This is somebody's own resume, written in LaTeX. Turn it into a template.

Keep the design exactly: the document class if it is one the engine has, the
preamble, the macros, the spacing, the section order, the typography. Replace
only the content, swapping this person's details for the placeholders.

If it depends on a class or style file that is not part of a standard TeX
distribution, reproduce the same look using standard packages instead, and say
so in notes. Do not silently drop a design element; either reproduce it or
mention it.

Return one JSON object with keys: name, latex_source, notes.
"""

PDF_PROMPT = """\
Recreate this document's design as a LaTeX resume template.

Match what you can actually see: the column layout, the section order, how
headings are set, rules and dividers, the weight and size relationships, and how
dense the spacing is. Ignore the specific person entirely.

You are reconstructing a design from its output, so say plainly in notes what
you could not match. Do not claim a match you did not achieve.

Return one JSON object with keys: name, latex_source, notes.
"""


def _client() -> anthropic.AsyncAnthropic:
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise TemplateBuildError("ANTHROPIC_API_KEY is not set.")
    # auth_token, not api_key: the Manifest gateway wants an Authorization
    # bearer header rather than x-api-key.
    return anthropic.AsyncAnthropic(
        auth_token=settings.anthropic_api_key,
        base_url=settings.anthropic_base_url or None,
    )


# Things a stored template has no business doing. Every one of these reads or
# writes a file, and the compile runs in this container. --untrusted already
# turns off shell escape, and the engine runs with a scrubbed environment, but a
# template that asks for any of this is either broken or hostile, and either way
# it should not be stored.
_FORBIDDEN = (
    (re.compile(r"\\write18"), r"\write18"),
    (re.compile(r"\\immediate\s*\\write"), r"\immediate\write"),
    (re.compile(r"\\openin|\\openout|\\read\b"), "file reads and writes"),
    (
        re.compile(r"\\(input|include|InputIfFileExists)\s*\{?\s*(/|\.\.)"),
        "absolute or parent paths",
    ),
    (re.compile(r"\\ShellEscape|\\shellescape"), "shell escape"),
)


def _reject_unsafe(latex_source: str) -> None:
    for pattern, label in _FORBIDDEN:
        if pattern.search(latex_source):
            raise TemplateBuildError(
                f"The generated template uses {label}, which a stored template "
                "may not do. Nothing was saved."
            )


def _document_blocks(raw: bytes, filename: str) -> tuple[list[dict], str]:
    """What to show the model, and which instruction to send with it."""
    lowered = filename.lower()
    if lowered.endswith((".tex", ".latex", ".cls", ".sty", ".txt")):
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("latin-1", errors="replace")
        if len(text) > 120_000:
            raise TemplateBuildError(
                "That .tex file is larger than this can handle. Trim it to the "
                "resume itself, without bibliography files or images."
            )
        return [{"type": "text", "text": f"The uploaded LaTeX source:\n\n{text}"}], TEX_PROMPT
    if lowered.endswith(".pdf"):
        return [
            {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": base64.standard_b64encode(raw).decode("ascii"),
                },
            }
        ], PDF_PROMPT
    raise TemplateBuildError(
        "Upload a .tex file, which keeps the design exactly, or a .pdf, which is "
        "recreated as closely as the design can be read from the page."
    )


def validate_template(latex_source: str) -> bytes:
    """Compile the sample resume with this template. Raises if it cannot.

    Returns the PDF, which doubles as the preview, so acceptance and preview are
    the same act: a stored template is always one that has demonstrably rendered.
    """
    _reject_unsafe(latex_source)
    model = build_render_model(SAMPLE_RESUME)
    filled = fill_template(latex_source, model)
    pdf = compile_pdf(filled)
    if not pdf.startswith(b"%PDF"):
        raise TemplateBuildError("The template did not produce a PDF.")
    if len(pdf) < 2_000:
        raise TemplateBuildError("The template compiled to an essentially empty page.")
    return pdf


def _failure_note(error: Exception) -> str:
    """What to tell the model, and what to record as a repair note."""
    compiler_log = getattr(error, "log", "") or ""
    lines = [line for line in compiler_log.splitlines() if line.startswith("!")]
    headline = lines[0].strip() if lines else str(error)
    return headline[:300]


async def build_template_from_upload(
    raw: bytes,
    filename: str,
    *,
    requested_name: str | None = None,
) -> TemplateCandidate:
    """Ask for a template, then compile it until it works or the attempts run out.

    Raises TemplateBuildError when no attempt compiles, so the caller can keep
    the user's current template and say what happened.
    """
    settings = get_settings()
    client = _client()
    blocks, instruction = _document_blocks(raw, filename)
    messages: list[anthropic.types.MessageParam] = [
        {"role": "user", "content": [*blocks, {"type": "text", "text": instruction}]}
    ]
    repairs: list[str] = []

    for attempt in range(1, MAX_ATTEMPTS + 1):
        # Streamed, like every other call in this codebase: max_tokens has to
        # hold a thinking block as well as a whole resume template, and the SDK
        # refuses a non-streaming request with a budget that large.
        response = await create_message(
            client,
            model=settings.anthropic_model_extract,
            max_tokens=MAX_TOKENS,
            system=CONTRACT,
            messages=messages,
            extra_headers={"x-manifest-tier": settings.manifest_tier_quality},
        )
        reply = response_text(response)
        if not reply.strip():
            note = "The model returned no text."
            log.warning(
                "latex_template.empty_reply",
                attempt=attempt,
                filename=filename,
                **response_diagnostics(response),
            )
            if attempt == MAX_ATTEMPTS:
                raise TemplateBuildError(
                    "The model never returned a template. Try again, or upload "
                    "the .tex source if you have it."
                )
            repairs.append(f"Attempt {attempt}: {note}")
            messages = [
                *messages,
                {"role": "assistant", "content": "(empty)"},
                {"role": "user", "content": EMPTY_REPLY_RETRY},
            ]
            continue
        try:
            generated = parse_model_json(GeneratedLatexTemplate, reply)
            pdf_bytes = validate_template(generated.latex_source)
        except TectonicUnavailableError:
            # A deployment problem, not a bad template. Do not spend three more
            # model calls discovering the same thing.
            raise
        except Exception as error:  # noqa: BLE001 - anything can surface here
            note = _failure_note(error)
            log.warning(
                "latex_template.attempt_failed",
                attempt=attempt,
                filename=filename,
                error=note,
            )
            if attempt == MAX_ATTEMPTS:
                detail = (
                    "Could not build a template from this upload after "
                    f"{MAX_ATTEMPTS} attempts. The last compiler error was: {note}"
                )
                raise TemplateBuildError(detail) from error
            repairs.append(f"Attempt {attempt}: {note}")
            compiler_log = getattr(error, "log", "") or ""
            headline = (
                f"Tectonic reported:\n\n{note}\n\nThe end of the log:\n\n{compiler_log[-2500:]}"
                if compiler_log
                else (
                    "It never reached the compiler. Filling the placeholders "
                    f"failed:\n\n{note}"
                )
            )
            messages = [
                *messages,
                {"role": "assistant", "content": reply[:8000] or "(empty)"},
                {
                    "role": "user",
                    "content": (
                        f"That did not work. {headline}\n\n"
                        "Fix the cause and return the corrected template. Keep the "
                        "design. Remember: XeTeX only, TeX Live 2022 packages only, "
                        "no fontawesome6, fonts by file name, one self-contained "
                        "file, every optional section guarded, and block tags that "
                        "close with the right delimiter.\n\n" + JSON_ONLY_RETRY
                    ),
                },
            ]
            continue

        return TemplateCandidate(
            name=(requested_name or generated.name).strip(),
            latex_source=generated.latex_source,
            notes=generated.notes.strip(),
            pdf_bytes=pdf_bytes,
            attempts=attempt,
            repairs=repairs,
        )

    # Unreachable: the loop either returns or raises on the final attempt.
    raise TemplateBuildError("Could not build a template from this upload.")
