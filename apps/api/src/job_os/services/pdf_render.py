"""Render a JSON Resume document to PDF via Jinja2 + WeasyPrint.

The default template (`master_resume.html.j2` + `master_resume.css`) is a
faithful recreation of the user's existing master PDF layout. Future
templates can be added under `templates/` and selected by name.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from jinja2 import Environment, FileSystemLoader
from jinja2.sandbox import SandboxedEnvironment

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
DEFAULT_TEMPLATE = "master_resume"


def _weasyprint():
    """Lazy import — WeasyPrint loads native libs at module import time, and
    on macOS those need DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib. Lazy-
    loading means the API can start even if the libs aren't reachable, and
    the helpful error fires only on actual render attempts."""
    try:
        from weasyprint import CSS, HTML
        return HTML, CSS
    except OSError as e:
        raise RuntimeError(
            "WeasyPrint native libs not loadable. On macOS run:\n"
            "  brew install pango\n"
            "  export DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib\n"
            "Then restart the API process. Original error: " + str(e)
        ) from e


@dataclass(slots=True)
class RenderedPdf:
    bytes_: bytes
    content_type: str = "application/pdf"


_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=True,
    trim_blocks=True,
    lstrip_blocks=True,
)

# Templates stored in the database, and in particular templates a model wrote
# from an uploaded PDF, are untrusted input that this process is about to
# execute. A plain Environment would let one reach attributes off the context
# and walk into module globals, which in this container means the database URL
# and the gateway key. SandboxedEnvironment blocks attribute access to unsafe
# internals and unsafe calls, there is no loader so {% include %} and
# {% extends %} cannot touch the filesystem, and autoescape stays on so resume
# content cannot inject markup either.
_sandbox = SandboxedEnvironment(
    loader=None,
    autoescape=True,
    trim_blocks=True,
    lstrip_blocks=True,
)


def _safe_resume_url(value: Any) -> str:
    """Allow only ordinary web links in rendered resume anchors."""
    text = str(value or "").strip()
    try:
        parsed = urlparse(text)
    except ValueError:
        return ""
    return text if parsed.scheme in {"http", "https"} and parsed.netloc else ""


def _fmt_date(value: str | date | datetime | None) -> str | None:
    """JSON Resume dates are typically YYYY-MM or YYYY. Render them
    like 'Jul 2024'. Pass through anything we don't recognise."""
    if value is None or value == "":
        return None
    if isinstance(value, (date, datetime)):
        d = value
    else:
        parts = str(value).split("-")
        try:
            year = int(parts[0])
        except ValueError:
            return str(value)
        month = int(parts[1]) if len(parts) > 1 else None
        day = int(parts[2]) if len(parts) > 2 else None
        if month is None:
            return str(year)
        d = date(year, month, day or 1)
    return d.strftime("%b %Y")


# An en dash, the character a numeric range takes, and what LaTeX's "--" sets.
# The em dash this used to print is banned everywhere on the page; the career-ops
# playbook makes the date range the one place a range dash is still correct.
RANGE_DASH = "–"


def _fmt_range(
    start: str | date | datetime | None,
    end: str | date | datetime | None,
    *,
    present_label: str = "Present",
    ongoing_suffix: str | None = None,
) -> str:
    s = _fmt_date(start)
    e = _fmt_date(end)
    if not s and not e:
        return ""
    if not e:
        return f"{s} {RANGE_DASH} {present_label}"
    if not s:
        return e or ""
    if ongoing_suffix and end and _is_future(end):
        return f"{s} {RANGE_DASH} {e} {ongoing_suffix}"
    return f"{s} {RANGE_DASH} {e}"


def _is_future(value: str | date | datetime) -> bool:
    if isinstance(value, (date, datetime)):
        d = value if isinstance(value, date) else value.date()
    else:
        parts = str(value).split("-")
        try:
            year = int(parts[0])
            month = int(parts[1]) if len(parts) > 1 else 1
            d = date(year, month, 1)
        except (ValueError, IndexError):
            return False
    return d > date.today()


for _environment in (_env, _sandbox):
    _environment.filters["fmt_date"] = _fmt_date
    _environment.filters["safe_resume_url"] = _safe_resume_url


def resume_context(json_resume: dict[str, Any]) -> dict[str, Any]:
    """The only names a resume template may use.

    Kept in one place because it is a contract in three directions: the bundled
    template, a stored template, and the prompt in phase 3 that asks a model to
    write a template must all agree on it.
    """
    return {
        "basics": json_resume.get("basics") or {},
        "education": json_resume.get("education") or [],
        "work": json_resume.get("work") or [],
        "projects": json_resume.get("projects") or [],
        "skills": json_resume.get("skills") or [],
        "certificates": json_resume.get("certificates") or [],
        "languages": json_resume.get("languages") or [],
        "interests": json_resume.get("interests") or [],
        "publications": json_resume.get("publications") or [],
        "awards": json_resume.get("awards") or [],
        "fmt_range": _fmt_range,
    }


def render_resume_pdf(
    json_resume: dict[str, Any],
    *,
    template: str = DEFAULT_TEMPLATE,
    html_source: str | None = None,
    css_source: str | None = None,
) -> RenderedPdf:
    """Render a JSON Resume to a PDF byte string.

    Pass `html_source` (and optionally `css_source`) to render a stored template
    rather than the bundled one. Stored templates go through the sandboxed
    environment, since their markup is untrusted.
    """
    context = resume_context(json_resume)
    if html_source is not None:
        html_str = _sandbox.from_string(html_source).render(**context)
        css_text = css_source or ""
    else:
        html_str = _env.get_template(f"{template}.html.j2").render(**context)
        css_path = TEMPLATES_DIR / f"{template}.css"
        css_text = css_path.read_text() if css_path.exists() else ""

    html_renderer, css_renderer = _weasyprint()
    pdf_bytes = html_renderer(string=html_str, base_url=str(TEMPLATES_DIR)).write_pdf(
        stylesheets=[css_renderer(string=css_text)] if css_text.strip() else None,
    )
    return RenderedPdf(bytes_=pdf_bytes)


def render_resume_html(
    json_resume: dict[str, Any],
    *,
    template: str = DEFAULT_TEMPLATE,
    html_source: str | None = None,
    css_source: str | None = None,
) -> str:
    """Useful for previewing a template before paying the PDF render cost."""
    context = resume_context(json_resume)
    if html_source is not None:
        html_str = _sandbox.from_string(html_source).render(**context)
        css_text = css_source or ""
    else:
        html_str = _env.get_template(f"{template}.html.j2").render(**context)
        css_path = TEMPLATES_DIR / f"{template}.css"
        css_text = css_path.read_text() if css_path.exists() else ""

    # Inline the stylesheet so the HTML is self-contained. The bundled template
    # links its stylesheet by name; a stored one gets the style block appended
    # into <head>, falling back to a prefix if there is no head to target.
    linked = '<link rel="stylesheet" href="master_resume.css" />'
    if linked in html_str:
        return html_str.replace(linked, f"<style>{css_text}</style>")
    if not css_text.strip():
        return html_str
    if "</head>" in html_str:
        return html_str.replace("</head>", f"<style>{css_text}</style></head>", 1)
    return f"<style>{css_text}</style>{html_str}"
