"""The six bundled LaTeX resume templates, and the sample data that previews them.

Each entry names a directory under `latex_templates/`, which holds the
template's `template.tex.j2`, whatever class file and fonts it needs, and the
upstream licence plus an ATTRIBUTION.md recording where it came from and what
was changed. Nothing is fetched at render time.

`ats_note` is written for the user, not for logs. Two-column layouts really do
confuse some applicant tracking systems, and a picker that hides that is a
picker that costs somebody an interview.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class BuiltinTemplate:
    key: str
    name: str
    description: str
    columns: int
    ats_note: str
    upstream: str
    licence: str
    author: str
    changes: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)


BUILTIN_TEMPLATES: tuple[BuiltinTemplate, ...] = (
    BuiltinTemplate(
        key="jakes",
        name="Jake's Resume",
        description=(
            "The single-column engineering resume that gets recommended on "
            "r/cscareerquestions. Computer Modern, tight rules, no colour."
        ),
        columns=1,
        ats_note=(
            "Single column and plain text throughout. The safest of the six "
            "for applicant tracking systems."
        ),
        upstream="https://github.com/jakegut/resume",
        licence="MIT",
        author="Jake Gutierrez",
        changes=(
            "Guarded the pdfTeX-only glyphtounicode block behind \\ifPDFTeX so it "
            "compiles under Tectonic's XeTeX engine."
        ),
        tags=("single column", "classic", "ats safe"),
    ),
    BuiltinTemplate(
        key="sb2nov",
        name="sb2nov",
        description=(
            "Sourabh Bajaj's compact resume. Same lineage as Jake's, with "
            "bulleted section entries and a narrower header."
        ),
        columns=1,
        ats_note="Single column and plain text throughout. Parses cleanly.",
        upstream="https://github.com/sb2nov/resume",
        licence="MIT",
        author="Sourabh Bajaj",
        changes=(
            "Guarded the pdfTeX-only glyphtounicode block behind \\ifPDFTeX so it "
            "compiles under Tectonic's XeTeX engine."
        ),
        tags=("single column", "compact", "ats safe"),
    ),
    BuiltinTemplate(
        key="awesome-cv",
        name="Awesome-CV",
        description=(
            "The sharp, coloured header look with Font Awesome contact icons "
            "and Source Sans text. Single column body."
        ),
        columns=1,
        ats_note=(
            "Single column, so the reading order is unambiguous. The contact "
            "icons are glyphs, so a parser may read a stray character next to "
            "your email or phone number."
        ),
        upstream="https://github.com/posquit0/Awesome-CV",
        licence="LPPL-1.3c",
        author="Claud D. Park (posquit0)",
        changes=(
            "Vendored as awesome-cv-tectonic.cls: loads fontawesome5 rather than "
            "fontawesome6, which Tectonic's bundle does not carry, and names its "
            "fonts by file rather than by family."
        ),
        tags=("single column", "colour accent", "icons"),
    ),
    BuiltinTemplate(
        key="altacv",
        name="AltaCV",
        description=(
            "Two-column CV with a sidebar for skills and tags. The academic "
            "and product-facing look of the set."
        ),
        columns=2,
        ats_note=(
            "Two columns. Some applicant tracking systems flatten a two-column "
            "page into one text stream and interleave the sidebar with your "
            "experience. Prefer a single-column template when a posting says a "
            "resume is parsed automatically."
        ),
        upstream="https://github.com/liantze/AltaCV",
        licence="LPPL-1.3 or later",
        author="LianTze Lim",
        changes=(
            "Vendored as altacv-tectonic.cls: loads hyperref directly instead of "
            "pdfx, which needs a texlua Tectonic does not ship, and names its "
            "fonts by file rather than by family."
        ),
        tags=("two column", "sidebar", "colour"),
    ),
    BuiltinTemplate(
        key="moderncv",
        name="ModernCV (banking)",
        description=(
            "The long-serving CTAN CV class, in its banking style: a centred "
            "name, ruled sections and a roomy two-column entry grid."
        ),
        columns=1,
        ats_note=(
            "Single column body. Entry dates sit in their own column, which "
            "some parsers read as a separate field."
        ),
        upstream="https://ctan.org/pkg/moderncv",
        licence="LPPL-1.3c",
        author="Xavier Danaux and the moderncv maintainers",
        changes=(
            "Nothing vendored: the class ships inside Tectonic's own package "
            "bundle. Uses the letter icon set rather than Font Awesome so the "
            "contact line extracts as text."
        ),
        tags=("single column", "traditional", "ctan"),
    ),
    BuiltinTemplate(
        key="deedy",
        name="Deedy",
        description=(
            "Debarghya Das's two-column resume in Lato and Raleway. The most "
            "designed of the six."
        ),
        columns=2,
        ats_note=(
            "Two columns, and the layout is the point of it. This is the "
            "riskiest of the six for automated parsing: a system that reads the "
            "page as one stream will mix the right column into the left. Good "
            "for a human reader or a portfolio, not for a bulk application."
        ),
        upstream="https://github.com/deedy/Deedy-Resume",
        licence="Apache-2.0 (fonts SIL OFL 1.1)",
        author="Debarghya Das",
        changes=(
            "Uses the repository's OpenFonts variant unchanged. Lato and Raleway "
            "are vendored alongside it, with the OFL text the upstream repository "
            "omits."
        ),
        tags=("two column", "designed", "lato"),
    ),
)

_BY_KEY = {spec.key: spec for spec in BUILTIN_TEMPLATES}

# Jake's is the default because it is single column, has no icon glyphs and no
# custom class, which makes it the one most likely to survive a parser.
DEFAULT_TEMPLATE_KEY = "jakes"


def builtin(key: str) -> BuiltinTemplate:
    return _BY_KEY[key]



# ---------------------------------------------------------------------------
# Preview data
# ---------------------------------------------------------------------------

# Deliberately, obviously invented. Previews exist to show a layout, and a
# preview built from the user's real history would put their employer and their
# phone number into a shared thumbnail. Everything here is a reserved example
# domain, a 555 phone number, or a name no employer has.
SAMPLE_RESUME: dict[str, Any] = {
    "basics": {
        "name": "Jordan A. Sample",
        "label": "Backend and AI Engineer",
        "email": "jordan@example.com",
        "phone": "+1 555 010 0100",
        "url": "https://example.com/jordan",
        "summary": (
            "Backend engineer who ships data-heavy services. This is sample "
            "text so the template can be previewed; it is not a real resume."
        ),
        "location": {"city": "Boston", "region": "MA"},
        "profiles": [
            {"network": "GitHub", "username": "jordan-sample", "url": "https://github.com/jordan-sample"},
            {
                "network": "LinkedIn",
                "username": "jordan-sample",
                "url": "https://linkedin.com/in/jordan-sample",
            },
        ],
    },
    "work": [
        {
            "name": "Example Systems",
            "position": "Backend Engineer",
            "location": "Boston, MA",
            "startDate": "2024-06",
            "endDate": None,
            "highlights": [
                "Built an ingestion service that handles 4 million rows a day.",
                "Cut p95 request latency from 900 ms to 210 ms by reshaping two queries.",
                "Wrote the contract tests that let three teams deploy the API independently.",
            ],
        },
        {
            "name": "Placeholder Labs",
            "position": "Software Engineer, Test Automation",
            "location": "Remote",
            "startDate": "2022-08",
            "endDate": "2024-05",
            "highlights": [
                "Replaced a nightly manual regression pass with a suite that runs in 11 minutes.",
                "Cut flaky failures from 14% of runs to under 1% by isolating shared fixtures.",
            ],
        },
    ],
    "education": [
        {
            "institution": "Sample University",
            "area": "Computer Science",
            "studyType": "Master of Science",
            "location": "Boston, MA",
            "startDate": "2026-01",
            "endDate": "2028-05",
            "score": "3.9/4.0",
            "courses": ["Distributed Systems", "Machine Learning"],
        },
        {
            "institution": "Example Institute of Technology",
            "area": "Information Technology",
            "studyType": "Bachelor of Engineering",
            "location": "Chennai, India",
            "startDate": "2018-07",
            "endDate": "2022-05",
        },
    ],
    "projects": [
        {
            "name": "Ledger Reconciler",
            "description": "Matches two ledgers and explains every mismatch it cannot match.",
            "url": "https://github.com/jordan-sample/ledger-reconciler",
            "startDate": "2025-02",
            "endDate": "2025-06",
            "keywords": ["Python", "Postgres", "FastAPI"],
            "highlights": [
                "Reconciles 200k rows in 8 seconds and prints a reason for each exception.",
                "Ships a CLI and an HTTP API from one core module.",
            ],
        },
        {
            "name": "Sample Search",
            "description": "A small hybrid search index over a document set.",
            "url": "https://github.com/jordan-sample/sample-search",
            "startDate": "2024-09",
            "endDate": "2024-12",
            "keywords": ["Rust", "SQLite", "Embeddings"],
            "highlights": [
                "Serves keyword and vector results from one index file.",
            ],
        },
    ],
    "skills": [
        {"name": "Languages", "keywords": ["Python", "Java", "SQL", "Go"]},
        {"name": "Infrastructure", "keywords": ["Docker", "Postgres", "Redis", "AWS"]},
        {"name": "Practices", "keywords": ["Testing", "Observability", "CI/CD"]},
    ],
    "certificates": [
        {"name": "Sample Cloud Practitioner", "issuer": "Example Cloud", "date": "2025-03"},
    ],
    "awards": [
        {"title": "Sample Hackathon, first place", "awarder": "Example Systems", "summary": ""},
    ],
    "languages": [
        {"language": "English", "fluency": "Full professional"},
        {"language": "Tamil", "fluency": "Native"},
    ],
    "interests": [],
    "publications": [],
}
