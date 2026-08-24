"""Verified resume editing, review, and export support.

JSON Resume is the canonical editable document. PDF and LaTeX are generated
artifacts. Every AI revision is followed by a separate quality-model review
plus deterministic PDF checks before it can be finalized.
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import re
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import Annotated, Any

import anthropic
import httpx
import structlog
from pydantic import BaseModel, BeforeValidator, Field, ValidationError
from pypdf import PdfReader

from job_os.schemas.resumes import ResumeReviewIssue, ResumeReviewResult
from job_os.services.career_ops_rules import CAREER_OPS_RULES, KNOWN_GITHUB_REPOS
from job_os.services.latex_catalog import builtin
from job_os.services.latex_render import (
    TectonicUnavailableError,
    date_range as shared_date_range,
    render_resume_pdf_async,
)
from job_os.services.llm_json import (
    EMPTY_REPLY_RETRY,
    JSON_ONLY_RETRY,
    create_message,
    parse_model_json,
    response_diagnostics,
    response_text,
)
from job_os.services.pdf_text_audit import audit_pdf_text
from job_os.services.resume_writing import (
    BANNED_WORDING,
    document_quality_flags,
    has_banned_separator,
)
from job_os.settings import get_settings

log = structlog.get_logger(__name__)

# A resume a person would actually send should be able to clear this. At 90 it
# could not: two ordinary warnings cost 10 points outright, so the bar demanded a
# document with essentially nothing to say about it, and every real resume sat
# permanently at needs_changes. Blocking issues still fail regardless of score,
# so this threshold governs polish, not correctness.
PASS_SCORE = Decimal("75")

# Room for a full issue list, and for the thinking block the gateway's model emits
# before it, since max_tokens covers both. At 3000 a thorough review ran out of
# tokens mid-JSON and silently cost the model half of the score; at 6000, a review
# that could see the verified facts, and so had more to check, came back with no
# text at all; the tailor loop then hit the same wall at 16000 with the whole
# budget spent on thinking.
REVIEW_MAX_TOKENS = 24000
REVIEW_RETRY_MAX_TOKENS = 32000

# A conversational edit returns the WHOLE resume, not a patch, so the reply is a
# full document plus the model's thinking. At 7000 it truncated, which forced a
# second full call, and the two together are why an edit took two and a half
# minutes to come back. One call that fits is the whole fix.
REVISE_MAX_TOKENS = 32000

# What to say when an edit came back as prose with no document in it at all.
# Deliberately does NOT hand the model its own prose back: a retry that quotes a
# chatty reply as an assistant turn establishes prose as the format of this
# conversation, and the two production retries that did so answered in prose
# again and lost the edit. This restates the shape and asks for a short answer,
# because a second full document generation is what makes the wait double.
REVISION_FORMAT_RETRY = (
    "That reply was prose, so it could not be applied. Answer again as one raw "
    "JSON object and nothing else: no prose before or after it, no markdown "
    "fences, no headings. The object has exactly these keys: assistant_message "
    "(a string, where anything you want to say to the user goes), suggestions "
    "(a list of short strings), and json_resume (the COMPLETE edited resume, "
    "every section carried over, not a patch). Keep assistant_message to two or "
    "three sentences so the answer fits in one reply."
)

# The bundled template that fits the most content on one page, named in the
# page-count advice so the user has a concrete next step rather than just being
# told the resume is too long. Resolved through the catalogue rather than written
# out, so the advice cannot name a template that no longer exists, and shows the
# name the picker shows rather than the internal key.
TIGHTEST_TEMPLATE_KEY = "sb2nov"
TIGHTEST_TEMPLATE_NAME = builtin(TIGHTEST_TEMPLATE_KEY).name
GITHUB_RE = re.compile(r"https?://(?:www\.)?github\.com/([^/\s]+)/([^/#?\s]+)", re.I)
NUMBER_RE = re.compile(
    r"(?<!\w)(?:\$?\d[\d,.]*%?|\d+\s?(?:ms|s|sec|min|hours?|days?|x))(?!\w)",
    re.I,
)
# The career-ops benchmark's ban list, kept in one place so the tailor prompt,
# the rewrite guard and this review all police the same wording.
BANNED_PHRASES = set(BANNED_WORDING)

# Writing flags that cost the resume points rather than just earning a note. A
# duplicated bullet, a padded one or one nobody can read in two lines is a defect
# a reader will see; a repeated opening verb is a polish item.
SUBSTANTIVE_WRITING_FLAGS = (
    "near_duplicate_bullets",
    "too_many_bullets",
    "too_long",
    "first_person",
    "jd_padding",
    "inflated_rewrite",
    "banned_wording",
    "dash",
    # A summary claiming a domain the page cannot back is an overclaim, so it costs
    # points. It stays a warning rather than a block: the refine loop is told about
    # it and rewrites the summary, and an honest resume going out beats another
    # blocking verdict the user has to clear by hand.
    "unevidenced_domain",
    # The reader-side checks. These are not polish items: a missing graduation
    # month is the single most common reason a student resume is screened out
    # before anyone technical reads it, and a skill the page never demonstrates
    # is the claim that collapses in the interview. Left as suggestions they
    # were worth one point each against a cap of five, so a resume could carry
    # all four and lose three points.
    "no_graduation_month_and_year",
    "missing_education",
    "no_github_link",
    "no_linkedin_link",
    "unevidenced_skill",
)
class ModelReviewIssue(BaseModel):
    severity: str
    code: str
    message: str
    section: str | None = None


def _coerce_score(value: Any) -> Any:
    """Accept a score the model wrote as 87.5 or "88" rather than rejecting it.

    A strict int field failed the whole review over a decimal point, which cost
    the entire model half of the score for a purely cosmetic reason.
    """
    if isinstance(value, str):
        try:
            value = float(value.strip())
        except ValueError:
            return value
    if isinstance(value, float):
        return round(value)
    return value


class ModelReview(BaseModel):
    score: Annotated[int, BeforeValidator(_coerce_score)] = Field(ge=0, le=100)
    issues: list[ModelReviewIssue] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    summary: str = ""


class BlockedClaim(BaseModel):
    """One claim the edit tried to add that no verified fact supports.

    Structured so the caller can name the exact number and the exact sentence it
    appeared in, rather than showing a bare 400 the user reads as "broken".
    """

    metric: str
    text: str
    reason: str = "No verified Profile fact contains this number."
    remedy: str = (
        "Add it as a verified fact on your Profile, then ask for the edit again."
    )


class RevisionOutput(BaseModel):
    assistant_message: str
    suggestions: list[str] = Field(default_factory=list)
    json_resume: dict[str, Any]
    # Filled by the server after the model answers, never by the model.
    blocked_claims: list[BlockedClaim] = Field(default_factory=list)


def validate_json_resume_document(doc: dict[str, Any]) -> dict[str, Any]:
    """Reject malformed or oversized JSON Resume documents before persistence."""
    if len(json.dumps(doc, ensure_ascii=False).encode("utf-8")) > 300_000:
        raise ValueError("JSON Resume exceeds the 300 KB structured-data limit.")
    basics = doc.get("basics", {})
    if not isinstance(basics, dict):
        raise ValueError("JSON Resume basics must be an object.")
    for section in (
        "work",
        "education",
        "projects",
        "skills",
        "certificates",
        "languages",
        "volunteer",
        "publications",
        "awards",
    ):
        entries = doc.get(section, [])
        if entries is None:
            continue
        if not isinstance(entries, list) or not all(
            isinstance(entry, dict) for entry in entries
        ):
            raise ValueError(f"JSON Resume section {section} must be a list of objects.")
        for entry in entries:
            highlights = entry.get("highlights")
            if highlights is not None and (
                not isinstance(highlights, list)
                or not all(isinstance(item, str) for item in highlights)
            ):
                raise ValueError(f"{section}.highlights must be a list of strings.")
    return doc


def _compact_facts(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The evidence vault, small enough that none of it gets truncated away.

    Sent whole, a profile is mostly skill facts carrying empty dates, locations,
    source urls and bullet lists, and the payload overran the prompt budget. What
    got cut was the tail, which is where the skills live, so the reviewer could
    not see them and reported verified skills as invented technology claims:
    "Qwen is named ... but no verified fact mentions Qwen", about a skill fact
    literally titled "LLM integration (OpenAI, Anthropic, Qwen)".

    Defensive about shape, because this is no longer fed only by our own
    workspace loader. `/resumes/render-review` now accepts the vault from the
    browser over HTTP, so a payload that arrives as a JSON string, or a bullet
    that is not an object, is ordinary untrusted input. Raising here would 500
    the whole request and take the PDF render down with the review, when the
    right answer is to use what is well formed and ignore what is not.
    """
    compact: list[dict[str, Any]] = []
    for fact in facts:
        if not isinstance(fact, dict):
            continue
        payload = fact.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        kind = str(fact.get("kind") or "")
        if kind == "skill":
            compact.append(
                {
                    "kind": "skill",
                    "title": fact.get("title"),
                    "category": payload.get("category") or fact.get("org"),
                }
            )
            continue
        raw_bullets = fact.get("bullets")
        bullets = [
            str(bullet.get("text") or "")
            for bullet in (raw_bullets if isinstance(raw_bullets, list) else [])
            if isinstance(bullet, dict)
        ]
        entry = {
            "kind": kind,
            "title": fact.get("title"),
            "org": fact.get("org"),
            "start_date": fact.get("start_date"),
            "end_date": fact.get("end_date"),
            "location": fact.get("location"),
            "source_url": fact.get("source_url"),
            "payload": payload,
            "bullets": [bullet for bullet in bullets if bullet],
        }
        compact.append({key: value for key, value in entry.items() if value})
    return compact


def _client() -> anthropic.AsyncAnthropic:
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set.")
    return anthropic.AsyncAnthropic(
        auth_token=settings.anthropic_api_key,
        base_url=settings.anthropic_base_url or None,
    )


def _resume_text(doc: dict[str, Any]) -> str:
    parts: list[str] = []
    basics = doc.get("basics") or {}
    parts.extend(str(basics.get(key) or "") for key in ("name", "label", "summary"))
    for section in ("work", "projects", "education", "skills", "certificates"):
        for item in doc.get(section, []) or []:
            parts.append(json.dumps(item, ensure_ascii=False))
    return "\n".join(parts)


def _repo_from_url(value: str | None) -> tuple[str, str] | None:
    if not value:
        return None
    match = GITHUB_RE.search(value)
    if not match:
        return None
    return match.group(1), match.group(2).removesuffix(".git")


def _github_repositories(
    doc: dict[str, Any], *, requested_text: str = ""
) -> dict[str, tuple[str, str]]:
    """Resolve the repositories a resume's own projects link to.

    A GitHub URL sitting on the candidate's verified project is theirs by
    assertion, so it is the evidence to load. This used to accept a repository
    only when its owner matched one hardcoded username, which meant changing
    handle, or hosting a project under an organisation, silently stopped
    evidence loading and cost the review points through
    github_evidence_unavailable warnings. Projects with no URL still fall back
    to the name lookup in KNOWN_GITHUB_REPOS.
    """
    repos: dict[str, tuple[str, str]] = {}
    for project in doc.get("projects", []) or []:
        project_name = str(project.get("name") or "").strip()
        parsed = _repo_from_url(project.get("url"))
        if parsed:
            repos[f"{project_name or parsed[1]} [{parsed[1]}]"] = parsed
            continue
        normalized = re.sub(r"[^a-z0-9]+", " ", project_name.lower()).strip()
        # Match on the words the project name contains, not on the whole string.
        # A verified project is titled "BedRocked, Civic Sewer-Sequencing
        # Platform", which never equalled the key "bedrocked", so an exact
        # lookup loaded evidence for none of the real projects and the review
        # graded every project claim with nothing to check it against.
        for known_name, known_repos in KNOWN_GITHUB_REPOS.items():
            if not re.search(rf"\b{re.escape(known_name)}\b", normalized):
                continue
            for known in known_repos:
                repos[f"{project_name or known[1]} [{known[1]}]"] = known

    normalized_request = re.sub(r"[^a-z0-9]+", " ", requested_text.lower())
    for project_name, known_repos in KNOWN_GITHUB_REPOS.items():
        if project_name in normalized_request:
            for known in known_repos:
                repos[f"{project_name.title()} [{known[1]}]"] = known
    return repos


# Why a README could not be read, split by whose problem it is. A repository the
# resume links to that does not answer is the resume's problem: the URL is wrong
# or the repo is private, and a reader clicking it hits the same wall. Everything
# else is ours, and the resume must not be marked down for our missing token.
GITHUB_NOT_FOUND = "not_found"
GITHUB_RATE_LIMITED = "rate_limited"
GITHUB_UNAUTHORIZED = "unauthorized"
GITHUB_UNREACHABLE = "unreachable"
# The reasons that say nothing about the candidate.
GITHUB_OUR_FAULT = frozenset({GITHUB_RATE_LIMITED, GITHUB_UNAUTHORIZED, GITHUB_UNREACHABLE})


def _github_failure_reason(status_code: int, headers: Any) -> str:
    """Classify a non-200 from the GitHub API.

    403 and 429 both carry the rate limit; 403 is also what an exhausted
    unauthenticated quota returns, which is the common case from a shared cloud
    IP where sixty requests an hour is the whole budget. GitHub sets
    x-ratelimit-remaining to 0 on those, which separates them from a 403 for a
    private repository.
    """
    if status_code in (403, 429):
        remaining = None
        try:
            remaining = (headers or {}).get("x-ratelimit-remaining")
        except AttributeError:
            remaining = None
        if remaining == "0":
            return GITHUB_RATE_LIMITED
        return GITHUB_UNAUTHORIZED
    if status_code == 401:
        return GITHUB_UNAUTHORIZED
    if status_code == 404:
        return GITHUB_NOT_FOUND
    return GITHUB_UNREACHABLE


async def load_github_context(
    doc: dict[str, Any], *, requested_text: str = ""
) -> tuple[dict[str, dict[str, str]], list[str], dict[str, str]]:
    """Fetch current README text and commit-pinned SHA for included projects.

    Returns the contexts, the slugs that were checked, and a slug -> reason map
    for the ones that could not be read. The reason matters: production reviews
    carry `github_evidence_unavailable` warnings that cost five points each while
    a local run fetches every repo in 0.22s, because the deployed environments
    have no GITHUB_TOKEN and sixty unauthenticated requests an hour is nothing
    from a shared cloud IP. Deducting from the resume for that is scoring the
    candidate on our configuration.
    """
    repos = _github_repositories(doc, requested_text=requested_text)

    contexts: dict[str, dict[str, str]] = {}
    checked: list[str] = []
    missing: dict[str, str] = {}
    if not repos:
        return contexts, checked, missing

    settings = get_settings()
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "job-os-resume-verifier",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if settings.github_token:
        headers["Authorization"] = f"Bearer {settings.github_token}"

    async with httpx.AsyncClient(timeout=6, follow_redirects=True, headers=headers) as client:

        async def fetch_one(
            project_name: str, owner: str, repo: str
        ) -> tuple[str, str, str, str | None, str]:
            slug = f"{owner}/{repo}"
            try:
                response = await client.get(f"https://api.github.com/repos/{slug}/readme")
                if response.status_code != 200:
                    reason = _github_failure_reason(response.status_code, response.headers)
                    # Logged at warning, with the status, because the previous
                    # silence made a missing token look exactly like a deleted
                    # repository and there was no way to tell from the outside
                    # whether GITHUB_TOKEN was working.
                    log.warning(
                        "resume.github_readme_unavailable",
                        repo=slug,
                        status=response.status_code,
                        reason=reason,
                        authenticated=bool(settings.github_token),
                    )
                    return project_name, slug, "", None, reason
                payload = response.json()
                encoded = str(payload.get("content") or "").replace("\n", "")
                readme = base64.b64decode(encoded).decode("utf-8", errors="replace")
                return project_name, slug, readme[:16000], str(payload.get("sha") or ""), ""
            except (httpx.HTTPError, ValueError) as exc:
                log.warning(
                    "resume.github_readme_unavailable",
                    repo=slug,
                    reason=GITHUB_UNREACHABLE,
                    error=str(exc),
                    authenticated=bool(settings.github_token),
                )
                return project_name, slug, "", None, GITHUB_UNREACHABLE

        results = await asyncio.gather(
            *(
                fetch_one(project_name, owner, repo)
                for project_name, (owner, repo) in repos.items()
            )
        )

    for project_name, slug, readme, sha, reason in results:
        if not readme:
            missing[slug] = reason or GITHUB_UNREACHABLE
            continue
        contexts[project_name] = {
            "repository": slug,
            "readme_sha": sha or "unknown",
            "readme": readme,
        }
        checked.append(f"{slug}@{(sha or 'unknown')[:8]}")
    return contexts, checked, missing


def deterministic_review(
    doc: dict[str, Any],
    pdf_bytes: bytes,
) -> tuple[list[ResumeReviewIssue], int, bool]:
    issues: list[ResumeReviewIssue] = []
    # No PDF means the runtime has no LaTeX engine, not that the document is
    # bad. Skip the two checks that need a render and say so, rather than
    # failing a resume for something it did not do. See review_resume.
    if not pdf_bytes:
        issues.append(
            ResumeReviewIssue(
                severity="warning",
                code="render_unavailable",
                message=(
                    "Page count and selectable text were not checked: this "
                    "runtime cannot render a PDF."
                ),
            )
        )
        return issues + _document_review(doc), 0, False

    reader = PdfReader(io.BytesIO(pdf_bytes))
    page_count = len(reader.pages)
    extracted = "\n".join((page.extract_text() or "") for page in reader.pages).strip()
    text_selectable = len(extracted) >= 200

    if page_count > 1:
        # Advice, not a veto. One page is the goal and the score still reflects
        # missing it, but a resume that is a page and a bit is a real document the
        # user may well want to send, and blocking it outright produced exactly the
        # "why do I have to edit this again" loop that the false fabrication flags
        # did. The renderer will not shrink margins or fonts to fake a fit, which is
        # right, so the honest options are to trim or to pick a tighter template.
        issues.append(
            ResumeReviewIssue(
                severity="warning",
                code="page_count",
                message=(
                    f"Renders to {page_count} pages. One page is the target: trim "
                    "a bullet or a project, or switch to a tighter single-column "
                    f"template. {TIGHTEST_TEMPLATE_NAME} fits the same content on "
                    "one page."
                ),
            )
        )
    if not text_selectable:
        issues.append(
            ResumeReviewIssue(
                severity="blocking",
                code="selectable_text",
                message="Rendered PDF does not contain enough selectable text for ATS parsing.",
            )
        )
    else:
        # `text_selectable` only asks whether there is text. This asks whether the
        # text is any good, which is a different question and the one that was
        # actually costing interviews: a render can be full of selectable text and
        # still hand a parser `COMPUTERSCiENCE` or a literal `\faGlobe`. A warning
        # rather than a veto, for the reason given on the page count above, and
        # because the fix is to switch template, which the message names.
        # `doc` is passed so the audit can run its primary check, which compares
        # the text layer against the words this very document contains.
        audit = audit_pdf_text(pdf_bytes, source_document=doc)
        if audit.available and not audit.clean:
            detail = "; ".join(audit.artifacts)
            issues.append(
                ResumeReviewIssue(
                    severity="warning",
                    code="ats_text_layer",
                    message=(
                        f"The text an applicant tracking system reads is damaged: {detail}. "
                        "The page looks correct, but keyword matching runs on this text. "
                        f"Switching to {TIGHTEST_TEMPLATE_NAME} avoids it."
                    ),
                )
            )

    return issues + _document_review(doc), page_count, text_selectable


def _document_review(doc: dict[str, Any]) -> list[ResumeReviewIssue]:
    """Everything a rule can check without a rendered PDF."""
    issues: list[ResumeReviewIssue] = []
    basics = doc.get("basics") or {}
    for key in ("name", "email", "phone"):
        if not str(basics.get(key) or "").strip():
            issues.append(
                ResumeReviewIssue(
                    severity="blocking",
                    code=f"missing_{key}",
                    message=f"Contact field {key} is missing.",
                    section="basics",
                )
            )

    text = _resume_text(doc)
    if has_banned_separator(text):
        issues.append(
            ResumeReviewIssue(
                severity="warning",
                code="prose_dash",
                message=(
                    "Replace em dashes, en dashes, double hyphens or middle dots "
                    "in prose with a comma, a colon or a period."
                ),
            )
        )
    lowered = text.lower()
    found = sorted(phrase for phrase in BANNED_PHRASES if phrase in lowered)
    if found:
        issues.append(
            ResumeReviewIssue(
                severity="warning",
                code="inflated_language",
                message=f"Remove inflated wording: {', '.join(found)}.",
            )
        )
    if not (doc.get("work") or []):
        issues.append(
            ResumeReviewIssue(
                severity="blocking",
                code="missing_experience",
                message="Professional experience is missing.",
                section="work",
            )
        )
    if len(doc.get("projects") or []) < 2:
        issues.append(
            ResumeReviewIssue(
                severity="warning",
                code="project_depth",
                message="Use at least two relevant projects when space allows.",
                section="projects",
            )
        )
    # Writing problems a rule can see: over-long bullets, two bullets about the
    # same work, a repeated opening verb, first person, JD padding. These are the
    # things a reader notices first, and naming them here means the score
    # reflects them even when the model review is unavailable.
    for where, flags in document_quality_flags(doc).items():
        section = where.split(":", 1)[0]
        # A repeated opening verb is worth mentioning; a role that says the same
        # thing twice or pads a bullet with JD wording is worth points.
        substantive = any(
            flag.startswith(SUBSTANTIVE_WRITING_FLAGS) for flag in flags
        )
        issues.append(
            ResumeReviewIssue(
                severity="warning" if substantive else "suggestion",
                code="bullet_writing",
                message=f"{where}: {', '.join(flags)}.",
                section=section,
            )
        )
    # Deliberately no checks on WHICH employer or WHICH skills appear. Those
    # facts belong to the candidate, and asserting them here turned the scorer
    # into a rule that no edited resume could satisfy: naming one employer as
    # the only permitted one blocks any legitimate new job, and banning specific
    # languages blocks ever learning them. The no-hallucination contract is
    # enforced where it belongs, by grounding every bullet in a verified fact and
    # by CAREER_OPS_RULES in the model's system prompt. This function checks the
    # structure of the document, not the truth of the candidate's history.
    return issues


# Points deducted per issue when scoring a review. A blocking issue is the
# heaviest, a warning is a deduction a reader would really make, a suggestion is a
# minor polish note. The suggestion total is capped so a thorough reviewer that
# lists many small notes cannot, by being thorough, score a clean resume down.
_BLOCKING_PENALTY = 20
_WARNING_PENALTY = 5
_SUGGESTION_PENALTY = 1
_MAX_SUGGESTION_PENALTY = 5


def _score_from_issues(
    issues: list[ResumeReviewIssue],
) -> tuple[Decimal, dict[str, int]]:
    """The 0-100 score derived deterministically from the weighted issue list.

    The number is a function of the issues alone, so identical inputs produce an
    identical score. This is what replaces the reviewing model's free-form 0-100
    self-report, which swung nine points across three identical reviews of one real
    document (85, 89, 80) while the issue counts barely moved: that swing was the
    score whiplash the user felt at finalize. Deriving the number from the issues
    makes every deducted point traceable to a named issue, and makes a no-PDF draft
    agree with a render-backed finalize except for the one check a draft genuinely
    cannot run, the page count.

    Only `source == "rule"` issues are counted here. A model-judged issue
    (missing flagship project, lane focus, a bullet worth tightening) is real
    editorial input, but it is the reviewing model's own judgment call on THIS
    run, not a reproducible fact about the document -- the same resume review
    twice can surface a different set of these at default temperature, and a
    score built from them stops being a function of the resume. Passed as
    `issues` still carries them; the caller reports them as advisory notes with
    no point value attached, which is what keeps them informative without
    reopening the whiplash this function was built to end.
    """
    issues = [issue for issue in issues if issue.source == "rule"]
    blocking = sum(1 for issue in issues if issue.severity == "blocking")
    warning = sum(1 for issue in issues if issue.severity == "warning")
    suggestion = sum(1 for issue in issues if issue.severity == "suggestion")
    blocking_penalty = _BLOCKING_PENALTY * blocking
    warning_penalty = _WARNING_PENALTY * warning
    suggestion_penalty = min(_MAX_SUGGESTION_PENALTY, _SUGGESTION_PENALTY * suggestion)
    total = blocking_penalty + warning_penalty + suggestion_penalty
    score = Decimal(max(0, 100 - total))
    breakdown = {
        "blocking": blocking,
        "warning": warning,
        "suggestion": suggestion,
        "blocking_penalty": blocking_penalty,
        "warning_penalty": warning_penalty,
        "suggestion_penalty": suggestion_penalty,
        "total_penalty": total,
    }
    return score, breakdown


def _document_quality_score(doc: dict[str, Any]) -> Decimal:
    """A cheap, deterministic quality proxy from the document checks alone.

    No render, no model, no network, so it is safe to call twice around an edit to
    tell whether the edit made the resume measurably worse. It is the same
    penalty model the full review uses, restricted to what a rule can see, so the
    two never contradict each other.
    """
    score, _ = _score_from_issues(_document_review(doc))
    return score


def provisional_review(doc: dict[str, Any]) -> ResumeReviewResult:
    """The rules-only half of `review_resume`: no render, no model, no network.

    A runtime that can neither render a PDF nor afford a ninety-second review
    call still owes the caller an honest first read of the document. This runs
    exactly the checks a rule can run and scores them on the same weighted issue
    model the full review uses, so the two never contradict each other.

    `passed` is False by construction. The independent review has not happened,
    and an unknown is not a pass, which is the same stance `review_resume` takes
    when its model call fails. The caller is expected to replace this wholesale
    the moment a render-backed review lands.
    """
    validate_json_resume_document(doc)
    issues, page_count, text_selectable = deterministic_review(doc, b"")
    score, breakdown = _score_from_issues(issues)
    return ResumeReviewResult(
        score=score.quantize(Decimal("0.1")),
        passed=False,
        page_count=page_count,
        text_selectable=text_selectable,
        issues=issues,
        strengths=[],
        github_projects_checked=[],
        model_summary=(
            "Provisional score from the automated document checks only. The "
            "independent AI review, the page count and the selectable-text "
            "check run once the PDF is rendered."
        ),
        model_estimate=None,
        score_breakdown=breakdown,
    )


async def review_resume(
    doc: dict[str, Any],
    *,
    template_key: str | None = None,
    latex_source: str | None = None,
    verified_facts: list[dict[str, Any]] | None = None,
    on_partial: Callable[[ResumeReviewResult, bytes], None] | None = None,
) -> tuple[ResumeReviewResult, bytes]:
    """Render, inspect, then run an independent quality-model review.

    `template_key` names one of the bundled LaTeX templates and `latex_source`
    supplies a stored one. The review judges the document either way: a template
    changes how the resume looks, not what it claims.

    A runtime with no LaTeX engine returns a review and empty PDF bytes rather
    than raising. The Appwrite function is such a runtime, and a tailored draft
    coming back without a page count is far better than one coming back not at
    all; the caller decides whether an empty render is acceptable.

    `verified_facts` is the evidence the resume was built from. Without it the
    reviewer has no way to tell a verified claim from an invented one, so it
    treated anything absent from CAREER_OPS_RULES as fabricated and returned
    blocking issues against the candidate's own history: a real job title, a
    real client domain and real coursework were all called fabrications on the
    same pass. Pass it wherever it is available.

    `on_partial`, if given, fires once the PDF and the deterministic checks are
    ready but before the GitHub-evidence lookup or the model call -- the two
    things that make this call take a minute plus. The PDF is fully real by
    that point (page count and selectable text both come from the same
    render), so a caller wanting the finalized document as soon as possible
    does not have to wait on the model's advisory notes to show it. The score
    at this point already IS the final score: `source == "rule"` issues are
    the only ones `_score_from_issues` counts (see that function), and the
    model call below can only ever ADD advisory issues, never rule ones, so
    nothing past this point moves the number.
    """
    validate_json_resume_document(doc)
    try:
        pdf_bytes = (
            await render_resume_pdf_async(
                doc, template_key=template_key, latex_source=latex_source
            )
        ).bytes_
    except TectonicUnavailableError as exc:
        log.warning("resume_render_unavailable", error=str(exc))
        pdf_bytes = b""
    rule_issues, page_count, text_selectable = deterministic_review(doc, pdf_bytes)
    if on_partial is not None:
        partial_score, partial_breakdown = _score_from_issues(rule_issues)
        on_partial(
            ResumeReviewResult(
                score=partial_score.quantize(Decimal("0.1")),
                # Not a verdict yet: the model call can still raise a
                # blocking issue (a fabrication) that a rule cannot see, and
                # `passed` is not a pass until that has actually run. Mirrors
                # the same stance provisional_review takes for the same reason.
                passed=False,
                page_count=page_count,
                text_selectable=text_selectable,
                issues=rule_issues,
                strengths=[],
                github_projects_checked=[],
                model_summary=(
                    "Deterministic score and PDF are final. The independent "
                    "AI review is still running and will add advisory notes "
                    "shortly; they will not change this score."
                ),
                model_estimate=None,
                score_breakdown=partial_breakdown,
            ),
            pdf_bytes,
        )
    github_context, checked, missing_repos = await load_github_context(doc)
    for slug, reason in missing_repos.items():
        # A repository the resume links to that does not answer is the resume's
        # problem: a reader clicking that link hits the same 404, so it stays a
        # warning. A rate limit or a missing token is OURS, and charging the
        # candidate five points per repo for it is scoring them on our
        # configuration. Deployed reviews were losing ten points a run to this
        # while the identical fetch succeeded locally in 0.22s.
        ours = reason in GITHUB_OUR_FAULT
        if reason == GITHUB_UNAUTHORIZED:
            # GitHub returns 403 both for a token without the right scope and for
            # a repository we are not allowed to see, and there is no way to tell
            # which from the response. Scored as ours, because guessing wrong in
            # the other direction charges the candidate for our credentials, but
            # worded so a genuinely private repo is not silently excused: a
            # recruiter clicking that link would be turned away too.
            detail = (
                f"GitHub refused the request for {slug}, which means either our "
                "access token or a repository that is not public. Worth confirming "
                "the repository is public, since a reader following that link "
                "would be turned away as well."
            )
        elif ours:
            detail = (
                f"Could not reach GitHub to re-check {slug} ({reason}), so its "
                "project claims were reviewed against the verified facts alone. "
                "This is a limit on the check, not a problem with the resume."
            )
        else:
            detail = (
                f"Current README evidence could not be loaded for {slug}: the "
                f"repository did not answer ({reason}). Confirm the project URL is "
                "right and the repository is public."
            )
        rule_issues.append(
            ResumeReviewIssue(
                severity="suggestion" if ours else "warning",
                code="github_evidence_unavailable",
                message=detail,
                section="projects",
            )
        )

    settings = get_settings()
    writing_flags = document_quality_flags(doc)
    prompt = (
        "Review this JSON Resume after drafting. Return one JSON object with "
        "score (0-100), issues, strengths, and summary. Issue severity must be "
        "blocking, warning, or suggestion. Be strict about truth, readability, "
        "project evidence, one-page relevance, and backend/AI positioning.\n\n"
        f"TODAY'S DATE: {date.today().isoformat()}. A start date in the recent "
        "past is ordinary. Do not call a date fabricated because it looks "
        "future-dated to you.\n\n"
        "WHAT COUNTS AS FABRICATION. The VERIFIED FACTS below are the "
        "candidate's own evidence vault and they are the source of truth for "
        "what is true about them. A claim is fabricated only when nothing in "
        "the verified facts, the current resume, or the README evidence "
        "supports it. A job title, employer, client, date, course, metric or "
        "technology that appears in the verified facts is NOT fabricated, even "
        "if the career-ops rules in your system prompt word it differently or "
        "do not mention it at all. Those rules are a boundary on what may be "
        "ADDED, not an inventory of everything the candidate has done. When the "
        "two disagree about a detail, the verified facts win and there is no "
        "issue to report.\n\n"
        "Reply with the JSON object only: no prose around it, no markdown "
        "fences. Report at most 10 issues, the most important first.\n\n"
        "HOW THE GRADE IS COMPUTED. The resume's score is derived from the issues "
        "you report, weighted by severity, not from your `score` field, which is "
        "advisory. So report every real problem as an issue with an honest "
        "severity: blocking for a fabrication or a missing contact field, warning "
        "for an overclaim, an unverified metric, a domain the page cannot back or a "
        "readability defect, suggestion for a minor polish item. Do not hold a "
        "concern back because you already lowered the number.\n\n"
        f"RESUME:\n{json.dumps(doc, ensure_ascii=False)[:24000]}\n\n"
        "VERIFIED FACTS (the evidence this resume was built from, complete):\n"
        f"{json.dumps(_compact_facts(verified_facts or []), ensure_ascii=False)[:40000]}\n\n"
        "CURRENT GITHUB README EVIDENCE:\n"
        f"{json.dumps(github_context, ensure_ascii=False)[:22000]}\n\n"
        "DETERMINISTIC WRITING CHECKS ALREADY RUN (do not repeat these, they "
        "are reported separately; judge what they cannot see):\n"
        f"{json.dumps(writing_flags, ensure_ascii=False)[:4000]}\n\n"
        f"SCHEMA:\n{json.dumps(ModelReview.model_json_schema())}"
    )
    messages: list[anthropic.types.MessageParam] = [{"role": "user", "content": prompt}]

    async def ask(*, max_tokens: int = REVIEW_MAX_TOKENS) -> tuple[str, Any]:
        response = await create_message(
            _client(),
            model=settings.anthropic_model_verify,
            max_tokens=max_tokens,
            system=CAREER_OPS_RULES,
            messages=messages,
            extra_headers={"x-manifest-tier": settings.manifest_tier_sonnet},
        )
        return response_text(response), response

    # None means the review genuinely could not run, which is scored differently
    # from a review that ran and found problems. See below.
    model_review: ModelReview | None = None
    try:
        raw, response = await ask()
        try:
            model_review = parse_model_json(ModelReview, raw)
        except ValidationError:
            log.warning(
                "resume.review_not_json",
                preview=raw[:300],
                **response_diagnostics(response),
            )
            # Same distinction the tailor makes: a reply with no text at all ran
            # out of output room, and re-asking with the same ceiling plus a
            # "that was not valid JSON" note reproduces it. Giving the reviewer
            # the verified facts made it more thorough, which is what pushed a
            # thorough review past the old ceiling and returned nothing.
            if raw.strip():
                messages = [
                    *messages,
                    {"role": "assistant", "content": raw[:4000]},
                    {"role": "user", "content": JSON_ONLY_RETRY},
                ]
                retry_tokens = REVIEW_MAX_TOKENS
            else:
                messages = [*messages, {"role": "user", "content": EMPTY_REPLY_RETRY}]
                retry_tokens = REVIEW_RETRY_MAX_TOKENS
            retry_raw, retry_response = await ask(max_tokens=retry_tokens)
            try:
                model_review = parse_model_json(ModelReview, retry_raw)
            except ValidationError:
                log.warning(
                    "resume.review_not_json_after_retry",
                    preview=retry_raw[:300],
                    **response_diagnostics(retry_response),
                )
                raise
    except (
        ValidationError,
        json.JSONDecodeError,
        anthropic.APIError,
        httpx.HTTPError,
        TimeoutError,
        RuntimeError,
    ) as exc:
        # `httpx.HTTPError` for a stream that dies mid-reply: it arrives raw, past
        # every anthropic class, and the point of this block is that a missing
        # model review is a warning on a real review rather than a failed request.
        # `TimeoutError` for the same reason: `create_message`'s wall-clock
        # deadline (llm_json.py) raises it bare when a stream stays technically
        # alive without making real progress, past every anthropic/httpx class.
        log.warning("resume.review_model_failed", error=repr(exc))
        rule_issues.append(
            ResumeReviewIssue(
                severity="warning",
                code="model_review_unavailable",
                message=(
                    "The independent AI review could not complete. "
                    "Try review again before finalizing."
                ),
            )
        )

    issues = [
        *rule_issues,
        *[
            ResumeReviewIssue(
                severity=(
                    issue.severity
                    if issue.severity in {"blocking", "warning", "suggestion"}
                    else "warning"
                ),
                code=issue.code,
                message=issue.message,
                section=issue.section,
                # The one place a "model" issue enters the list. See
                # ResumeReviewIssue.source and _score_from_issues: these are
                # reported to the user but do not move the score.
                source="model",
            )
            for issue in (model_review.issues if model_review else [])
        ],
    ]
    # Score deterministically from the weighted issue list, not from the model's
    # own 0-100 number. The model number was the whiplash: it swung nine points
    # across identical reviews of one document. It is kept as an advisory estimate
    # in the report, never as the grade. The model still contributes ISSUES, which
    # are what the number is built from and are far more stable run to run.
    score, breakdown = _score_from_issues(issues)
    score = score.quantize(Decimal("0.1"))
    model_estimate = int(model_review.score) if model_review is not None else None
    # An unavailable review is an unknown, and an unknown is not a pass. The
    # deterministic checks alone scored a resume 95 and reported passed=True on a
    # run where the model review never returned a single token, which hands the
    # user a green light from a check that did not happen. The score still says
    # what was verified; only the verdict waits. Re-running the review is one
    # click, and the retry path above makes it likely to succeed.
    passed = (
        model_review is not None
        and score >= PASS_SCORE
        and not any(issue.severity == "blocking" for issue in issues)
    )
    return (
        ResumeReviewResult(
            score=score,
            passed=passed,
            page_count=page_count,
            text_selectable=text_selectable,
            issues=issues,
            strengths=model_review.strengths if model_review else [],
            github_projects_checked=checked,
            model_summary=(
                model_review.summary
                if model_review
                else "The independent AI review did not run. Score reflects the "
                "automated document checks only."
            ),
            model_estimate=model_estimate,
            score_breakdown=breakdown,
        ),
        pdf_bytes,
    )


async def revise_resume(
    doc: dict[str, Any],
    *,
    message: str,
    verified_facts: list[dict[str, Any]],
) -> RevisionOutput:
    """Apply a conversational edit without allowing new unsupported claims."""
    github_context, _checked, missing_repos = await load_github_context(
        doc, requested_text=message
    )
    settings = get_settings()
    prompt = (
        "The user is editing a resume in chat. Apply the request only when it is "
        "supported by the current resume, verified facts, or current GitHub README "
        "evidence. If a requested claim is unsupported, explain that in "
        "assistant_message and leave it out. Return the complete JSON Resume, not a "
        "patch. Preserve JSON Resume keys. Keep it one-page and concise.\n\n"
        f"USER REQUEST:\n{message}\n\n"
        f"CURRENT RESUME:\n{json.dumps(doc, ensure_ascii=False)[:24000]}\n\n"
        # Compact, for the same reason the review is: sent raw, this profile is
        # 40,179 characters and the 18,000 cut landed mid-JSON, dropping 39 skill
        # facts, 3 certifications, 3 projects, an education entry and a job. The
        # editor was refusing supported claims because the evidence for them had
        # been truncated away. Compacted it is 12,542 characters and fits whole.
        "VERIFIED FACTS (complete):\n"
        f"{json.dumps(_compact_facts(verified_facts), ensure_ascii=False)[:18000]}\n\n"
        f"GITHUB README EVIDENCE:\n{json.dumps(github_context, ensure_ascii=False)[:20000]}\n\n"
        f"UNAVAILABLE GITHUB EVIDENCE:\n{json.dumps(missing_repos)}\n\n"
        # The request reads like a chat turn, so say plainly that the reply is
        # not one. Without this the model sometimes answers conversationally
        # ("**Assistant message:** ... I'll run the Review action myself"),
        # which is not JSON and used to 400 the whole revision.
        "Reply with one raw JSON object matching the schema below and nothing "
        "else: no prose before or after it, no markdown fences. Anything you "
        "want to say to the user belongs in the assistant_message field.\n\n"
        f"OUTPUT SCHEMA:\n{json.dumps(RevisionOutput.model_json_schema())}"
    )
    client = _client()
    messages: list[anthropic.types.MessageParam] = [{"role": "user", "content": prompt}]

    async def ask() -> str:
        response = await create_message(
            client,
            model=settings.anthropic_model_tailor,
            max_tokens=REVISE_MAX_TOKENS,
            system=CAREER_OPS_RULES,
            messages=messages,
            extra_headers={"x-manifest-tier": settings.manifest_tier_sonnet},
        )
        return response_text(response)

    raw = await ask()
    try:
        output = parse_model_json(RevisionOutput, raw)
    except ValidationError:
        # A chatty reply is recoverable, so ask once more rather than failing the
        # edit. What gets sent back depends on what came back, because the retry
        # is a second full document generation and it doubles a two-minute edit.
        #
        # When the reply contained something object-shaped, showing it back is
        # useful: the model can see what to fix. When the reply was pure prose,
        # echoing four thousand characters of it as an assistant turn teaches the
        # conversation that prose is the house style here, and both production
        # retries that were handed their own "**Assistant message:** ..." back
        # answered in prose again and lost the edit. Ask fresh instead, and ask
        # for a compact answer, since output length is what the wait is made of.
        looks_recoverable = "{" in raw and '"json_resume"' in raw
        log.warning(
            "resume.revision_not_json",
            preview=raw[:300],
            recoverable=looks_recoverable,
        )
        messages = (
            [
                *messages,
                {"role": "assistant", "content": raw[:4000]},
                {"role": "user", "content": JSON_ONLY_RETRY},
            ]
            if looks_recoverable
            else [*messages, {"role": "user", "content": REVISION_FORMAT_RETRY}]
        )
        retried = await ask()
        try:
            output = parse_model_json(RevisionOutput, retried)
        except ValidationError as exc:
            log.warning("resume.revision_not_json_after_retry", preview=retried[:300])
            raise ValueError(
                "The editor could not produce a usable revision. Try rephrasing "
                "the request, or use the Review action directly."
            ) from exc
    validate_json_resume_document(output.json_resume)

    # Defense in depth: new metrics must already exist in verified facts or
    # the current resume. Chat remains an editor, not a fact-creation path.
    #
    # CAREER_OPS_RULES is deliberately NOT a source here, tempting as it looks.
    # It states "Sathyabama ... CGPA 8.39/10.0" where the matching vault fact
    # carries score: null, so harvesting its numbers would end the circular
    # failure where the reviewer suggests adding the CGPA and this guard then
    # rejects it. But the same file spells out the numbers it FORBIDS, "~92%",
    # "2 minutes to 10 seconds", and the cumulative GPA 3.334 that must not be
    # shown automatically. Reading numbers out of a prose rules file cannot tell
    # a permission from a prohibition, so it would whitelist precisely the claims
    # the rules exist to block. The right place to fix the CGPA is the vault.
    source_numbers = set(
        NUMBER_RE.findall(
            _resume_text(doc)
            + "\n"
            + json.dumps(verified_facts, ensure_ascii=False)
            + "\n"
            + json.dumps(github_context, ensure_ascii=False)
        )
    )
    new_numbers = set(NUMBER_RE.findall(_resume_text(output.json_resume)))
    unsupported = set(new_numbers - source_numbers)
    if not unsupported:
        loss = _content_loss_note(doc, output.json_resume)
        if loss:
            output.assistant_message = f"{loss}\n\n{output.assistant_message}".strip()
        return output

    # The guard is unchanged: a number no verified fact supports never reaches the
    # page. What changes is what happens to the rest of the edit. Rejecting the
    # whole revision threw away every honest improvement in it and returned a 400
    # the user reads as the feature being broken, after a two-minute wait. Now the
    # honest parts apply and only the invented claims are dropped.
    cleaned, blocked = _strip_unverified_numbers(
        output.json_resume, original=doc, unsupported=unsupported
    )
    validate_json_resume_document(cleaned)
    log.warning(
        "resume.revision_claims_blocked",
        metrics=sorted(unsupported),
        dropped=len(blocked),
    )
    named = ", ".join(sorted(unsupported))
    notice = (
        f"I left out {len(blocked)} change"
        f"{'' if len(blocked) == 1 else 's'} that introduced numbers your "
        f"Profile does not have ({named}). Everything else in the edit is "
        "applied. To use those numbers, add them as verified facts on your "
        "Profile first, or ask again without them."
    )
    loss = _content_loss_note(doc, cleaned)
    message = f"{notice}\n\n{output.assistant_message}".strip()
    if loss:
        message = f"{notice}\n\n{loss}\n\n{output.assistant_message}".strip()
    return RevisionOutput(
        assistant_message=message,
        suggestions=output.suggestions,
        json_resume=cleaned,
        blocked_claims=blocked,
    )


def _content_loss_note(original: dict[str, Any], revised: dict[str, Any]) -> str | None:
    """An honest heads-up when an edit made the resume thinner, or None.

    A fix that resolves an overclaim by cutting the claim leaves a shorter, more
    honest resume, and a shorter resume scores lower on both keyword coverage and
    the quality checks. Surfacing that here is the difference between a score that
    silently craters after "propose review fixes" and one that drops for a reason
    the user was told. Only fires on a real reduction: a project removed, or the
    deterministic document-quality score falling, so a dedup that trims a repeated
    bullet without hurting the page says nothing.
    """

    def bullet_count(doc: dict[str, Any]) -> int:
        return sum(
            len(entry.get("highlights") or [])
            for section in ("work", "projects", "volunteer")
            for entry in (doc.get(section) or [])
            if isinstance(entry, dict)
        )

    dropped_projects = len(original.get("projects") or []) - len(
        revised.get("projects") or []
    )
    dropped_bullets = bullet_count(original) - bullet_count(revised)
    quality_dropped = _document_quality_score(revised) < _document_quality_score(original)
    if dropped_projects <= 0 and not quality_dropped:
        return None
    if dropped_projects <= 0 and dropped_bullets <= 0:
        return None
    parts: list[str] = []
    if dropped_projects > 0:
        parts.append(f"{dropped_projects} project{'s' if dropped_projects != 1 else ''}")
    if dropped_bullets > 0:
        parts.append(f"{dropped_bullets} bullet{'s' if dropped_bullets != 1 else ''}")
    removed = " and ".join(parts) or "content"
    return (
        f"Heads up: this edit removed {removed}, so the resume is thinner and its "
        "coverage and quality score will reflect that. That is the honest trade-off "
        "for cutting claims your verified Profile does not support. To recover the "
        "score, add the missing evidence as verified facts on your Profile, then "
        "tailor or edit again."
    )


def _strip_unverified_numbers(
    revised: dict[str, Any],
    *,
    original: dict[str, Any],
    unsupported: set[str],
) -> tuple[dict[str, Any], list[BlockedClaim]]:
    """Drop the claims that invented a number, keep the rest of the edit.

    A scalar field reverts to the wording it had before, since that wording was
    already verified. A list item is dropped outright, because there is nothing to
    revert an inserted bullet to. An entry left with no bullets at all gets its
    original bullets back rather than rendering blank.
    """
    blocked: list[BlockedClaim] = []

    def offending(text: str) -> set[str]:
        return set(NUMBER_RE.findall(text)) & unsupported

    def clean(new: Any, old: Any) -> Any:
        if isinstance(new, str):
            found = offending(new)
            if not found:
                return new
            blocked.append(BlockedClaim(metric=", ".join(sorted(found)), text=new))
            return old if isinstance(old, str) else None
        if isinstance(new, dict):
            base = old if isinstance(old, dict) else {}
            return {key: clean(value, base.get(key)) for key, value in new.items()}
        if isinstance(new, list):
            base_list = old if isinstance(old, list) else []
            out: list[Any] = []
            for index, item in enumerate(new):
                previous = base_list[index] if index < len(base_list) else None
                if isinstance(item, str):
                    found = offending(item)
                    if found:
                        blocked.append(
                            BlockedClaim(metric=", ".join(sorted(found)), text=item)
                        )
                        continue
                    out.append(item)
                    continue
                out.append(clean(item, previous))
            return out
        return new

    cleaned = clean(revised, original)

    # A role or project stripped down to nothing is worse than an untouched one.
    for section in ("work", "projects", "volunteer"):
        entries = cleaned.get(section) or []
        originals = original.get(section) or []
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict) or entry.get("highlights"):
                continue
            if index < len(originals) and isinstance(originals[index], dict):
                restored = originals[index].get("highlights")
                if restored:
                    entry["highlights"] = list(restored)
    return cleaned, blocked


def generate_latex_source(doc: dict[str, Any]) -> str:
    """Generate a portable ATS-safe LaTeX source alongside the PDF."""

    def esc(value: Any) -> str:
        text = str(value or "")
        replacements = {
            "\\": r"\textbackslash{}",
            "&": r"\&",
            "%": r"\%",
            "$": r"\$",
            "#": r"\#",
            "_": r"\_",
            "{": r"\{",
            "}": r"\}",
            "~": r"\textasciitilde{}",
            "^": r"\textasciicircum{}",
        }
        return "".join(replacements.get(char, char) for char in text)

    def date_range(item: dict[str, Any]) -> str:
        # Shares latex_render.date_range/_fmt_date with the real Typst/LaTeX
        # templates so this portable source never renders a raw "2026-01-01"
        # where the actual PDF renders "Jan 2026" -- JSON Resume dates are
        # YYYY-MM or YYYY to begin with, this function just used to print
        # them unformatted instead of parsing them.
        return shared_date_range(item.get("startDate"), item.get("endDate"))

    basics = doc.get("basics") or {}
    location = basics.get("location") or {}
    contact = [
        ", ".join(filter(None, [location.get("city"), location.get("region")])),
        basics.get("phone"),
        basics.get("email"),
    ]
    # Every filled-in profile renders, not just the first -- a candidate with
    # both LinkedIn and GitHub set had the second one silently dropped from
    # this portable LaTeX contact line even though the primary Typst/LaTeX
    # templates (latex_render.py's _named_profile) already showed both.
    for profile in basics.get("profiles") or []:
        contact.append(profile.get("url"))
    lines = [
        r"\documentclass[11pt,letterpaper]{article}",
        r"\usepackage[top=0.45in,bottom=0.4in,left=0.6in,right=0.6in]{geometry}",
        r"\usepackage[T1]{fontenc}",
        r"\usepackage{mathptmx}",
        r"\usepackage{enumitem}",
        r"\usepackage{titlesec}",
        r"\usepackage[hidelinks]{hyperref}",
        r"\hyphenpenalty=10000",
        r"\pagestyle{empty}",
        r"\setlength{\parindent}{0pt}",
        r"\titleformat{\section}{\large\bfseries}{}{0em}{\MakeUppercase}[\titlerule]",
        r"\setlist[itemize]{leftmargin=1.4em,itemsep=2pt,topsep=2pt}",
        r"\begin{document}",
        r"\begin{center}",
        rf"{{\Large\bfseries {esc(basics.get('name'))}}}\\[2pt]",
        esc(" | ".join(filter(None, contact))),
        r"\end{center}",
    ]
    for title, key in (
        ("Education", "education"),
        ("Professional Experience", "work"),
        ("Projects", "projects"),
    ):
        entries = doc.get(key) or []
        if not entries:
            continue
        lines.append(rf"\section{{{title}}}")
        for item in entries:
            name = item.get("institution") or item.get("name")
            role = item.get("studyType") or item.get("position") or item.get("description")
            lines.append(rf"\textbf{{{esc(name)}}}\hfill {date_range(item)}\\")
            if role:
                lines.append(rf"\textit{{{esc(role)}}}\\")
            highlights = item.get("highlights") or []
            if highlights:
                lines.append(r"\begin{itemize}")
                lines.extend(rf"\item {esc(bullet)}" for bullet in highlights)
                lines.append(r"\end{itemize}")
    skills = doc.get("skills") or []
    if skills:
        lines.append(r"\section{Technical Skills}")
        for group in skills:
            lines.append(
                rf"\textbf{{{esc(group.get('name'))}:}} "
                + esc(", ".join(group.get("keywords") or []))
                + r"\\"
            )
    lines.append(r"\end{document}")
    return "\n".join(lines)
