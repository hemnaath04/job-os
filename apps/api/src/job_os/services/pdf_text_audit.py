"""Audit the text layer a rendered resume looks like to an applicant tracking system.

An ATS never sees the page. It sees the string the PDF's text layer yields, and a
resume that looks immaculate at 100% zoom can hand that parser garbage. This module
reads the layer the machine reads and reports what is wrong with it.

The primary check is coverage, and it is deliberately the general one: what
fraction of the words the candidate actually wrote can still be found, as whole
words, in the extracted text. That question is engine-agnostic. It knows nothing
about LaTeX or Typst or any renderer we have not adopted yet, it just asks whether
the candidate's own vocabulary survived the trip to the page, which is the only
thing keyword matching depends on. A layer that fails this is unreadable whatever
broke it.

Three specific artifact patterns run alongside it as corroboration. They are
Tectonic-shaped, so they are secondary by design:

1. Raw LaTeX macros survive the render. `altacv` puts the literal strings
   `\\faGlobe`, `\\faLinkedin` and `\\faGithub` in front of the contact details.
2. Small caps decompose. `awesome-cv` sets headings in small caps and the
   lowercase `i` glyph keeps its lowercase codepoint, so "Computer Science"
   reaches the parser as `COMPUTERSCiENCE`.
3. Word spacing is lost, joining whole clauses into single tokens
   (`EPAMSystems`, `triageddailyfailures`).

WHY BOTH, when coverage is the better signal. Coverage does not subsume the
patterns, and the measurements say so plainly. Rendering the bundled sample
through all six templates and both engines, coverage runs:

    altacv       26.0% Tectonic   97.6% Typst
    awesome-cv   83.5% Tectonic  100.0% Typst
    jakes        85.0% Tectonic   85.0% Typst
    sb2nov       85.0% Tectonic   85.0% Typst
    deedy        97.6% Tectonic   96.9% Typst
    moderncv     98.4% Tectonic  100.0% Typst

Coverage catches AltaCV's collapse with an enormous margin. It cannot catch
Awesome-CV, because a clean render does not score 100 either: Jake's legitimately
omits the languages section, one project and the awards, so its honest floor is
85.0%, and Awesome-CV's damaged render scores 83.5%. Those are 1.5 points apart,
which is not a margin anybody should build a threshold on. So coverage is set
where only a collapse trips it, and the patterns keep catching the subtler damage
they were written for.

ON `pdf_type`, AND WHY IT IS NOT A GATE. ModernCV under Tectonic classifies as
`mixed`, not `text_based`, because page two draws some text as vector paths. Its
coverage is 98.4% with no artifacts, so the words do reach the parser and the
resume is fine; the classification is describing how the page was drawn, not
whether it can be read. ModernCV is also the one bundled template with no Typst
port, so it is what a real user on Tectonic gets today, and gating on
`is_text_based` would have flagged every one of those resumes for nothing. Only
`scanned` and `image_based` count as unreadable here, and a PDF that truly is one
of those collapses on coverage anyway.

`pdf-inspector` (https://github.com/firecrawl/pdf-inspector, MIT) supplies the
input: it classifies the document and extracts the text in reading order, in
single-digit milliseconds. Note what it does NOT do, because the split matters and
the credit should be accurate: its own `has_encoding_issues` flag reads False on
every one of the twelve renders this was calibrated against, including both known
bad ones. It is not tuned for these defects. The library gives us a trustworthy
text layer and a type classification; the judgements below are ours.

Everything here is a pure function, and nothing here raises. A resume render must
not fail because an audit of it failed; see `audit_pdf_text`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import structlog

log = structlog.get_logger(__name__)

# A backslash followed by letters, which is a LaTeX control sequence that escaped
# into the text layer. Threshold is one: a well-formed render contains no
# backslash at all. Measured across the six Typst renders, every one of them has
# a backslash count of exactly zero, so this fires on a defect and nothing else.
_LATEX_MACRO = re.compile(r"\\[a-zA-Z]{2,}")

# Two or more capitals, one lowercase, two or more capitals: the shape small caps
# collapse into. Catches SCiENCE, CANDiDATE, PYDANTiC. The two-capital runs on
# both sides are what keeps ordinary mixed-case words out, including the ones a
# resume genuinely contains: SQLite, IoT, MySQL and eBay all fail to match.
_SMALL_CAPS_MANGLED = re.compile(r"[A-Z]{2,}[a-z][A-Z]{2,}")

# An unbroken letter run long enough that it is almost certainly several words
# with the spaces dropped. Longest legitimate single words in these resumes run
# to about fifteen characters, so eighteen leaves headroom.
_RUN_ON_WORD = re.compile(r"\b[A-Za-z]{18,}\b")

# Joined words are the noisiest of the three patterns, because two columns sitting
# side by side can legitimately abut once ("SololearnBEDROCKED") without the layer
# being broken. So this one needs both a floor and a rate before it counts.
# Calibration across twelve renders: clean ones peak at 1 occurrence and 0.12% of
# words, broken ones start at 27 occurrences and 3.8%.
_RUN_ON_MIN_COUNT = 3
_RUN_ON_MIN_SHARE = 0.01

# The coverage floor. Set from the measurements in the module docstring: the
# lowest HONEST coverage any bundled template produces is 83.5%, because templates
# legitimately omit sections, and the one collapsed render scores 26%. Seventy
# percent sits thirteen points below the honest floor and forty-four above the
# collapse, so it takes real damage to trip and template pruning never does.
_MIN_COVERAGE = 0.70

# Below this many distinct source words the ratio is too jumpy to mean anything,
# so coverage is simply not measured. A near-empty draft is the review's problem,
# not the renderer's.
_MIN_SOURCE_TOKENS = 40

# Words shorter than this are dropped from the coverage vocabulary. Short tokens
# collide with fragments of longer ones and with template chrome, which makes the
# ratio noisy without making it more sensitive.
_MIN_TOKEN_LENGTH = 4

# Keeps `+`, `#`, `.` and `-` inside a token so C++, C#, .NET and CI/CD survive
# tokenisation as themselves rather than as fragments.
_TOKEN = re.compile(r"[A-Za-z][A-Za-z+#./-]*")

# Emails and URLs are dropped before tokenising: a template may render them as a
# link, an icon or not at all, and none of that is a text-layer defect.
_CONTACT_NOISE = re.compile(r"\S+@\S+|https?://\S+|www\.\S+")

# How many offending samples to carry back for the reviewer message. Enough to
# make the flag actionable, few enough to keep the issue readable.
_MAX_SAMPLES = 4

# The classifications that mean there is no text layer worth reading. `mixed` is
# deliberately absent; see the note on `pdf_type` in the module docstring.
_UNREADABLE_TYPES = frozenset({"scanned", "image_based"})


@dataclass(frozen=True, slots=True)
class PdfTextAudit:
    """What the text layer of one rendered PDF looks like to a parser.

    `available` is False when the audit could not run at all. That is the neutral
    result: every other field then reads as though nothing was wrong, so a caller
    that ignores `available` degrades to trusting the render, never to failing it.

    `coverage` is None when it was not measured, which is not the same as zero.

    `is_text_based` reports the classification faithfully but is NOT what decides
    `clean`; `text_layer_unreadable` is. The two differ on `mixed`, which is a
    statement about how a page was drawn rather than about whether it can be read.
    """

    is_text_based: bool
    encoding_issue_flagged: bool
    text: str
    coverage: float | None = None
    coverage_shortfall: bool = False
    text_layer_unreadable: bool = False
    artifacts: tuple[str, ...] = ()
    pdf_type: str = "unknown"
    available: bool = True

    @property
    def clean(self) -> bool:
        return not (
            self.text_layer_unreadable
            or self.encoding_issue_flagged
            or self.coverage_shortfall
        )


_NEUTRAL = PdfTextAudit(
    is_text_based=True,
    encoding_issue_flagged=False,
    text="",
    pdf_type="unknown",
    available=False,
)


def source_vocabulary(document: dict[str, Any]) -> set[str]:
    """The distinct words the candidate wrote, from the values of a JSON Resume.

    Values only, never the schema key names, for the same reason the job-match
    coverage scores values only: "summary", "location" and "keywords" are words
    the schema contributes and the candidate did not, and counting them would let
    a broken render look better than it is by matching its own template chrome.
    """
    parts: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(document)
    text = _CONTACT_NOISE.sub(" ", " ".join(parts))
    vocabulary: set[str] = set()
    for raw in _TOKEN.findall(text):
        token = raw.strip("./-").casefold()
        if len(token) >= _MIN_TOKEN_LENGTH:
            vocabulary.add(token)
    return vocabulary


def _mentions(haystack: str, term: str) -> bool:
    """Whether `haystack` names `term` as a word, not merely as a substring.

    Word boundaries are hand-rolled rather than `\\b` because the terms include
    C++, CI/CD and .NET, where the edge character is not a word character and
    `\\b` lands in the wrong place. This is the same rule the job-match coverage
    uses, deliberately: the audit should measure what the product's own keyword
    matching would find, not something adjacent to it.
    """
    return bool(re.search(rf"(?<!\w){re.escape(term)}(?!\w)", haystack))


def _measure_coverage(text: str, document: dict[str, Any]) -> float | None:
    """Fraction of the candidate's own words still findable in the text layer."""
    vocabulary = source_vocabulary(document)
    if len(vocabulary) < _MIN_SOURCE_TOKENS:
        return None
    haystack = text.casefold()
    found = sum(1 for token in vocabulary if _mentions(haystack, token))
    return found / len(vocabulary)


def _find_artifacts(text: str) -> tuple[str, ...]:
    """Name the ATS-hostile patterns present in an extracted text layer."""
    artifacts: list[str] = []

    macros = _LATEX_MACRO.findall(text)
    if macros:
        sample = ", ".join(sorted(set(macros))[:_MAX_SAMPLES])
        artifacts.append(f"raw LaTeX commands in the text layer ({sample})")

    mangled = _SMALL_CAPS_MANGLED.findall(text)
    if mangled:
        sample = ", ".join(sorted(set(mangled))[:_MAX_SAMPLES])
        artifacts.append(f"small caps broken into mixed case ({sample})")

    words = text.split()
    run_ons = _RUN_ON_WORD.findall(text)
    if words and len(run_ons) >= _RUN_ON_MIN_COUNT:
        share = len(run_ons) / len(words)
        if share >= _RUN_ON_MIN_SHARE:
            sample = ", ".join(sorted(set(run_ons), key=len, reverse=True)[:2])
            artifacts.append(
                f"{len(run_ons)} words joined together without spaces ({sample})"
            )

    return tuple(artifacts)


def audit_pdf_text(
    pdf_bytes: bytes,
    *,
    source_document: dict[str, Any] | None = None,
) -> PdfTextAudit:
    """Read the text layer of `pdf_bytes` and report whether an ATS could parse it.

    Pass `source_document`, the JSON Resume the PDF was rendered from, to enable
    the coverage check. Without it only the artifact patterns run, which is a
    weaker audit but still a valid one.

    Never raises. A resume the user is waiting on must not fail to render because
    the audit of it fell over, so anything unexpected returns the neutral result
    and is logged. The caller cannot tell an audit that found nothing wrong from
    one that could not run unless it checks `available`, which is deliberate:
    the safe reading of both is "do not flag this resume".
    """
    if not pdf_bytes:
        return _NEUTRAL

    try:
        import pdf_inspector

        result = pdf_inspector.process_pdf_bytes(pdf_bytes)
        pdf_type = str(result.pdf_type)
        text = result.markdown or ""
        library_flagged = bool(result.has_encoding_issues)
    except Exception as exc:  # noqa: BLE001 - an audit failure must not fail a render
        log.warning("pdf_text_audit_failed", error=str(exc))
        return _NEUTRAL

    coverage: float | None = None
    if source_document:
        try:
            coverage = _measure_coverage(text, source_document)
        except Exception as exc:  # noqa: BLE001 - same reasoning as above
            log.warning("pdf_text_coverage_failed", error=str(exc))

    shortfall = coverage is not None and coverage < _MIN_COVERAGE
    unreadable = pdf_type in _UNREADABLE_TYPES
    artifacts = _find_artifacts(text)

    # Coverage leads the list when it fires, because it is the finding that
    # explains the others rather than the other way round.
    reasons: list[str] = []
    if unreadable:
        reasons.append(f"the PDF reads as {pdf_type} rather than text")
    if shortfall and coverage is not None:
        reasons.append(
            f"only {coverage:.0%} of the resume's own words survive into the text "
            "layer an ATS reads"
        )
    reasons.extend(artifacts)

    return PdfTextAudit(
        is_text_based=pdf_type == "text_based",
        encoding_issue_flagged=library_flagged or bool(artifacts),
        text=text,
        coverage=coverage,
        coverage_shortfall=shortfall,
        text_layer_unreadable=unreadable,
        artifacts=tuple(reasons),
        pdf_type=pdf_type,
        available=True,
    )
