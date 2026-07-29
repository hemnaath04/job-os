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
from job_os.services.llm_json import (
    JSON_ONLY_RETRY,
    parse_model_json,
    response_text,
)
from job_os.services.pdf_render import render_resume_pdf
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


class RevisionOutput(BaseModel):
    assistant_message: str
    suggestions: list[str] = Field(default_factory=list)
    json_resume: dict[str, Any]


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


async def load_github_context(
    doc: dict[str, Any], *, requested_text: str = ""
) -> tuple[dict[str, dict[str, str]], list[str], list[str]]:
    """Fetch current README text and commit-pinned SHA for included projects."""
    repos = _github_repositories(doc, requested_text=requested_text)

    contexts: dict[str, dict[str, str]] = {}
    checked: list[str] = []
    missing: list[str] = []
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
        ) -> tuple[str, str, str, str | None]:
            slug = f"{owner}/{repo}"
            try:
                response = await client.get(f"https://api.github.com/repos/{slug}/readme")
                if response.status_code != 200:
                    return project_name, slug, "", None
                payload = response.json()
                encoded = str(payload.get("content") or "").replace("\n", "")
                readme = base64.b64decode(encoded).decode("utf-8", errors="replace")
                return project_name, slug, readme[:16000], str(payload.get("sha") or "")
            except (httpx.HTTPError, ValueError) as exc:
                log.info("resume.github_readme_unavailable", repo=slug, error=str(exc))
                return project_name, slug, "", None

        results = await asyncio.gather(
            *(
                fetch_one(project_name, owner, repo)
                for project_name, (owner, repo) in repos.items()
            )
        )

    for project_name, slug, readme, sha in results:
        if not readme:
            missing.append(slug)
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
    reader = PdfReader(io.BytesIO(pdf_bytes))
    page_count = len(reader.pages)
    extracted = "\n".join((page.extract_text() or "") for page in reader.pages).strip()
    text_selectable = len(extracted) >= 200

    if page_count != 1:
        issues.append(
            ResumeReviewIssue(
                severity="blocking",
                code="page_count",
                message=(
                    f"Resume renders to {page_count} pages. "
                    "Final resumes must be exactly one page."
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
    return issues, page_count, text_selectable


async def review_resume(
    doc: dict[str, Any],
    *,
    html_source: str | None = None,
    css_source: str | None = None,
    verified_facts: list[dict[str, Any]] | None = None,
) -> tuple[ResumeReviewResult, bytes]:
    """Render, inspect, then run an independent quality-model review.

    `html_source`/`css_source` render a stored template's look instead of the
    bundled one. The review judges the document either way: a template changes
    how the resume looks, not what it claims.

    `verified_facts` is the evidence the resume was built from. Without it the
    reviewer has no way to tell a verified claim from an invented one, so it
    treated anything absent from CAREER_OPS_RULES as fabricated and returned
    blocking issues against the candidate's own history: a real job title, a
    real client domain and real coursework were all called fabrications on the
    same pass. Pass it wherever it is available.
    """
    validate_json_resume_document(doc)
    rendered = render_resume_pdf(doc, html_source=html_source, css_source=css_source)
    rule_issues, page_count, text_selectable = deterministic_review(doc, rendered.bytes_)
    github_context, checked, missing_repos = await load_github_context(doc)
    for slug in missing_repos:
        rule_issues.append(
            ResumeReviewIssue(
                severity="warning",
                code="github_evidence_unavailable",
                message=f"Current README evidence could not be loaded for {slug}.",
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
        f"RESUME:\n{json.dumps(doc, ensure_ascii=False)[:24000]}\n\n"
        "VERIFIED FACTS (the evidence this resume was built from):\n"
        f"{json.dumps(verified_facts or [], ensure_ascii=False)[:20000]}\n\n"
        "CURRENT GITHUB README EVIDENCE:\n"
        f"{json.dumps(github_context, ensure_ascii=False)[:22000]}\n\n"
        "DETERMINISTIC WRITING CHECKS ALREADY RUN (do not repeat these, they "
        "are reported separately; judge what they cannot see):\n"
        f"{json.dumps(writing_flags, ensure_ascii=False)[:4000]}\n\n"
        f"SCHEMA:\n{json.dumps(ModelReview.model_json_schema())}"
    )
    messages: list[anthropic.types.MessageParam] = [{"role": "user", "content": prompt}]

    async def ask() -> str:
        response = await _client().messages.create(
            model=settings.anthropic_model_verify,
            # Room for a full issue list. At 3000 a thorough review ran out of
            # tokens mid-JSON, which failed validation and silently cost the
            # whole model half of the score.
            max_tokens=6000,
            system=CAREER_OPS_RULES,
            messages=messages,
            extra_headers={"x-manifest-tier": settings.manifest_tier_sonnet},
        )
        return response_text(response)

    # None means the review genuinely could not run, which is scored differently
    # from a review that ran and found problems. See below.
    model_review: ModelReview | None = None
    try:
        raw = await ask()
        try:
            model_review = parse_model_json(ModelReview, raw)
        except ValidationError:
            log.warning("resume.review_not_json", preview=raw[:300])
            messages = [
                *messages,
                {"role": "assistant", "content": raw[:4000] or "(empty)"},
                {"role": "user", "content": JSON_ONLY_RETRY},
            ]
            model_review = parse_model_json(ModelReview, await ask())
    except (ValidationError, json.JSONDecodeError, anthropic.APIError, RuntimeError) as exc:
        log.warning("resume.review_model_failed", error=str(exc))
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
            )
            for issue in (model_review.issues if model_review else [])
        ],
    ]
    deterministic_penalty = sum(
        20 if issue.severity == "blocking" else 5 if issue.severity == "warning" else 1
        for issue in rule_issues
    )
    rule_score = max(Decimal("0"), Decimal("100") - Decimal(deterministic_penalty))
    # Score what was actually checked. When the model review could not run, the
    # old code substituted a flat 70, so a structurally clean resume was branded
    # mediocre by a request that never happened, and with a pass mark of 90 it
    # could never be finalized. An unavailable review is an unknown, not a
    # verdict: fall back to the deterministic checks and let the warning above
    # tell the user the AI half is missing.
    score = (
        min(Decimal(str(model_review.score)), rule_score)
        if model_review is not None
        else rule_score
    ).quantize(Decimal("0.1"))
    passed = score >= PASS_SCORE and not any(issue.severity == "blocking" for issue in issues)
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
        ),
        rendered.bytes_,
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
        f"VERIFIED FACTS:\n{json.dumps(verified_facts, ensure_ascii=False)[:18000]}\n\n"
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
        response = await client.messages.create(
            model=settings.anthropic_model_tailor,
            max_tokens=7000,
            system=CAREER_OPS_RULES,
            messages=messages,
            extra_headers={"x-manifest-tier": settings.manifest_tier_sonnet},
        )
        return response_text(response)

    raw = await ask()
    try:
        output = parse_model_json(RevisionOutput, raw)
    except ValidationError:
        # A chatty reply is recoverable, so show the model its own output and
        # ask once more for the object alone rather than failing the edit.
        log.warning("resume.revision_not_json", preview=raw[:300])
        messages = [
            *messages,
            {"role": "assistant", "content": raw[:4000] or "(empty)"},
            {"role": "user", "content": JSON_ONLY_RETRY},
        ]
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
    unsupported = sorted(new_numbers - source_numbers)
    if unsupported:
        raise ValueError(
            "The requested edit introduced unverified metrics: "
            + ", ".join(unsupported)
            + ". Add them as verified Profile facts first."
        )
    return output


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
        start = esc(item.get("startDate"))
        end = esc(item.get("endDate") or "Present")
        return f"{start} -- {end}" if start else end

    basics = doc.get("basics") or {}
    location = basics.get("location") or {}
    contact = [
        ", ".join(filter(None, [location.get("city"), location.get("region")])),
        basics.get("phone"),
        basics.get("email"),
    ]
    profiles = basics.get("profiles") or []
    if profiles:
        contact.append(profiles[0].get("url"))
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
