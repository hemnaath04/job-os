from __future__ import annotations

import pytest
from jinja2.exceptions import SecurityError, UndefinedError

from job_os.services.pdf_render import (
    render_resume_html,
    resume_context,
)

RESUME = {
    "basics": {"name": "A Candidate", "email": "a@b.com"},
    "work": [
        {
            "name": "Acme",
            "position": "Engineer",
            "startDate": "2024-07",
            "endDate": None,
            "highlights": ["Shipped a thing."],
        }
    ],
    "skills": [{"name": "Languages", "keywords": ["Python"]}],
}


def test_stored_template_renders_the_resume() -> None:
    html = render_resume_html(
        RESUME,
        html_source=(
            "<html><head></head><body>"
            "<h1>{{ basics.name }}</h1>"
            "{% for job in work %}"
            "<p>{{ job.position }} {{ fmt_range(job.startDate, job.endDate) }}</p>"
            "{% endfor %}"
            "</body></html>"
        ),
        css_source="h1 { color: red; }",
    )
    assert "A Candidate" in html
    assert "Engineer" in html
    # fmt_range is part of the documented context, so a template may call it.
    assert "Jul 2024" in html
    # CSS is inlined into head so the preview is self-contained.
    assert "<style>h1 { color: red; }</style></head>" in html


def test_resume_content_cannot_inject_markup() -> None:
    """Autoescape stays on for stored templates."""
    html = render_resume_html(
        {"basics": {"name": "<script>alert(1)</script>"}},
        html_source="<p>{{ basics.name }}</p>",
    )
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_stored_template_cannot_reach_python_internals() -> None:
    """The sandbox is the whole reason a model may write these templates.

    Without it, a stored or generated template could walk from any context
    object into module globals, which in the API container holds the database
    URL and the gateway key.
    """
    with pytest.raises((SecurityError, UndefinedError)):
        render_resume_html(
            RESUME,
            html_source="{{ basics.__class__.__mro__[1].__subclasses__() }}",
        )


def test_stored_template_cannot_read_the_filesystem() -> None:
    """No loader, so include and extends have nothing to reach."""
    with pytest.raises(Exception) as excinfo:
        render_resume_html(RESUME, html_source="{% include 'master_resume.html.j2' %}")
    assert "TemplateNotFound" in type(excinfo.value).__name__ or "no loader" in str(
        excinfo.value
    ).lower()


def test_bundled_template_still_renders_unchanged() -> None:
    """The default look must keep working with no template argument."""
    html = render_resume_html(RESUME)
    assert "A Candidate" in html
    assert "<style>" in html


def test_resume_context_exposes_only_the_documented_names() -> None:
    # Phase 3 asks a model to write against exactly this list, so pin it.
    assert set(resume_context({})) == {
        "basics",
        "education",
        "work",
        "projects",
        "skills",
        "certificates",
        "languages",
        "interests",
        "publications",
        "awards",
        "fmt_range",
    }
