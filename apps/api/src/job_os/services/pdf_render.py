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

from jinja2 import Environment, FileSystemLoader, select_autoescape

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
    autoescape=select_autoescape(["html", "xml"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


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
        return f"{s} — {present_label}"
    if not s:
        return e or ""
    if ongoing_suffix and end and _is_future(end):
        return f"{s} — {e} {ongoing_suffix}"
    return f"{s} — {e}"


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


_env.filters["fmt_date"] = _fmt_date


def render_resume_pdf(
    json_resume: dict[str, Any],
    *,
    template: str = DEFAULT_TEMPLATE,
) -> RenderedPdf:
    """Render a JSON Resume to a PDF byte string."""
    tmpl = _env.get_template(f"{template}.html.j2")
    css_path = TEMPLATES_DIR / f"{template}.css"

    context: dict[str, Any] = {
        "basics": json_resume.get("basics") or {},
        "education": json_resume.get("education") or [],
        "work": json_resume.get("work") or [],
        "projects": json_resume.get("projects") or [],
        "skills": json_resume.get("skills") or [],
        "certificates": json_resume.get("certificates") or [],
        "interests": json_resume.get("interests") or [],
        "publications": json_resume.get("publications") or [],
        "awards": json_resume.get("awards") or [],
        "fmt_range": _fmt_range,
    }
    html_str = tmpl.render(**context)

    HTML, CSS = _weasyprint()
    pdf_bytes = HTML(string=html_str, base_url=str(TEMPLATES_DIR)).write_pdf(
        stylesheets=[CSS(filename=str(css_path))] if css_path.exists() else None,
    )
    return RenderedPdf(bytes_=pdf_bytes)


def render_resume_html(
    json_resume: dict[str, Any],
    *,
    template: str = DEFAULT_TEMPLATE,
) -> str:
    """Useful for previewing the template before paying the PDF render cost."""
    tmpl = _env.get_template(f"{template}.html.j2")
    css_path = TEMPLATES_DIR / f"{template}.css"
    css_text = css_path.read_text() if css_path.exists() else ""
    html_str = tmpl.render(
        basics=json_resume.get("basics") or {},
        education=json_resume.get("education") or [],
        work=json_resume.get("work") or [],
        projects=json_resume.get("projects") or [],
        skills=json_resume.get("skills") or [],
        certificates=json_resume.get("certificates") or [],
        interests=json_resume.get("interests") or [],
        publications=json_resume.get("publications") or [],
        awards=json_resume.get("awards") or [],
        fmt_range=_fmt_range,
    )
    # Inline the stylesheet so the HTML is self-contained.
    return html_str.replace(
        '<link rel="stylesheet" href="master_resume.css" />',
        f"<style>{css_text}</style>",
    )
