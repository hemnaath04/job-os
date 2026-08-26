"""A project with no dates must not take the whole document down.

Every project in the sample carried dates, so an entry with none never got
rendered. It got rendered the moment the ranking check started putting a
genuinely undated project on the page: `job.os` has no start date, no end date
and no description, and husky's `\\entry` emitted `\\textbf{title}\\\\` against
an empty right-hand column.

`\\\\` with nothing before it inside a minipage is "There's no line here to
end", and LaTeX takes the whole document down rather than the entry: no
preview, no PDF, no review, no download. A resume that cannot compile is worse
than one that picked the wrong project.

Real renders through the real engine. A template bug of this shape is invisible
to anything that stops short of compiling.
"""
from __future__ import annotations

import copy
import shutil

import pytest

from job_os.services.latex_catalog import BUILTIN_TEMPLATES, SAMPLE_RESUME
from job_os.services.latex_render import render_resume_pdf

# Real compiles, so `slow` and the nightly job, per this repo's convention.
# Worth the five minutes there rather than nowhere: a template bug of this shape
# is invisible to anything that stops short of compiling.
pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        shutil.which("tectonic") is None or shutil.which("typst") is None,
        reason="needs the real render engines",
    ),
]

# job.os exactly as the vault holds it: a name, a bullet, and nothing else.
DATELESS = {
    "name": "job.os: AI Job-Search Platform",
    "highlights": [
        "Built a fact-grounded tailoring engine that rewrites bullets only from verified facts."
    ],
}


def _render(template_key: str, project: dict) -> bytes:
    resume = copy.deepcopy(SAMPLE_RESUME)
    resume["projects"] = [project]
    return render_resume_pdf(resume, template_key=template_key).bytes_


@pytest.mark.parametrize("spec", BUILTIN_TEMPLATES, ids=lambda s: s.key)
def test_every_template_survives_a_project_with_no_dates(spec) -> None:
    # Parametrised over the catalogue rather than the one that broke, because
    # the next template to grow an entry macro should fail here first.
    pdf = _render(spec.key, DATELESS)
    assert pdf.startswith(b"%PDF-")


@pytest.mark.parametrize(
    "project",
    [
        pytest.param({"name": "Thing", "startDate": "2025-01", "endDate": "2025-03",
                      "description": "Python", "highlights": ["Did it."]}, id="everything"),
        pytest.param({"name": "Thing", "startDate": "2025-01", "endDate": "2025-03",
                      "highlights": ["Did it."]}, id="dates-only"),
        pytest.param({"name": "Thing", "description": "Python",
                      "highlights": ["Did it."]}, id="description-only"),
        pytest.param({"name": "Thing"}, id="name-only"),
    ],
)
def test_husky_handles_every_combination_of_missing_fields(project: dict) -> None:
    # The right-hand column can be empty, date-only, second-line-only or both,
    # and only the last of those may carry a break between them.
    assert _render("husky", project).startswith(b"%PDF-")
