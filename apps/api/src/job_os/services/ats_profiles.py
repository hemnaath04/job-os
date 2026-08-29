"""What the six big applicant tracking systems actually reward, per platform.

job.os has always scored a tailored resume one way: the share of the posting's
must-have requirements the document can be shown to claim. That is a good
number, and it stays the number the tailoring loop steers by, because it is the
only one the loop can act on. Missing "Kubernetes" is a fixable fact. But it is
one dimension, and the systems on the other end do not agree that it is the only
one that matters. Taleo puts 35% of its weight on literal keyword overlap and
5% on whether a bullet has a number in it. Lever inverts that almost exactly.
Scoring one resume one way and calling the result "ATS score" hides that.

So this module carries the six platform profiles, the weights and thresholds
they score by, and the detection that decides which one a given posting is
actually going to be read by.

Sources
-------
Two public projects were read for this, both permissively licensed, and both
are credited in ATTRIBUTION.md next to this file:

- sunnypatell/ats-screener (MIT) is where the six-dimension model, the per
  platform weight matrix, the parsing-strictness multipliers, the matching
  strategies and the pass thresholds come from. Its published methodology is
  reproduced here rather than reimplemented from memory.
- srbhr/Resume-Matcher (Apache-2.0) is where the "compare against the posting,
  not against a style guide" framing comes from, which job.os already followed.

Neither project's code is vendored. What is reproduced is the numbers, which
are documentation rather than implementation, and they are kept in one table
here so that a future correction is a one-line edit rather than a hunt.

What is honest about the scores this produces
---------------------------------------------
job.os renders its own PDFs from its own eight templates, so some of what
ats-screener has to infer from an uploaded file, job.os knows exactly. It knows
the column count from the template catalogue. It knows the estimated page
count. It knows the word count. Those deductions are computed.

It also cannot see what it does not render: none of the bundled templates emit
tables or images, so those deductions are recorded as not-applicable rather
than silently scored as clean. `AtsDimensions.unchecked` names them, and the
report says so. A formatting score of 100 here means "none of the issues job.os
can detect are present", not "a parser will love this", and the difference is
worth keeping visible.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal
from urllib.parse import urlsplit

MatchStrategy = Literal["exact", "fuzzy", "semantic"]

# The six dimensions, in the order ats-screener numbers them. Kept as a tuple
# so a profile whose weights do not name all six fails loudly at import rather
# than scoring a missing dimension as zero.
DIMENSIONS: tuple[str, ...] = (
    "formatting",
    "keyword_match",
    "section_completeness",
    "experience_relevance",
    "education_match",
    "quantification",
)


@dataclass(frozen=True, slots=True)
class AtsProfile:
    """One applicant tracking system's scoring behaviour.

    `weights` sums to 1.0 across the six dimensions. `strictness` scales the
    formatting deductions: the same two-column layout costs 13.5 points on
    Workday and 5.25 on Lever, which is most of why the same resume scores so
    differently on the two.
    """

    key: str
    name: str
    weights: dict[str, float]
    strictness: float
    matching: MatchStrategy
    pass_threshold: int
    auto_rejects: bool
    # Hosts that identify the platform from a posting URL. Suffix-matched, so
    # "myworkdayjobs.com" catches "nvidia.wd5.myworkdayjobs.com".
    hosts: tuple[str, ...] = field(default_factory=tuple)
    # One sentence handed to the writer. This is the tailoring half of the
    # feature: it says what to do differently, not what the platform is.
    guidance: str = ""

    def __post_init__(self) -> None:
        missing = set(DIMENSIONS) - set(self.weights)
        if missing:  # pragma: no cover - a definition error, caught at import
            raise ValueError(f"{self.key} is missing weights for {sorted(missing)}")
        total = round(sum(self.weights.values()), 6)
        if total != 1.0:  # pragma: no cover - a definition error, caught at import
            raise ValueError(f"{self.key} weights sum to {total}, not 1.0")


# ---------------------------------------------------------------------------
# The six platforms
# ---------------------------------------------------------------------------

WORKDAY = AtsProfile(
    key="workday",
    name="Workday",
    weights={
        "formatting": 0.25,
        "keyword_match": 0.30,
        "section_completeness": 0.15,
        "experience_relevance": 0.15,
        "education_match": 0.10,
        "quantification": 0.05,
    },
    strictness=0.90,
    matching="exact",
    pass_threshold=70,
    auto_rejects=True,
    hosts=("myworkdayjobs.com", "myworkdaysite.com", "workday.com"),
    guidance=(
        "This posting is hosted on Workday, which matches keywords literally and "
        "is the strictest parser of the six. Use the posting's exact wording for "
        "every tool and skill rather than a synonym or an abbreviation: write "
        "both forms where the posting uses one and the resume uses the other. "
        "Keep section headings standard (Experience, Education, Skills, "
        "Projects)."
    ),
)

TALEO = AtsProfile(
    key="taleo",
    name="Taleo (Oracle)",
    weights={
        "formatting": 0.20,
        "keyword_match": 0.35,
        "section_completeness": 0.15,
        "experience_relevance": 0.15,
        "education_match": 0.10,
        "quantification": 0.05,
    },
    strictness=0.85,
    matching="exact",
    pass_threshold=75,
    auto_rejects=True,
    hosts=("taleo.net", "oraclecloud.com"),
    guidance=(
        "This posting is on Taleo, which puts more weight on literal keyword "
        "overlap than any other system and auto-ranks against a visible cutoff. "
        "Exact terms from the posting matter more here than anywhere else: a "
        "synonym scores nothing. Prefer the posting's own phrasing over a "
        "better-sounding paraphrase."
    ),
)

ICIMS = AtsProfile(
    key="icims",
    name="iCIMS",
    weights={
        "formatting": 0.15,
        "keyword_match": 0.30,
        "section_completeness": 0.15,
        "experience_relevance": 0.20,
        "education_match": 0.10,
        "quantification": 0.10,
    },
    strictness=0.60,
    matching="fuzzy",
    pass_threshold=60,
    auto_rejects=False,
    hosts=("icims.com",),
    guidance=(
        "This posting is on iCIMS, whose parser resolves synonyms and canonical "
        "forms, so an abbreviation is credited. Its screening is advisory and a "
        "human decides, so weight a specific, well-evidenced bullet over "
        "repeating a term the resume already carries."
    ),
)

GREENHOUSE = AtsProfile(
    key="greenhouse",
    name="Greenhouse",
    weights={
        "formatting": 0.10,
        "keyword_match": 0.25,
        "section_completeness": 0.10,
        "experience_relevance": 0.25,
        "education_match": 0.10,
        "quantification": 0.20,
    },
    strictness=0.40,
    matching="semantic",
    pass_threshold=50,
    auto_rejects=False,
    hosts=("greenhouse.io",),
    guidance=(
        "This posting is on Greenhouse, which does no automated filtering: a "
        "person reads the page. Nearly half its weight is on the quality of the "
        "experience bullets and whether they carry numbers, so spend the page on "
        "quantified, specific achievements rather than on keyword coverage."
    ),
)

LEVER = AtsProfile(
    key="lever",
    name="Lever",
    weights={
        "formatting": 0.08,
        "keyword_match": 0.22,
        "section_completeness": 0.10,
        "experience_relevance": 0.30,
        "education_match": 0.10,
        "quantification": 0.20,
    },
    strictness=0.35,
    matching="semantic",
    pass_threshold=50,
    auto_rejects=False,
    hosts=("lever.co",),
    guidance=(
        "This posting is on Lever, which does no automated screening at all and "
        "weights the experience section higher than any other platform. Write "
        "for the person reading it: concrete outcomes with numbers, strongest "
        "and most relevant work first."
    ),
)

SUCCESSFACTORS = AtsProfile(
    key="successfactors",
    name="SuccessFactors (SAP)",
    weights={
        "formatting": 0.25,
        "keyword_match": 0.25,
        "section_completeness": 0.20,
        "experience_relevance": 0.15,
        "education_match": 0.10,
        "quantification": 0.05,
    },
    strictness=0.85,
    matching="exact",
    pass_threshold=65,
    auto_rejects=False,
    hosts=("successfactors.com", "successfactors.eu", "sapsf.com", "sapsf.eu"),
    guidance=(
        "This posting is on SuccessFactors, whose Textkernel parser routes "
        "content by detecting standard section headings and weights section "
        "structure higher than any other platform. Keep headings conventional "
        "and match the posting's literal terms; a creative heading can cost the "
        "whole section."
    ),
)

# The fallback, and job.os's own opinion rather than a vendor's. Weights are
# the unweighted mean of the six, which is the honest thing to use when the
# platform is unknown: it is what every platform agrees on, in the proportion
# they collectively agree on it. Threshold 60 is the median of the six.
GENERIC = AtsProfile(
    key="generic",
    name="Unknown or direct posting",
    weights={
        "formatting": 0.17,
        "keyword_match": 0.28,
        "section_completeness": 0.14,
        "experience_relevance": 0.20,
        "education_match": 0.10,
        "quantification": 0.11,
    },
    strictness=0.65,
    matching="fuzzy",
    pass_threshold=60,
    auto_rejects=False,
    hosts=(),
    guidance=(
        "This posting is not on one of the applicant tracking systems job.os "
        "models, so no platform-specific rule applies. Write for a clean parse "
        "and a human reader both: the posting's own terms for skills, standard "
        "section headings, and quantified bullets."
    ),
)

PROFILES: tuple[AtsProfile, ...] = (
    WORKDAY,
    TALEO,
    ICIMS,
    GREENHOUSE,
    LEVER,
    SUCCESSFACTORS,
)

_BY_KEY = {profile.key: profile for profile in (*PROFILES, GENERIC)}


def profile(key: str | None) -> AtsProfile:
    """Look a profile up by key, falling back rather than raising.

    A stored run can name a platform this build no longer defines, and a resume
    is worth scoring in the generic profile rather than failing over it.
    """
    if not key:
        return GENERIC
    return _BY_KEY.get(key, GENERIC)


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def detect(url: str | None) -> AtsProfile:
    """Which system will read a resume submitted to this posting.

    Host-suffix matching only. A posting's body often names the vendor in a
    footer or a cookie banner, and reading that was tried and dropped: a
    Greenhouse-hosted board that embeds a Workday link for a different team
    detected as Workday and got the wrong advice. The host a candidate actually
    submits to is the one that parses the file, and it is the only signal here
    that cannot be wrong for that reason.
    """
    host = _host(url)
    if not host:
        return GENERIC
    for candidate in PROFILES:
        if any(host == vendor or host.endswith("." + vendor) for vendor in candidate.hosts):
            return candidate
    return GENERIC


def _host(url: str | None) -> str:
    if not url:
        return ""
    text = url.strip()
    if not text:
        return ""
    # A stored source_url is not always absolute, and urlsplit reads a bare
    # "boards.greenhouse.io/acme" as a path with no host at all.
    if "://" not in text:
        text = "https://" + text
    try:
        host = urlsplit(text).hostname or ""
    except ValueError:
        return ""
    return host.casefold()


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

# Deductions, from ats-screener's published formatting model. Only the four
# job.os can actually determine from a document it rendered itself are listed;
# the rest are reported as unchecked.
_MULTI_COLUMN_PENALTY = 15.0
_LONG_DOCUMENT_PENALTY = 5.0
_TOO_SHORT_PENALTY = 10.0
_TOO_LONG_PENALTY = 3.0

# What job.os does not inspect, and says so rather than scoring as clean.
_UNCHECKED = (
    "tables",
    "images",
    "special character density",
    "all-caps lines",
    "bullet style consistency",
)

_QUANTIFIED_RE = re.compile(
    r"""
    \d                      # any digit at all is the base signal
    """,
    re.VERBOSE,
)

# "Responsible for" and friends. A bullet opening with one of these is the
# canonical low-scoring shape in every ATS guide, ats-screener's included.
_WEAK_OPENERS = (
    "responsible for",
    "worked on",
    "helped with",
    "assisted with",
    "involved in",
    "tasked with",
    "duties included",
)


@dataclass(frozen=True, slots=True)
class AtsDimensions:
    """The six raw dimension scores, before the platform's weights apply."""

    formatting: float
    keyword_match: float
    section_completeness: float
    experience_relevance: float
    education_match: float
    quantification: float
    # Human-readable notes about what drove each number, for the report.
    notes: tuple[str, ...] = field(default_factory=tuple)
    unchecked: tuple[str, ...] = _UNCHECKED

    def as_dict(self) -> dict[str, float]:
        return {name: getattr(self, name) for name in DIMENSIONS}


def _bullets(document: dict[str, Any]) -> list[str]:
    """Every experience and project highlight in the document, as text."""
    out: list[str] = []
    for section in ("work", "projects", "volunteer"):
        for entry in document.get(section) or []:
            if not isinstance(entry, dict):
                continue
            for highlight in entry.get("highlights") or []:
                if isinstance(highlight, str) and highlight.strip():
                    out.append(highlight.strip())
    return out


def _word_count(document: dict[str, Any]) -> int:
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
    return len(" ".join(parts).split())


def score_formatting(
    *,
    columns: int,
    page_count: int,
    word_count: int,
    strictness: float,
) -> tuple[float, list[str]]:
    """D1, deduction-based, scaled by the platform's parsing strictness.

    `columns` comes from the template catalogue rather than from inspecting the
    PDF, which is the one place job.os has better information than a screener
    that only sees the file.
    """
    notes: list[str] = []
    penalty = 0.0
    if columns >= 2:
        penalty += _MULTI_COLUMN_PENALTY
        notes.append(
            f"Two-column template, {_MULTI_COLUMN_PENALTY * strictness:.1f} points "
            "at this platform's strictness."
        )
    if page_count > 2:
        penalty += _LONG_DOCUMENT_PENALTY
        notes.append(f"{page_count} pages; over two risks truncation.")
    if word_count < 150:
        penalty += _TOO_SHORT_PENALTY
        notes.append(f"Only {word_count} words; reads as a parsing failure.")
    elif word_count > 1500:
        penalty += _TOO_LONG_PENALTY
        notes.append(f"{word_count} words is long enough to be worth trimming.")
    return max(0.0, 100.0 - penalty * strictness), notes


def score_sections(document: dict[str, Any]) -> tuple[float, list[str]]:
    """D3. Four required sections, five bonus ones.

    Scored as required coverage out of 100 with the bonus sections able to add
    back what a missing required one costs, which is how ats-screener describes
    it: bonus sections "improve the score" rather than being separately graded.
    """
    basics = document.get("basics") or {}
    required = {
        "contact": bool(basics.get("name") and basics.get("email")),
        "experience": bool(document.get("work")),
        "education": bool(document.get("education")),
        "skills": bool(document.get("skills")),
    }
    bonus = {
        "summary": bool((basics.get("summary") or "").strip()),
        "certifications": bool(document.get("certificates")),
        "projects": bool(document.get("projects")),
        "publications": bool(document.get("publications")),
        "volunteer": bool(document.get("volunteer")),
    }
    met = sum(required.values())
    score = met / len(required) * 100.0
    score = min(100.0, score + sum(bonus.values()) * 2.0)
    notes: list[str] = []
    absent = [name for name, present in required.items() if not present]
    if absent:
        notes.append("Missing required section: " + ", ".join(sorted(absent)) + ".")
    return score, notes


def score_experience(bullets: list[str]) -> tuple[float, list[str]]:
    """D4. Bullet quality: quantification, and the absence of weak openers.

    Deliberately not a model call. This runs inside the tailoring loop where a
    round trip costs seconds, and the two things it measures are the two an ATS
    guide would measure without one.
    """
    if not bullets:
        return 0.0, ["No experience or project bullets in the document."]
    quantified = sum(1 for line in bullets if _QUANTIFIED_RE.search(line))
    weak = [
        line
        for line in bullets
        if line.casefold().lstrip().startswith(_WEAK_OPENERS)
    ]
    score = 100.0
    score -= (1 - quantified / len(bullets)) * 40.0
    score -= len(weak) / len(bullets) * 30.0
    notes: list[str] = []
    if weak:
        notes.append(
            f"{len(weak)} of {len(bullets)} bullets open passively "
            f'(for example "{weak[0][:60]}").'
        )
    return max(0.0, score), notes


def score_education(document: dict[str, Any]) -> tuple[float, list[str]]:
    """D5. A baseline check, weighted 0.10 everywhere, so it is scored simply."""
    entries = [e for e in (document.get("education") or []) if isinstance(e, dict)]
    if not entries:
        return 0.0, ["No education section."]
    scored = 0.0
    for entry in entries:
        points = 0.0
        if entry.get("studyType"):
            points += 40.0
        if entry.get("area"):
            points += 20.0
        if entry.get("institution"):
            points += 20.0
        if entry.get("endDate") or entry.get("startDate"):
            points += 20.0
        scored = max(scored, points)
    notes: list[str] = []
    if scored < 100.0:
        notes.append("Education entry is missing a degree, field, school or date.")
    return scored, notes


def score_quantification(bullets: list[str]) -> tuple[float, list[str]]:
    """D6. The plain ratio, which is what ats-screener defines it as."""
    if not bullets:
        return 0.0, []
    quantified = sum(1 for line in bullets if _QUANTIFIED_RE.search(line))
    ratio = quantified / len(bullets) * 100.0
    notes = [f"{quantified} of {len(bullets)} bullets carry a number."]
    return float(int(ratio)), notes


def weighted_keyword_score(
    *,
    matched: list[str],
    missing: list[str],
    jd_text: str,
) -> tuple[float, dict[str, Any]]:
    """Keyword coverage weighted by how often the posting repeats each term.

    The plain coverage number treats every must-have as equally important, so a
    resume that names eight incidental tools and misses the one the posting
    repeats six times outscores the reverse. Ranking systems do not work that
    way: term frequency in the posting is the oldest signal in the field, and it
    is what Taleo's and Workday's exact-match indexes are built on.

    This is the term-frequency half of BM25 and not BM25 itself. There is no
    document corpus here to compute an inverse document frequency against: one
    posting is not a corpus, and inventing one would be dressing a guess up as
    information retrieval. `1 + log(count)` is the saturation BM25 uses, which
    is the part that actually matters, and it stops a posting that says
    "Python" nine times from making every other requirement worthless.
    """
    haystack = (jd_text or "").casefold()

    def weight(label: str) -> float:
        term = label.casefold().strip()
        if not term:
            return 1.0
        count = haystack.count(term)
        return 1.0 + math.log(count) if count > 1 else 1.0

    met = {label: weight(label) for label in matched}
    gap = {label: weight(label) for label in missing}
    total = sum(met.values()) + sum(gap.values())
    if total <= 0:
        return 0.0, {"weighted": False, "reason": "no requirements to weight"}
    score = sum(met.values()) / total * 100.0
    # The requirements the posting leans on hardest that the resume does not
    # claim. This is the actionable half: it ranks the gap by what it costs.
    costly = sorted(gap.items(), key=lambda pair: pair[1], reverse=True)[:5]
    return round(score, 1), {
        "weighted": True,
        "most_repeated_gaps": [label for label, _w in costly if _w > 1.0],
        "weights": {label: round(w, 2) for label, w in {**met, **gap}.items() if w > 1.0},
    }


def evaluate(
    *,
    document: dict[str, Any],
    keyword_score: float,
    columns: int,
    page_count: int,
    ats: AtsProfile,
) -> tuple[Decimal, AtsDimensions, dict[str, Any]]:
    """Score a document the way one platform would, and report the parts.

    `keyword_score` is passed in rather than computed here: it is job.os's
    existing requirement-coverage number, which already does word-boundary
    matching against the posting's parsed must-haves. Recomputing it with a
    second, worse matcher would produce two numbers that disagree.
    """
    bullets = _bullets(document)
    words = _word_count(document)

    formatting, format_notes = score_formatting(
        columns=columns,
        page_count=page_count,
        word_count=words,
        strictness=ats.strictness,
    )
    sections, section_notes = score_sections(document)
    experience, experience_notes = score_experience(bullets)
    education, education_notes = score_education(document)
    quantification, quant_notes = score_quantification(bullets)

    dimensions = AtsDimensions(
        formatting=round(formatting, 1),
        keyword_match=round(float(keyword_score), 1),
        section_completeness=round(sections, 1),
        experience_relevance=round(experience, 1),
        education_match=round(education, 1),
        quantification=round(quantification, 1),
        notes=tuple(
            format_notes + section_notes + experience_notes + education_notes + quant_notes
        ),
    )

    raw = dimensions.as_dict()
    composite = sum(raw[name] * ats.weights[name] for name in DIMENSIONS)
    score = Decimal(str(round(composite, 1)))

    report = {
        "platform": ats.key,
        "platform_name": ats.name,
        "matching": ats.matching,
        "composite_score": float(score),
        "pass_threshold": ats.pass_threshold,
        "passes": float(score) >= ats.pass_threshold,
        "auto_rejects": ats.auto_rejects,
        "weights": dict(ats.weights),
        "dimensions": raw,
        "notes": list(dimensions.notes),
        "not_checked": list(dimensions.unchecked),
        "word_count": words,
        "bullet_count": len(bullets),
    }
    return score, dimensions, report
