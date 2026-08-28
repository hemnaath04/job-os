"""Render a JSON Resume document to PDF with real LaTeX, compiled by Tectonic.

Three pieces, in the order the data moves through them:

1. `latex_escape` and friends turn resume values into LaTeX-safe text. Every
   string a user or a model can influence goes through this, without exception.
   A stray `%` in a job title used to end the line and silently swallow the
   rest of it; a stray `\\` could execute.
2. `build_render_model` shapes the JSON Resume into the flat, pre-escaped names
   a template may use. This is the template contract, and it is the same
   contract in three directions: the seven bundled templates, a template stored
   in Appwrite, and the prompt that asks a model to write one from an upload.
3. `compile_pdf` runs Tectonic over the filled source in a scratch directory.

Tectonic is a patched XeTeX. There is no pdflatex and no lualatex here, so
engine-specific parts of upstream templates are adapted rather than assumed;
see the per-template ATTRIBUTION.md files under `latex_templates/`.
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "latex_templates"

# The output file Tectonic is asked to produce, and the input name it reads.
_TEX_NAME = "resume.tex"
_PDF_NAME = "resume.pdf"

# A resume compiles in a couple of seconds once the bundle cache is warm. This
# ceiling exists for the pathological case: a stored or model-written template
# with a runaway loop should fail the request, not hold the worker forever.
COMPILE_TIMEOUT_SECONDS = 90


class LatexRenderError(RuntimeError):
    """A template did not compile. Carries the part of the log that says why."""

    def __init__(self, message: str, *, log: str = "") -> None:
        super().__init__(message)
        self.log = log


class TectonicUnavailableError(LatexRenderError):
    """No tectonic binary on PATH, so this runtime cannot render at all.

    Separate from a compile failure because it is a deployment fact, not a
    problem with the document: the Appwrite function runtime has no LaTeX
    engine, the API container does.
    """


@dataclass(slots=True)
class RenderedPdf:
    bytes_: bytes
    content_type: str = "application/pdf"


# ---------------------------------------------------------------------------
# Escaping
# ---------------------------------------------------------------------------

# The seven characters TeX reserves, plus the backslash. Order matters only in
# that this is a single pass over the string, so no replacement can re-trigger.
_ESCAPES = {
    "\\": r"\textbackslash{}",
    "{": r"\{",
    "}": r"\}",
    "$": r"\$",
    "&": r"\&",
    "#": r"\#",
    "^": r"\textasciicircum{}",
    "_": r"\_",
    "~": r"\textasciitilde{}",
    "%": r"\%",
}

# Typographic characters that arrive from PDFs, Word documents and models.
# XeTeX is Unicode-native, but the Computer Modern faces two of the bundled
# templates use have no glyph for these, and a missing glyph prints nothing at
# all. Mapping them to their LaTeX spellings renders correctly everywhere.
_TRANSLITERATE = {
    "\u2014": "---",
    "\u2013": "--",
    "\u2212": "-",
    "\u2018": "`",
    "\u2019": "'",
    "\u201c": "``",
    "\u201d": "''",
    "\u2026": r"\dots{}",
    "\u2022": r"\textbullet{}",
    "\u00a0": "~",
    "\u2009": r"\,",
    "\u200b": "",
    "\ufeff": "",
}

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def latex_escape(value: Any) -> str:
    """Make any value safe to drop into a LaTeX document as text.

    Not optional anywhere. A resume field is data, never markup: a candidate
    who worked on "C# & .NET" and one who typed a stray backslash must both
    render, and neither may reach the engine as instructions.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    text = str(value)
    text = _CONTROL_CHARS.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    out: list[str] = []
    for char in text:
        if char in _ESCAPES:
            out.append(_ESCAPES[char])
        elif char in _TRANSLITERATE:
            out.append(_TRANSLITERATE[char])
        elif char == "\n":
            # A newline inside one field is a soft break, not a paragraph: a
            # blank line would end the enclosing item or table cell.
            out.append(" ")
        else:
            out.append(char)
    return "".join(out).strip()


# Characters that have no business in a URL and that would need escaping in a
# way `\href` does not survive. A link carrying one of these is dropped rather
# than mangled, so a bad link never becomes broken markup.
_URL_FORBIDDEN = set("\\{}^$\"'`<> \t\n")

# What hyperref does need escaped inside a link target.
_URL_ESCAPES = (
    ("%", r"\%"),
    ("#", r"\#"),
    ("&", r"\&"),
    ("_", r"\_"),
    ("~", r"\string~"),
)


def latex_escape_url(value: Any) -> str:
    """An http(s) URL, safe as the first argument of `\\href`, or "".

    Escapes only what hyperref needs escaped in a target, and refuses anything
    that cannot be expressed that way. Non-web schemes are dropped: a resume
    link is a web link, and `javascript:` or `file:` in a PDF annotation is a
    trap for whoever opens the file.
    """
    text = str(value or "").strip()
    if not text or any(char in _URL_FORBIDDEN for char in text):
        return ""
    try:
        parsed = urlparse(text)
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    for char, escaped in _URL_ESCAPES:
        text = text.replace(char, escaped)
    return text


def link_label(value: Any) -> str:
    """How a URL should read on paper: no scheme, no trailing slash."""
    text = str(value or "").strip()
    text = re.sub(r"^https?://", "", text)
    text = re.sub(r"^www\.", "", text)
    return latex_escape(text.rstrip("/"))


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------


def _fmt_date(
    value: str | date | datetime | None,
    *,
    escape: Callable[[Any], str] = latex_escape,
) -> str:
    """JSON Resume dates are YYYY-MM or YYYY. Render them like 'Jul 2024'.

    `escape` is how to render a value that is not a date at all, which is the
    one branch here that emits user text verbatim. The Typst renderer shares
    this function and passes its own cleaner, so the two engines cannot start
    formatting the same date differently.
    """
    if value is None or value == "":
        return ""
    if isinstance(value, (date, datetime)):
        return value.strftime("%b %Y")
    parts = str(value).split("-")
    try:
        year = int(parts[0])
    except ValueError:
        return escape(value)
    try:
        month = int(parts[1]) if len(parts) > 1 else None
    except ValueError:
        month = None
    if month is None or not 1 <= month <= 12:
        return str(year)
    return date(year, month, 1).strftime("%b %Y")


def _is_future(value: str | date | datetime) -> bool:
    if isinstance(value, datetime):
        return value.date() > date.today()
    if isinstance(value, date):
        return value > date.today()
    parts = str(value).split("-")
    try:
        year = int(parts[0])
        month = int(parts[1]) if len(parts) > 1 else 1
        return date(year, month, 1) > date.today()
    except (ValueError, IndexError):
        return False


def date_range(
    start: Any,
    end: Any,
    *,
    present_label: str = "Present",
    expected_label: str = "expected",
) -> str:
    """A date range as LaTeX, using `--` for the dash.

    The em dash is banned across this codebase and an en dash character has no
    glyph in every bundled face, so the range takes LaTeX's own `--`, which is
    what a typeset en dash is supposed to be.
    """
    left = _fmt_date(start)
    right = _fmt_date(end)
    if not left and not right:
        return ""
    if not right:
        return f"{left} -- {present_label}" if left else ""
    if not left:
        return right
    if end and _is_future(end):
        return f"{left} -- {right} ({expected_label})"
    return f"{left} -- {right}"


# ---------------------------------------------------------------------------
# The template contract
# ---------------------------------------------------------------------------


def _clean_list(values: Any) -> list[str]:
    if not isinstance(values, (list, tuple)):
        return []
    return [latex_escape(v) for v in values if str(v or "").strip()]


def _profiles(basics: dict[str, Any]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for profile in basics.get("profiles") or []:
        if not isinstance(profile, dict):
            continue
        url = latex_escape_url(profile.get("url"))
        network = latex_escape(profile.get("network"))
        username = latex_escape(profile.get("username"))
        label = link_label(profile.get("url")) or username or network
        if not (url or label):
            continue
        out.append({"network": network, "username": username, "url": url, "label": label})
    return out


def _mailto(email: Any) -> str:
    """A `mailto:` target for `\\href`, or "" if the address is not one.

    Kept apart from latex_escape_url, which only admits http and https: a
    resume needs a clickable email address, and nothing else non-web.
    """
    text = str(email or "").strip()
    if not text or any(char in _URL_FORBIDDEN for char in text):
        return ""
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", text):
        return ""
    for char, escaped in _URL_ESCAPES:
        text = text.replace(char, escaped)
    return f"mailto:{text}"


def _named_profile(profiles: list[dict[str, str]], network: str) -> dict[str, str] | None:
    for profile in profiles:
        if network.lower() in profile["network"].lower():
            return profile
        if network.lower() in profile["label"].lower():
            return profile
    return None


def build_render_model(json_resume: dict[str, Any]) -> dict[str, Any]:
    """The only names a LaTeX resume template may use. Every string escaped.

    Flat and pre-escaped on purpose. A template cannot reach a raw value, so a
    template cannot leak one into the engine, whether it was written here or by
    a model from somebody's uploaded design.
    """
    basics = json_resume.get("basics") or {}
    location = basics.get("location") or {}
    profiles = _profiles(basics)

    city = latex_escape(location.get("city"))
    region = latex_escape(location.get("region"))
    where = ", ".join(part for part in (city, region) if part)

    work: list[dict[str, Any]] = []
    for item in json_resume.get("work") or []:
        if not isinstance(item, dict):
            continue
        item_location = item.get("location")
        work.append(
            {
                "company": latex_escape(item.get("name") or item.get("company")),
                "position": latex_escape(item.get("position")),
                "location": latex_escape(item_location),
                "url": latex_escape_url(item.get("url")),
                "summary": latex_escape(item.get("summary")),
                "dates": date_range(item.get("startDate"), item.get("endDate")),
                "start": _fmt_date(item.get("startDate")),
                "end": _fmt_date(item.get("endDate")) or "Present",
                "bullets": _clean_list(item.get("highlights")),
            }
        )

    education: list[dict[str, Any]] = []
    for item in json_resume.get("education") or []:
        if not isinstance(item, dict):
            continue
        study = latex_escape(item.get("studyType"))
        area = latex_escape(item.get("area"))
        degree = ", ".join(part for part in (study, area) if part)
        education.append(
            {
                "institution": latex_escape(item.get("institution")),
                "area": area,
                "study_type": study,
                "degree": degree,
                "location": latex_escape(item.get("location")),
                "score": latex_escape(item.get("score")),
                "dates": date_range(item.get("startDate"), item.get("endDate")),
                "bullets": _clean_list(item.get("courses")),
            }
        )

    projects: list[dict[str, Any]] = []
    for item in json_resume.get("projects") or []:
        if not isinstance(item, dict):
            continue
        projects.append(
            {
                "name": latex_escape(item.get("name")),
                "description": latex_escape(item.get("description")),
                "url": latex_escape_url(item.get("url")),
                "url_label": link_label(item.get("url")),
                "dates": date_range(item.get("startDate"), item.get("endDate")),
                "keywords": _clean_list(item.get("keywords")),
                "keywords_line": ", ".join(_clean_list(item.get("keywords"))),
                "bullets": _clean_list(item.get("highlights")),
            }
        )

    skills: list[dict[str, Any]] = []
    for item in json_resume.get("skills") or []:
        if not isinstance(item, dict):
            continue
        keywords = _clean_list(item.get("keywords"))
        skills.append(
            {
                "name": latex_escape(item.get("name")),
                "level": latex_escape(item.get("level")),
                "keywords": keywords,
                "keywords_line": ", ".join(keywords),
            }
        )

    def simple(section: str, *fields: str) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        for item in json_resume.get(section) or []:
            if not isinstance(item, dict):
                continue
            row = {field: latex_escape(item.get(field)) for field in fields}
            if any(row.values()):
                out.append(row)
        return out

    certificates = []
    for item in json_resume.get("certificates") or []:
        if not isinstance(item, dict):
            continue
        certificates.append(
            {
                "name": latex_escape(item.get("name")),
                "issuer": latex_escape(item.get("issuer")),
                "date": _fmt_date(item.get("date")),
                "url": latex_escape_url(item.get("url")),
            }
        )

    # One ordered list so every template writes the same contact line the same
    # way, and so no template has to know how to build a mailto or decide what
    # to do when a link is missing.
    #
    # Location leads rather than trails: jakes/deedy/dashline lay this whole
    # list out as one run of `\mbox`/`box`-wrapped items joined by " | " and
    # centered, wrapping wherever it runs out of width -- exactly like a line
    # of justified text. Whichever item lands last is what gets isolated onto
    # its own line when that happens, and a lone centered "Boston, MA" reads
    # as a second, unstyled heading under the name rather than as leftover
    # contact info. altacv/moderncv/awesome-cv are unaffected either way: they
    # already re-sort or filter this list themselves rather than trusting
    # input order.
    contact: list[dict[str, str]] = []
    if where:
        contact.append({"kind": "location", "text": where, "url": ""})
    if basics.get("phone"):
        contact.append({"kind": "phone", "text": latex_escape(basics.get("phone")), "url": ""})
    if basics.get("email"):
        contact.append(
            {
                "kind": "email",
                "text": latex_escape(basics.get("email")),
                "url": _mailto(basics.get("email")),
            }
        )
    if latex_escape_url(basics.get("url")):
        contact.append(
            {
                "kind": "website",
                "text": link_label(basics.get("url")),
                "url": latex_escape_url(basics.get("url")),
            }
        )
    for profile in profiles:
        contact.append(
            {
                "kind": profile["network"].lower() or "link",
                "text": profile["label"],
                "url": profile["url"],
            }
        )
    # Last, and only when the tailored document set it, which it does only when
    # the posting asked when the candidate is free. Every template iterates this
    # list, so this is the one field that reaches all of them; the two that
    # re-sort it by a known-kind order put an unrecognised kind at the end,
    # which is where this belongs anyway. Mirrors typst_render.py.
    if latex_escape(basics.get("availability")):
        contact.append(
            {
                "kind": "availability",
                "text": latex_escape(basics.get("availability")),
                "url": "",
            }
        )

    # Awesome-CV's \name takes two arguments, and its header sets them in
    # different weights. Split here rather than in a template, where the value
    # is already escaped and splitting it could cut an escape sequence in half.
    raw_name = str(basics.get("name") or "").strip()
    first, _, last = raw_name.partition(" ")

    return {
        "name": latex_escape(basics.get("name")),
        "first_name": latex_escape(first),
        "last_name": latex_escape(last),
        "headline": latex_escape(basics.get("label")),
        "email": latex_escape(basics.get("email")),
        "email_url": _mailto(basics.get("email")),
        "contact": contact,
        "phone": latex_escape(basics.get("phone")),
        "website": latex_escape_url(basics.get("url")),
        "website_label": link_label(basics.get("url")),
        "summary": latex_escape(basics.get("summary")),
        "city": city,
        "region": region,
        "location": where,
        "profiles": profiles,
        "linkedin": _named_profile(profiles, "linkedin"),
        "github": _named_profile(profiles, "github"),
        "work": work,
        "education": education,
        "projects": projects,
        "skills": skills,
        "certificates": certificates,
        "awards": simple("awards", "title", "awarder", "summary"),
        "publications": simple("publications", "name", "publisher", "summary"),
        "languages": simple("languages", "language", "fluency"),
        "interests": simple("interests", "name"),
    }


# ---------------------------------------------------------------------------
# Template filling
# ---------------------------------------------------------------------------

# LaTeX already owns `{`, `}` and `%`, so Jinja's usual delimiters would fight
# the syntax they are embedded in. These three pairs appear nowhere in the
# bundled templates' LaTeX.
_JINJA_SYNTAX = {
    "block_start_string": "<%",
    "block_end_string": "%>",
    "variable_start_string": "<<",
    "variable_end_string": ">>",
    "comment_start_string": "<#",
    "comment_end_string": "#>",
    "trim_blocks": True,
    "lstrip_blocks": True,
    "keep_trailing_newline": True,
    # Escaping happens once, in build_render_model, over the whole document.
    # Jinja's autoescape is HTML-shaped and would be wrong here.
    "autoescape": False,
}


def _environment(*, loader: Any = None) -> SandboxedEnvironment:
    """Sandboxed because a stored template is untrusted Python-adjacent input.

    A plain Environment lets a template walk attributes into module globals,
    which in this process means the database URL and the gateway key. The
    sandbox blocks that, and a template with no loader cannot `include` a file
    off the filesystem either.
    """
    env = SandboxedEnvironment(loader=loader, undefined=StrictUndefined, **_JINJA_SYNTAX)
    env.filters["latex"] = latex_escape
    env.globals["date_range"] = date_range
    env.globals["today"] = date.today().strftime("%b %Y")
    return env


def fill_template(latex_source: str, model: dict[str, Any]) -> str:
    """Fill a template's placeholders from an already-escaped render model."""
    return _environment().from_string(latex_source).render(**model)


def load_builtin_source(key: str) -> str:
    """Read a bundled template's LaTeX. Raises KeyError for anything else."""
    directory = builtin_directory(key)
    source = directory / "template.tex.j2"
    if not source.is_file():
        raise KeyError(key)
    return source.read_text()


def builtin_directory(key: str) -> Path:
    """The vendored asset directory for a bundled template.

    Validated against the directory listing rather than trusted, so a template
    key arriving from a request cannot walk out of the templates tree.
    """
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,40}", key or ""):
        raise KeyError(key)
    directory = TEMPLATES_DIR / key
    if not directory.is_dir() or directory.parent != TEMPLATES_DIR:
        raise KeyError(key)
    return directory


# ---------------------------------------------------------------------------
# Compiling
# ---------------------------------------------------------------------------

# Files a template directory may contribute to a compile: the class it needs
# and the fonts it names. Anything else in there (licences, notes, the template
# itself) has no business in the engine's working directory.
_ASSET_SUFFIXES = {".cls", ".sty", ".def", ".fd", ".ttf", ".otf", ".clo"}


def tectonic_binary() -> str | None:
    return shutil.which(os.environ.get("TECTONIC_BIN", "tectonic"))


def _compile_env(cache_dir: str | None, home: Path) -> dict[str, str]:
    """A deliberately tiny environment for the engine subprocess.

    Everything this process holds in os.environ is a secret to somebody: the
    Postgres URL, the Appwrite key, the gateway token. A LaTeX document can
    read files, and `\\input{/proc/self/environ}` is a real technique, so the
    child gets only what Tectonic needs and nothing that is worth stealing.
    """
    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": str(home),
        "TMPDIR": str(home),
    }
    if cache_dir:
        env["TECTONIC_CACHE_DIR"] = cache_dir
    for passthrough in ("SSL_CERT_FILE", "SSL_CERT_DIR"):
        if os.environ.get(passthrough):
            env[passthrough] = os.environ[passthrough]
    return env


def _only_cached() -> bool:
    """Render offline when the image was built with a warm bundle cache.

    Set in the container, unset on a developer machine, where the first compile
    of a template is allowed to reach the bundle and fill the cache.
    """
    return os.environ.get("TECTONIC_ONLY_CACHED", "").strip().lower() in {"1", "true", "yes"}


def _log_tail(text: str, *, limit: int = 4000) -> str:
    """The end of a TeX log, which is where the error is."""
    text = text.strip()
    return text if len(text) <= limit else "...\n" + text[-limit:]


def compile_pdf(
    tex_source: str,
    *,
    assets_dir: Path | None = None,
    timeout: int = COMPILE_TIMEOUT_SECONDS,
) -> bytes:
    """Compile LaTeX to PDF bytes with Tectonic, in a throwaway directory.

    `assets_dir` contributes a bundled template's class file and fonts. Each
    compile gets its own directory: several of these classes write fixed-name
    side files, so a shared working directory would let two renders corrupt
    each other.
    """
    binary = tectonic_binary()
    if binary is None:
        raise TectonicUnavailableError(
            "No tectonic binary on PATH, so this runtime cannot render a PDF. "
            "Rendering runs in the API container, which ships one."
        )

    with tempfile.TemporaryDirectory(prefix="latex-render-") as raw_tmp:
        tmp = Path(raw_tmp)
        work = tmp / "work"
        out = tmp / "out"
        work.mkdir()
        out.mkdir()

        if assets_dir is not None:
            for item in sorted(assets_dir.iterdir()):
                if item.is_dir():
                    shutil.copytree(item, work / item.name)
                elif item.suffix.lower() in _ASSET_SUFFIXES:
                    shutil.copy2(item, work / item.name)

        (work / _TEX_NAME).write_text(tex_source)

        command = [
            binary,
            "-X",
            "compile",
            # A stored or model-written template is untrusted input that is
            # about to be executed. This turns off shell escape and the extra
            # search paths; the bundled templates need neither.
            "--untrusted",
            "--outdir",
            str(out),
            "--keep-logs",
            _TEX_NAME,
        ]
        if _only_cached():
            command.insert(3, "--only-cached")

        try:
            proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
                command,
                cwd=work,
                env=_compile_env(os.environ.get("TECTONIC_CACHE_DIR"), tmp),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise LatexRenderError(
                f"The LaTeX compile did not finish within {timeout} seconds."
            ) from exc

        pdf = out / _PDF_NAME
        if proc.returncode != 0 or not pdf.is_file():
            log_path = out / "resume.log"
            log = log_path.read_text(errors="replace") if log_path.is_file() else ""
            raise LatexRenderError(
                "The LaTeX compile failed.",
                log=_log_tail(log or proc.stderr or proc.stdout),
            )
        return pdf.read_bytes()


def _try_typst(json_resume: dict[str, Any], key: str) -> RenderedPdf | None:
    """Render through Typst, or return None to let Tectonic handle it.

    Returns rather than raises on every "not this way" answer, because none of
    them is the user's problem: a template nobody has ported yet, a catalogue
    entry not yet marked as matching, or an image built without the binary all
    mean the same thing to a caller, which is that the slow path renders it.
    A template that IS ported and DOES fail is a different matter and raises,
    since silently serving a different layout would hide a real regression.
    """
    # Imported here rather than at module scope: typst_render imports this
    # module for the shared date helpers, and at module scope that is a cycle.
    from job_os.services import typst_render
    from job_os.services.latex_catalog import builtin

    try:
        spec = builtin(key)
    except KeyError:
        return None
    if not spec.typst_ready or not typst_render.has_builtin(key):
        return None
    try:
        return typst_render.render_resume_pdf(json_resume, template_key=key)
    except typst_render.TypstUnavailableError:
        return None


def render_engine() -> str:
    """Which engine renders a bundled template: `tectonic` (the default) or `typst`.

    An environment variable rather than a field on a request, because this is a
    deployment decision and not something a caller should be able to ask for.
    `typst` is still per template: it means "use Typst wherever the template has
    been ported and its output has been checked", and anything else falls
    through to Tectonic below.
    """
    return os.environ.get("RENDER_ENGINE", "tectonic").strip().lower()


def render_resume_pdf(
    json_resume: dict[str, Any],
    *,
    template_key: str | None = None,
    latex_source: str | None = None,
) -> RenderedPdf:
    """Render a JSON Resume to PDF through one of the templates.

    `latex_source` renders a stored template, which is how a custom one built
    from an upload is used. `template_key` names a bundled template. Neither
    given falls back to the default, which is a single-column look that parses
    cleanly in applicant tracking systems.

    A bundled template may render through Typst instead of Tectonic, which is
    two orders of magnitude faster. That is opt-in per template and per
    deployment, and it never changes what a caller has to pass: this signature
    is the whole interface, and `resume_engine.py` neither knows nor cares which
    engine produced the bytes.
    """
    from job_os.services.latex_catalog import DEFAULT_TEMPLATE_KEY, builtin

    # A stored template is model-written source, and it stays on Tectonic. The
    # hardening for executing somebody else's markup lives there: --untrusted,
    # the forbidden-command screen, the scrubbed environment. None of that
    # transfers for free, so the fast path is offered only for templates we wrote.
    if latex_source is None and render_engine() == "typst":
        rendered = _try_typst(json_resume, template_key or DEFAULT_TEMPLATE_KEY)
        if rendered is not None:
            return rendered

    model = build_render_model(json_resume)
    if latex_source is not None:
        source = latex_source
        assets: Path | None = None
    else:
        key = template_key or DEFAULT_TEMPLATE_KEY
        try:
            spec = builtin(key)
        except KeyError as exc:
            raise LatexRenderError(f"Unknown resume template: {key!r}.") from exc
        source = load_builtin_source(spec.key)
        assets = builtin_directory(spec.key)

    filled = fill_template(source, model)
    return RenderedPdf(bytes_=compile_pdf(filled, assets_dir=assets))


async def render_resume_pdf_async(
    json_resume: dict[str, Any],
    *,
    template_key: str | None = None,
    latex_source: str | None = None,
) -> RenderedPdf:
    """`render_resume_pdf`, off the event loop.

    Tectonic compilation is a blocking subprocess call that can run several
    seconds. Every caller lives inside an async request handler on a
    single-worker process, so calling the sync function directly serializes
    every other request behind that one render: two "parallel" tailor jobs, or
    one tailor and one unrelated page load, would actually queue one after the
    other rather than making progress together. asyncio.to_thread moves the
    wait onto a worker thread so the event loop stays free for everything else
    while this compiles.
    """
    return await asyncio.to_thread(
        render_resume_pdf,
        json_resume,
        template_key=template_key,
        latex_source=latex_source,
    )
