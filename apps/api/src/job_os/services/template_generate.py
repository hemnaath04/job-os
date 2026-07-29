"""Turn an uploaded resume document into a reusable look.

The model is asked to recreate a document's design as a Jinja template plus CSS,
written against the context the renderer actually provides. Its output is
executable code that this service will store and later run, so nothing is
trusted on the model's word: every candidate is rendered against a sample
resume before it is accepted, a failure is fed back once for repair, and a
second failure raises rather than storing a look that cannot render.

This lives on the API container rather than the Appwrite agent function because
validation means really rendering, and the Appwrite python runtime has no pango
or cairo for WeasyPrint. Generating where it cannot be checked would defeat the
point of checking.
"""
from __future__ import annotations

import base64
import io
from dataclasses import dataclass
from typing import Any

import anthropic
import structlog
from pydantic import BaseModel, Field

from job_os.services.llm_json import (
    JSON_ONLY_RETRY,
    parse_model_json,
    response_text,
)
from job_os.services.pdf_render import (
    render_resume_html,
    render_resume_pdf,
    resume_context,
)
from job_os.settings import get_settings

log = structlog.get_logger(__name__)


class TemplateGenerationError(RuntimeError):
    """The document could not be turned into a template that renders.

    Callers are expected to keep the default look and tell the user, rather than
    storing a broken template or failing the whole request.
    """


class GeneratedTemplate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    html_source: str = Field(min_length=1)
    css_source: str = ""
    notes: str = ""


@dataclass(slots=True)
class TemplateCandidate:
    name: str
    html_source: str
    css_source: str
    notes: str
    pdf_bytes: bytes
    # The sample rendered as self-contained HTML. A browser cannot render Jinja
    # and cannot thumbnail a PDF without a rasteriser, so this is what the
    # library shows as a preview: the real output, scaled down.
    preview_html: str


# Exercises every section a template may reference, so one that crashes on a
# field the uploaded document happened not to show is caught here rather than
# the first time a real resume uses it.
SAMPLE_RESUME: dict[str, Any] = {
    "basics": {
        "name": "Sample Candidate",
        "label": "Backend Engineer",
        "email": "sample@example.com",
        "phone": "+1 555 0100",
        "url": "https://example.com",
        "summary": "Two lines of summary text to show how the template wraps prose.",
        "location": {"city": "Boston", "region": "MA"},
        "profiles": [{"network": "GitHub", "url": "https://github.com/example"}],
    },
    "work": [
        {
            "name": "Acme Corp",
            "position": "Senior Engineer",
            "location": "Boston, MA",
            "startDate": "2024-07",
            "endDate": None,
            "summary": "Platform team.",
            "url": "https://example.com",
            "highlights": [
                "Cut p99 latency from 900ms to 180ms across the checkout path.",
                "Led the migration of 40 services onto a shared deploy pipeline.",
            ],
            "keywords": ["Python", "Postgres"],
        }
    ],
    "education": [
        {
            "institution": "Example University",
            "area": "Computer Science",
            "studyType": "Master of Science",
            "startDate": "2026-01",
            "endDate": "2028-05",
            "score": "3.9",
            "location": "Boston, MA",
            "courses": ["Distributed Systems", "Algorithms"],
        }
    ],
    "projects": [
        {
            "name": "Example Project",
            "description": "A short project description.",
            "startDate": "2025-01",
            "endDate": "2025-06",
            "url": "https://github.com/example/project",
            "highlights": ["Shipped a scheduler handling 2k jobs a minute."],
            "keywords": ["Go"],
            "roles": ["Author"],
            "entity": "Personal",
            "type": "application",
        }
    ],
    "skills": [
        {"name": "Languages", "keywords": ["Python", "Go", "SQL"]},
        {"name": "Infrastructure", "keywords": ["Docker", "Terraform"]},
    ],
    "certificates": [
        {"name": "Example Certification", "issuer": "Example Body", "date": "2025-03",
         "url": "https://example.com"}
    ],
    "languages": [{"language": "English", "fluency": "Native"}],
    "interests": [{"name": "Cycling"}],
    "publications": [
        {"name": "An Example Paper", "publisher": "Example Journal",
         "releaseDate": "2025-02", "url": "https://example.com",
         "summary": "One line."}
    ],
    "awards": [
        {"title": "Example Award", "awarder": "Example Org", "date": "2025-04",
         "summary": "One line."}
    ],
}

_CONTEXT_NAMES = ", ".join(sorted(resume_context({})))

SYSTEM_PROMPT = f"""\
You recreate the visual design of a resume as a reusable Jinja2 HTML template
plus a separate CSS stylesheet.

You are copying the LAYOUT AND STYLING ONLY. Never copy the person's details
from the document into the template. Every piece of content must come from the
template variables, so the same design can render anybody's resume.

The renderer passes exactly these names and nothing else:
  {_CONTEXT_NAMES}

Shapes, following the JSON Resume schema:
- basics: name, label, email, phone, url, summary, location.city,
  location.region, profiles (list of network, url)
- work: name, position, location, startDate, endDate, summary, url,
  highlights (list of strings), keywords
- education: institution, area, studyType, startDate, endDate, score, location,
  courses
- projects: name, description, startDate, endDate, url, highlights, keywords,
  roles, entity, type
- skills: name, keywords
- certificates: name, issuer, date, url
- languages: language, fluency
- interests: name
- publications: name, publisher, releaseDate, url, summary
- awards: title, awarder, date, summary
- fmt_range(startDate, endDate) is a function returning a formatted date range

Hard rules:
1. Output ONE JSON object and nothing else. No prose, no markdown fences.
2. html_source is a complete HTML document. Put ALL styling in css_source, not
   in a style block or inline attributes, because the two are stored separately.
3. Guard every optional field. A resume may have no awards, no publications and
   no summary, so wrap each section in a check and never assume a list is
   non-empty. Rendering must not fail on a sparse resume.
4. Use only the names listed above. There is no other variable, no filters
   beyond Jinja's builtins plus fmt_date and safe_resume_url, no includes, no
   extends, and no access to Python attributes such as __class__.
5. Target a single printed page on US Letter. Use print-appropriate units and a
   font stack that exists on a Linux rendering host.
6. name is a short label for this design, two or three words, describing the
   look rather than the person. "Two Column Serif", not "Jane's Resume".
"""

USER_PROMPT = """\
Recreate this document's design as a Jinja2 template and CSS.

Match its structure and typographic feel: column layout, section order, how
section headings are set, rules and dividers, weight and size relationships,
and spacing density. Ignore the specific person entirely.

Return one JSON object with these keys:
  name         a short label for the design
  html_source  the complete Jinja2 HTML document
  css_source   the stylesheet
  notes        one sentence on what characterises this design
"""


def _client() -> anthropic.AsyncAnthropic:
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise TemplateGenerationError("ANTHROPIC_API_KEY is not set.")
    return anthropic.AsyncAnthropic(
        auth_token=settings.anthropic_api_key,
        base_url=settings.anthropic_base_url or None,
    )


def _document_block(raw: bytes, filename: str) -> list[dict[str, Any]]:
    """Show the model the document. A PDF goes as-is so it can see the layout."""
    lowered = filename.lower()
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
        ]
    if lowered.endswith(".docx"):
        # No layout to see, so describe the structure from the text instead.
        from docx import Document

        document = Document(io.BytesIO(raw))
        text = "\n".join(p.text for p in document.paragraphs if p.text.strip())
        return [
            {
                "type": "text",
                "text": (
                    "The source is a DOCX, so only its text is available. Infer a "
                    "clean single-column layout from the heading structure.\n\n"
                    f"{text[:20000]}"
                ),
            }
        ]
    raise TemplateGenerationError(
        "Only PDF and DOCX documents can be turned into a template."
    )


def validate_template(html_source: str, css_source: str) -> bytes:
    """Render the sample resume with this look. Raises if the template is unusable.

    Returns the rendered PDF, which doubles as the preview, so acceptance and
    preview generation are the same act and a stored template is always one that
    has demonstrably rendered.
    """
    rendered = render_resume_pdf(
        SAMPLE_RESUME, html_source=html_source, css_source=css_source
    )
    if not rendered.bytes_.startswith(b"%PDF"):
        raise TemplateGenerationError("The template did not produce a PDF.")
    if len(rendered.bytes_) < 1_000:
        raise TemplateGenerationError(
            "The template rendered an essentially empty page."
        )
    return rendered.bytes_


async def generate_template_from_document(
    raw: bytes, filename: str
) -> TemplateCandidate:
    """Ask for a template, then prove it renders before handing it back.

    Raises TemplateGenerationError when the design cannot be turned into a
    working template, so the caller can keep the default look and say so plainly.
    """
    settings = get_settings()
    client = _client()
    messages: list[anthropic.types.MessageParam] = [
        {
            "role": "user",
            "content": [*_document_block(raw, filename), {"type": "text", "text": USER_PROMPT}],
        }
    ]

    async def ask() -> str:
        response = await client.messages.create(
            model=settings.anthropic_model_extract,
            max_tokens=8192,
            system=SYSTEM_PROMPT,
            messages=messages,
            extra_headers={"x-manifest-tier": settings.manifest_tier_quality},
        )
        return response_text(response)

    def accept(raw_reply: str) -> TemplateCandidate:
        generated = parse_model_json(GeneratedTemplate, raw_reply)
        pdf_bytes = validate_template(generated.html_source, generated.css_source)
        return TemplateCandidate(
            name=generated.name.strip(),
            html_source=generated.html_source,
            css_source=generated.css_source,
            notes=generated.notes.strip(),
            pdf_bytes=pdf_bytes,
            preview_html=render_resume_html(
                SAMPLE_RESUME,
                html_source=generated.html_source,
                css_source=generated.css_source,
            ),
        )

    first = await ask()
    try:
        return accept(first)
    except Exception as first_error:  # noqa: BLE001 - see below
        # One repair attempt, showing the model its own output and what broke.
        # Anything can surface here: bad JSON, a Jinja syntax error, a sandbox
        # rejection, or a template that renders blank.
        log.warning("template.generation_failed", error=str(first_error)[:400])
        messages = [
            *messages,
            {"role": "assistant", "content": first[:6000] or "(empty)"},
            {
                "role": "user",
                "content": (
                    "That did not work. The error was:\n\n"
                    f"{str(first_error)[:1500]}\n\n"
                    "Fix the cause and return the corrected template. Remember to "
                    "guard optional fields, use only the listed variable names, "
                    "and keep all styling in css_source.\n\n" + JSON_ONLY_RETRY
                ),
            },
        ]
        try:
            return accept(await ask())
        except Exception as second_error:
            log.warning(
                "template.generation_failed_after_repair",
                error=str(second_error)[:400],
            )
            raise TemplateGenerationError(
                "Could not build a template from this design. The document may be "
                "an unusual layout or a scan. The default look is unchanged."
            ) from second_error
