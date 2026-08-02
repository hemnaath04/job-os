"""Render a JSON Resume document to PDF with Typst, as a fast path beside Tectonic.

This exists for one reason: Tectonic takes about twenty seconds per resume and
Typst takes about a tenth of a second. Nothing else here is a change of opinion
about LaTeX. `latex_render.py` stays the default and stays the fallback, and the
upload-a-design path still compiles through it, because that path executes
model-written source and Tectonic is where the hardening for that lives.

Three pieces, in the order the data moves through them:

1. `sanitize` and friends flatten resume values into plain text. Unlike the
   LaTeX side this does NOT produce markup, and that difference is the point;
   see the note on safety below.
2. `build_render_model` shapes the JSON Resume into the same flat names the
   LaTeX contract uses, field for field, so one template contract describes both
   engines and neither can drift into formatting a date its own way.
3. `compile_pdf` writes that model as `data.json` next to the template and runs
   Typst over it in a scratch directory.

WHY THERE IS NO ESCAPE TABLE HERE. The LaTeX path has to escape, because a
resume value is pasted into the middle of a document that the engine then parses
as instructions: a stray `%` swallows a line and a stray backslash executes. A
Typst template does not paste anything. It reads `data.json` and interpolates the
value in code mode, and a string interpolated in code mode is never re-parsed as
markup. Verified rather than assumed, against Typst 0.15.1: `#set page(width:1pt)`,
`#read("/etc/passwd")`, `*bold*`, `$x^2$` and a lone backslash in resume fields
all render as the literal characters a candidate typed, none of them execute, and
the page geometry is untouched. That is a stronger property than escaping,
because there is no table of ten characters to get wrong. What remains below is
not escaping but cleaning: control characters that would corrupt the JSON, and
newlines that would end a paragraph in the middle of a bullet.

The engine runs offline. Typst reaches the network for exactly one thing, a
`@preview` package import, and 0.15.1 has no flag to forbid it, so `_reject_imports`
refuses any source that contains one. The six bundled templates import nothing.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from job_os.services.latex_render import (
    LatexRenderError,
    RenderedPdf,
    _fmt_date,
    _is_future,
)

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "typst_templates"

# The input Typst reads, the data file it reads alongside it, and the output.
_TYP_NAME = "resume.typ"
_DATA_NAME = "data.json"
_PDF_NAME = "resume.pdf"

# A resume renders in well under a second. This ceiling is for the pathological
# case only: Typst evaluates a real language, and a template with a runaway loop
# should fail the request rather than hold the worker. Far tighter than the LaTeX
# side's ninety seconds because nothing legitimate here comes close.
COMPILE_TIMEOUT_SECONDS = 30


class TypstRenderError(LatexRenderError):
    """A template did not compile. Carries the part of the log that says why.

    Subclasses the LaTeX error deliberately. `routers/resumes.py` turns
    LatexRenderError into a 422 with the compiler's own message, and a render
    failure means the same thing to a caller whichever engine produced it.
    """


class TypstUnavailableError(TypstRenderError):
    """No typst binary on PATH. Callers fall back to Tectonic rather than fail."""


# ---------------------------------------------------------------------------
# Cleaning
# ---------------------------------------------------------------------------

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Zero-width characters that arrive from PDFs and Word documents. They are
# invisible on the page but they break a search in a PDF reader and they confuse
# the applicant tracking systems that read the text layer, so they go.
_STRIP = {"\u200b": "", "\ufeff": ""}


def sanitize(value: Any) -> str:
    """Flatten any value to a single line of plain text.

    Not an escape function, and deliberately not shaped like one. Typst is
    Unicode-native, so the LaTeX side's transliteration table has no equivalent
    here: a candidate who typed a curly apostrophe gets a curly apostrophe,
    because every face this ships with has the glyph. What this does remove is
    the two classes of character that would break something downstream rather
    than merely look wrong.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    text = str(value)
    text = _CONTROL_CHARS.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    for char, replacement in _STRIP.items():
        text = text.replace(char, replacement)
    # A newline inside one field is a soft break, not a paragraph. Left alone it
    # would end the enclosing list item or grid cell, exactly as it would in LaTeX.
    text = text.replace("\n", " ")
    return text.strip()


# Characters that have no business in a URL. Same refusal as the LaTeX side, and
# for the same reason: a link carrying one of these is dropped rather than
# mangled. Typst needs no escaping inside a link target, so this is the whole check.
_URL_FORBIDDEN = set("\\{}^$\"'`<> \t\n")


def safe_url(value: Any) -> str:
    """An http(s) URL fit for `#link`, or "".

    Non-web schemes are dropped for the same reason as on the LaTeX side: a
    resume link is a web link, and `javascript:` or `file:` in a PDF annotation
    is a trap for whoever opens the file.
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
    return text


def link_label(value: Any) -> str:
    """How a URL should read on paper: no scheme, no trailing slash."""
    text = str(value or "").strip()
    text = re.sub(r"^https?://", "", text)
    text = re.sub(r"^www\.", "", text)
    return sanitize(text.rstrip("/"))


def _mailto(email: Any) -> str:
    """A `mailto:` target, or "" if the address is not one."""
    text = str(email or "").strip()
    if not text or any(char in _URL_FORBIDDEN for char in text):
        return ""
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", text):
        return ""
    return f"mailto:{text}"


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------

# The real en dash, where the LaTeX templates write `--` and let TeX make one.
# Typst has no such ligature and every face bundled here has the glyph.
_EN_DASH = "\u2013"


def fmt_date(value: str | date | datetime | None) -> str:
    """JSON Resume dates are YYYY-MM or YYYY. Render them like 'Jul 2024'.

    Shares the LaTeX side's implementation so the two engines cannot format the
    same date differently, and hands it this module's cleaner for the branch
    where the value is not a date at all.
    """
    return _fmt_date(value, escape=sanitize)


def date_range(
    start: Any,
    end: Any,
    *,
    present_label: str = "Present",
    expected_label: str = "expected",
) -> str:
    """A date range, with a real en dash between the ends."""
    left = fmt_date(start)
    right = fmt_date(end)
    if not left and not right:
        return ""
    if not right:
        return f"{left} {_EN_DASH} {present_label}" if left else ""
    if not left:
        return right
    if end and _is_future(end):
        return f"{left} {_EN_DASH} {right} ({expected_label})"
    return f"{left} {_EN_DASH} {right}"


# ---------------------------------------------------------------------------
# The template contract
# ---------------------------------------------------------------------------


def _clean_list(values: Any) -> list[str]:
    if not isinstance(values, (list, tuple)):
        return []
    return [sanitize(v) for v in values if str(v or "").strip()]


def _profiles(basics: dict[str, Any]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for profile in basics.get("profiles") or []:
        if not isinstance(profile, dict):
            continue
        url = safe_url(profile.get("url"))
        network = sanitize(profile.get("network"))
        username = sanitize(profile.get("username"))
        label = link_label(profile.get("url")) or username or network
        if not (url or label):
            continue
        out.append({"network": network, "username": username, "url": url, "label": label})
    return out


def _named_profile(profiles: list[dict[str, str]], network: str) -> dict[str, str] | None:
    for profile in profiles:
        if network.lower() in profile["network"].lower():
            return profile
        if network.lower() in profile["label"].lower():
            return profile
    return None


def build_render_model(json_resume: dict[str, Any]) -> dict[str, Any]:
    """The only names a Typst resume template may use. Every string cleaned.

    Field for field the same contract as `latex_render.build_render_model`, on
    purpose. The two engines have to agree about what a resume looks like as
    data, or the same document renders as two different documents and the
    template picker starts lying to the user.

    A template that finds a name missing here gets a Typst error naming the
    field, which is the same failure the LaTeX side gets from StrictUndefined.
    """
    basics = json_resume.get("basics") or {}
    location = basics.get("location") or {}
    profiles = _profiles(basics)

    city = sanitize(location.get("city"))
    region = sanitize(location.get("region"))
    where = ", ".join(part for part in (city, region) if part)

    work: list[dict[str, Any]] = []
    for item in json_resume.get("work") or []:
        if not isinstance(item, dict):
            continue
        work.append(
            {
                "company": sanitize(item.get("name") or item.get("company")),
                "position": sanitize(item.get("position")),
                "location": sanitize(item.get("location")),
                "url": safe_url(item.get("url")),
                "summary": sanitize(item.get("summary")),
                "dates": date_range(item.get("startDate"), item.get("endDate")),
                "start": fmt_date(item.get("startDate")),
                "end": fmt_date(item.get("endDate")) or "Present",
                "bullets": _clean_list(item.get("highlights")),
            }
        )

    education: list[dict[str, Any]] = []
    for item in json_resume.get("education") or []:
        if not isinstance(item, dict):
            continue
        study = sanitize(item.get("studyType"))
        area = sanitize(item.get("area"))
        degree = ", ".join(part for part in (study, area) if part)
        education.append(
            {
                "institution": sanitize(item.get("institution")),
                "area": area,
                "study_type": study,
                "degree": degree,
                "location": sanitize(item.get("location")),
                "score": sanitize(item.get("score")),
                "dates": date_range(item.get("startDate"), item.get("endDate")),
                "bullets": _clean_list(item.get("courses")),
            }
        )

    projects: list[dict[str, Any]] = []
    for item in json_resume.get("projects") or []:
        if not isinstance(item, dict):
            continue
        keywords = _clean_list(item.get("keywords"))
        projects.append(
            {
                "name": sanitize(item.get("name")),
                "description": sanitize(item.get("description")),
                "url": safe_url(item.get("url")),
                "url_label": link_label(item.get("url")),
                "dates": date_range(item.get("startDate"), item.get("endDate")),
                "keywords": keywords,
                "keywords_line": ", ".join(keywords),
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
                "name": sanitize(item.get("name")),
                "level": sanitize(item.get("level")),
                "keywords": keywords,
                "keywords_line": ", ".join(keywords),
            }
        )

    def simple(section: str, *fields: str) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        for item in json_resume.get(section) or []:
            if not isinstance(item, dict):
                continue
            row = {field: sanitize(item.get(field)) for field in fields}
            if any(row.values()):
                out.append(row)
        return out

    certificates = []
    for item in json_resume.get("certificates") or []:
        if not isinstance(item, dict):
            continue
        certificates.append(
            {
                "name": sanitize(item.get("name")),
                "issuer": sanitize(item.get("issuer")),
                "date": fmt_date(item.get("date")),
                "url": safe_url(item.get("url")),
            }
        )

    # One ordered list so every template writes the same contact line the same
    # way, and so no template has to know how to build a mailto or decide what
    # to do when a link is missing.
    contact: list[dict[str, str]] = []
    if basics.get("phone"):
        contact.append({"kind": "phone", "text": sanitize(basics.get("phone")), "url": ""})
    if basics.get("email"):
        contact.append(
            {
                "kind": "email",
                "text": sanitize(basics.get("email")),
                "url": _mailto(basics.get("email")),
            }
        )
    if safe_url(basics.get("url")):
        contact.append(
            {
                "kind": "website",
                "text": link_label(basics.get("url")),
                "url": safe_url(basics.get("url")),
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
    if where:
        contact.append({"kind": "location", "text": where, "url": ""})

    raw_name = str(basics.get("name") or "").strip()
    first, _, last = raw_name.partition(" ")

    return {
        "name": sanitize(basics.get("name")),
        "first_name": sanitize(first),
        "last_name": sanitize(last),
        "headline": sanitize(basics.get("label")),
        "email": sanitize(basics.get("email")),
        "email_url": _mailto(basics.get("email")),
        "contact": contact,
        "phone": sanitize(basics.get("phone")),
        "website": safe_url(basics.get("url")),
        "website_label": link_label(basics.get("url")),
        "summary": sanitize(basics.get("summary")),
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
# Templates
# ---------------------------------------------------------------------------


def builtin_directory(key: str) -> Path:
    """The asset directory for a bundled Typst template.

    Validated against the directory listing rather than trusted, so a template
    key arriving from a request cannot walk out of the templates tree.
    """
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,40}", key or ""):
        raise KeyError(key)
    directory = TEMPLATES_DIR / key
    if not directory.is_dir() or directory.parent != TEMPLATES_DIR:
        raise KeyError(key)
    return directory


def has_builtin(key: str) -> bool:
    """Whether a template has been ported to Typst yet.

    The port is per template and deliberately incomplete: a template only moves
    once its output has been compared against the LaTeX one and honestly
    matches. Everything else keeps rendering through Tectonic.
    """
    try:
        return (builtin_directory(key) / _TYP_NAME).is_file()
    except KeyError:
        return False


# The one thing in Typst that touches the network. 0.15.1 has no flag to forbid
# it, so it is forbidden here instead. The bundled templates import nothing, and
# a template that wants a package is a template that wants to fetch code at
# render time, on a request a user is waiting for.
_PACKAGE_IMPORT = re.compile(r"@(?:preview|local)\s*/")


def _reject_imports(source: str) -> None:
    if _PACKAGE_IMPORT.search(source):
        raise TypstRenderError(
            "This Typst template imports a package, which would fetch code from "
            "the network at render time. Templates must be self-contained."
        )


# ---------------------------------------------------------------------------
# Compiling
# ---------------------------------------------------------------------------

# Files a template directory may contribute to a compile. Fonts and the template
# itself; everything else in there (licences, attribution notes) has no business
# in the engine's working directory.
_ASSET_SUFFIXES = {".typ", ".ttf", ".otf", ".ttc"}


def typst_binary() -> str | None:
    return shutil.which(os.environ.get("TYPST_BIN", "typst"))


# The discard port on loopback: nothing listens there, so a connection attempt
# is refused immediately rather than hanging until a timeout.
_BLACKHOLE_PROXY = "http://127.0.0.1:9"


def _compile_env(home: Path) -> dict[str, str]:
    """A deliberately tiny environment for the engine subprocess.

    Same reasoning as the Tectonic side: everything this process holds in
    os.environ is a secret to somebody, and the child gets only what it needs.
    Typst reads its own TYPST_* variables for font and package paths, so an
    inherited environment could also redirect where it looks for both. Every
    such path is passed on the command line instead.

    The proxy variables are the network backstop, and they are belt to
    `_reject_imports`'s braces. A `@preview` import is the one thing Typst
    fetches, 0.15.1 has no offline switch, and a guard that reads the source is
    only as good as its regex. Pointing every proxy variable at a closed port
    means that even an import this module failed to spot cannot reach the
    network: it fails closed, in milliseconds, with a connection refused.
    Verified against 0.15.1 rather than assumed.

    NO_PROXY is deliberately absent. Setting it would punch a hole straight
    back through this, which is also why the child gets a built environment
    rather than a filtered copy of the parent's.
    """
    return {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": str(home),
        "TMPDIR": str(home),
        "HTTP_PROXY": _BLACKHOLE_PROXY,
        "HTTPS_PROXY": _BLACKHOLE_PROXY,
        "ALL_PROXY": _BLACKHOLE_PROXY,
        "http_proxy": _BLACKHOLE_PROXY,
        "https_proxy": _BLACKHOLE_PROXY,
        "all_proxy": _BLACKHOLE_PROXY,
    }


def _log_tail(text: str, *, limit: int = 4000) -> str:
    text = text.strip()
    return text if len(text) <= limit else "...\n" + text[-limit:]


def compile_pdf(
    typst_source: str,
    model: dict[str, Any],
    *,
    assets_dir: Path | None = None,
    font_dirs: tuple[Path, ...] = (),
    timeout: int = COMPILE_TIMEOUT_SECONDS,
) -> bytes:
    """Compile a Typst template against a render model, in a throwaway directory.

    The model is written as `data.json` beside the template rather than pasted
    into it, which is what keeps resume content out of the source the engine
    parses. `--root` is the scratch directory, so a template cannot read a file
    outside it: an absolute path resolves inside the root and a `..` path is
    refused outright.
    """
    binary = typst_binary()
    if binary is None:
        raise TypstUnavailableError(
            "No typst binary on PATH, so this runtime cannot render with Typst."
        )
    _reject_imports(typst_source)

    with tempfile.TemporaryDirectory(prefix="typst-render-") as raw_tmp:
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

        (work / _TYP_NAME).write_text(typst_source)
        # ensure_ascii keeps the file byte-safe whatever the container's locale
        # is, and Typst reads the escapes back as the characters they stand for.
        (work / _DATA_NAME).write_text(json.dumps(model, ensure_ascii=True))

        fonts: list[str] = []
        for font_dir in (work, *font_dirs):
            fonts += ["--font-path", str(font_dir)]

        command = [
            binary,
            "compile",
            "--root",
            str(work),
            # No host fonts. A container has none installed and this machine has
            # four hundred, so without this a template silently resolves a face
            # locally that it cannot resolve in production, and the render that
            # was checked is not the render that ships.
            "--ignore-system-fonts",
            *fonts,
            "--package-path",
            str(tmp / "no-packages"),
            "--package-cache-path",
            str(tmp / "no-packages"),
            _TYP_NAME,
            str(out / _PDF_NAME),
        ]

        try:
            proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
                command,
                cwd=work,
                env=_compile_env(tmp),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise TypstRenderError(
                f"The Typst compile did not finish within {timeout} seconds."
            ) from exc

        pdf = out / _PDF_NAME
        if proc.returncode != 0 or not pdf.is_file():
            raise TypstRenderError(
                "The Typst compile failed.",
                log=_log_tail(proc.stderr or proc.stdout),
            )
        return pdf.read_bytes()


# Faces shared by more than one template, vendored once with their licences.
# Underscored so it can never be mistaken for a template key: `builtin_directory`
# only accepts keys matching [a-z0-9][a-z0-9-]*, so this name cannot be reached
# through one.
SHARED_FONTS_DIR = TEMPLATES_DIR / "_fonts"


def _font_dirs(key: str) -> tuple[Path, ...]:
    """Every font directory a template's faces could come from.

    Two of them. The shared directory holds Source Sans Pro, Roboto, Roboto Slab
    and Font Awesome. Deedy's Lato and Raleway were already vendored beside the
    LaTeX template, with the OFL text upstream omits, so they are used from
    there rather than copied: one copy of the fonts, one copy of the licence.

    Handed to every template rather than filtered per template, because renders
    run with `--ignore-system-fonts` and a template resolves faces by name. What
    stops a template picking up a face it did not ask for is that it names the
    one it wants; what stops it silently getting a substitute when that face is
    absent is `missing_fonts`, below, which is asserted in the tests and at
    image build time.
    """
    from job_os.services.latex_render import TEMPLATES_DIR as LATEX_TEMPLATES_DIR

    candidates = (SHARED_FONTS_DIR, LATEX_TEMPLATES_DIR / "deedy" / "fonts")
    return tuple(path for path in candidates if path.is_dir())


# The font families each template names. A missing face does not fail a Typst
# compile: it substitutes silently, and the render comes out looking wrong in a
# way nobody notices until an employer opens it. So this is written down and
# checked, rather than trusted to whatever happens to be installed.
FONT_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "jakes": ("New Computer Modern",),
    "sb2nov": ("New Computer Modern",),
    "deedy": ("Lato", "Raleway"),
    "awesome-cv": ("Source Sans Pro", "Roboto", "Font Awesome 5 Free Solid"),
    "altacv": ("Roboto Slab", "Lato", "Font Awesome 5 Free Solid"),
    "moderncv": (),
}


def available_font_families(key: str) -> set[str]:
    """The families Typst can actually resolve for this template, as it would.

    Asks the binary rather than reading the directory, because what matters is
    the family name inside the file and not the name of the file, and those are
    routinely different: Lato ships here as `Lato-Reg.ttf` and resolves as
    `Lato`.
    """
    binary = typst_binary()
    if binary is None:
        raise TypstUnavailableError("No typst binary on PATH.")
    command = [binary, "fonts", "--ignore-system-fonts"]
    for font_dir in (builtin_directory(key), *_font_dirs(key)):
        command += ["--font-path", str(font_dir)]
    proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
        command, capture_output=True, text=True, timeout=30, check=False
    )
    if proc.returncode != 0:
        raise TypstRenderError("Could not list Typst fonts.", log=proc.stderr)
    return {line.strip() for line in proc.stdout.splitlines() if line.strip()}


def missing_fonts(key: str) -> list[str]:
    """Families this template names that Typst would fail to find, if any."""
    available = available_font_families(key)
    return sorted(set(FONT_REQUIREMENTS.get(key, ())) - available)


def render_resume_pdf(
    json_resume: dict[str, Any],
    *,
    template_key: str,
) -> RenderedPdf:
    """Render a JSON Resume to PDF through one of the ported Typst templates.

    No `latex_source` equivalent and none intended. A stored template is source
    written by a model from somebody's upload, and that path stays on Tectonic,
    where `--untrusted` and the forbidden-command screen already live.
    """
    directory = builtin_directory(template_key)
    source = (directory / _TYP_NAME).read_text()
    model = build_render_model(json_resume)
    return RenderedPdf(
        bytes_=compile_pdf(
            source,
            model,
            assets_dir=directory,
            font_dirs=_font_dirs(template_key),
        )
    )
