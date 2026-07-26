"""Verified resume editing, review, and export support.

JSON Resume is the canonical editable document. PDF and LaTeX are generated
artifacts. Every AI revision is followed by a separate quality-model review
plus deterministic PDF checks before it can be finalized.
"""
from __future__ import annotations

import io
import json
import re
from decimal import Decimal
from typing import Any

import anthropic
import httpx
import structlog
from pydantic import BaseModel, Field, ValidationError
from pypdf import PdfReader

from job_os.schemas.resumes import ResumeReviewIssue, ResumeReviewResult
from job_os.services.jd_parse import _strip_json_fence
from job_os.services.pdf_render import render_resume_pdf
from job_os.settings import get_settings

log = structlog.get_logger(__name__)

PASS_SCORE = Decimal("90")
GITHUB_RE = re.compile(r"https?://(?:www\.)?github\.com/([^/\s]+)/([^/#?\s]+)", re.I)
NUMBER_RE = re.compile(
    r"(?<!\w)(?:\$?\d[\d,.]*%?|\d+\s?(?:ms|s|sec|min|hours?|days?|x))(?!\w)",
    re.I,
)
BANNED_PHRASES = {
    "leveraged",
    "utilized",
    "spearheaded",
    "cutting-edge",
    "state-of-the-art",
    "innovative solution",
    "robust architecture",
    "seamlessly",
    "end-to-end solution",
    "synergized",
    "revolutionized",
    "transformed",
    "facilitated",
    "enabled",
}
KNOWN_GITHUB_REPOS = {
    "bedrocked": ("hemnaath04", "bedrocked"),
    "claimfarm": ("hemnaath04", "claimfarm"),
    "hackradar": ("hemnaath04", "hackradar"),
    "job os": ("hemnaath04", "job-os"),
    "job-os": ("hemnaath04", "job-os"),
    "job searcher": ("hemnaath04", "job-searcher"),
    "repository learning builder": ("hemnaath04", "repo-learning-builder"),
    "role reveal": ("hemnaath04", "rolereveal"),
    "rolereveal": ("hemnaath04", "rolereveal"),
}

CAREER_OPS_RULES = """\
You are the quality gate for Hemnaath Balasubramani's resume engine.

Non-negotiable rules:
- Never invent employers, dates, grades, metrics, technologies, coursework,
  responsibilities, credentials, or outcomes.
- He targets AI/ML, backend/systems, agentic AI/LLM, and test automation.
  Do not frame him as a frontend engineer. React and Next.js may appear only
  as project context; emphasize the backend, proxies, agents, APIs, pipelines,
  concurrency, testing, and infrastructure he actually owns.
- One page, single column, ATS-safe, Times-style, one-line contact, selectable
  text, ordinary headings, thin rules, and no tables, icons, graphics, or
  multi-column layouts.
- Keep education precise. Northeastern MS CS is Jan 2026 to May 2028. Fall
  2026 courses are registered, not completed. Do not show GPA 3.334 unless it
  clearly helps.
- EPAM Systems is the only professional experience. Everything else is a
  project.
- Use evidence from the canonical profile and current GitHub README text.
- Use short, engineer-like sentences. No em dashes in prose and no inflated
  marketing language.
- A project bullet that is not supported by its README or verified profile is
  blocking. Missing JD evidence becomes a suggestion or gap, not a claim.
"""


class ModelReviewIssue(BaseModel):
    severity: str
    code: str
    message: str
    section: str | None = None


class ModelReview(BaseModel):
    score: int = Field(ge=0, le=100)
    issues: list[ModelReviewIssue] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    summary: str = ""


class RevisionOutput(BaseModel):
    assistant_message: str
    suggestions: list[str] = Field(default_factory=list)
    json_resume: dict[str, Any]


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


async def load_github_context(doc: dict[str, Any]) -> tuple[dict[str, str], list[str]]:
    """Fetch current public README text for projects included in this resume."""
    repos: dict[str, tuple[str, str]] = {}
    for project in doc.get("projects", []) or []:
        project_name = str(project.get("name") or "").strip()
        parsed = _repo_from_url(project.get("url"))
        if not parsed:
            normalized = re.sub(r"[^a-z0-9]+", " ", project_name.lower()).strip()
            parsed = KNOWN_GITHUB_REPOS.get(normalized)
        if parsed:
            repos[project_name or parsed[1]] = parsed

    contexts: dict[str, str] = {}
    checked: list[str] = []
    if not repos:
        return contexts, checked

    headers = {
        "Accept": "application/vnd.github.raw+json",
        "User-Agent": "job-os-resume-verifier",
    }
    async with httpx.AsyncClient(timeout=8, follow_redirects=True, headers=headers) as client:
        for project_name, (owner, repo) in repos.items():
            try:
                response = await client.get(
                    f"https://api.github.com/repos/{owner}/{repo}/readme"
                )
                if response.status_code == 200:
                    contexts[project_name] = response.text[:12000]
                    checked.append(f"{owner}/{repo}")
            except httpx.HTTPError as exc:
                log.info("resume.github_readme_unavailable", repo=f"{owner}/{repo}", error=str(exc))
    return contexts, checked


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
    if "—" in text or "--" in text:
        issues.append(
            ResumeReviewIssue(
                severity="warning",
                code="prose_dash",
                message="Replace em dashes or double hyphens in prose with simpler punctuation.",
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
    return issues, page_count, text_selectable


async def review_resume(doc: dict[str, Any]) -> tuple[ResumeReviewResult, bytes]:
    """Render, inspect, then run an independent quality-model review."""
    rendered = render_resume_pdf(doc)
    rule_issues, page_count, text_selectable = deterministic_review(doc, rendered.bytes_)
    github_context, checked = await load_github_context(doc)

    settings = get_settings()
    model_review = ModelReview(score=70, summary="Model review unavailable.")
    try:
        response = await _client().messages.create(
            model=settings.anthropic_model_verify,
            max_tokens=3000,
            system=CAREER_OPS_RULES,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Review this JSON Resume after drafting. Return one JSON object with "
                        "score (0-100), issues, strengths, and summary. Issue severity must be "
                        "blocking, warning, or suggestion. Be strict about truth, readability, "
                        "project evidence, one-page relevance, and backend/AI positioning.\n\n"
                        f"RESUME:\n{json.dumps(doc, ensure_ascii=False)[:24000]}\n\n"
                        "CURRENT GITHUB README EVIDENCE:\n"
                        f"{json.dumps(github_context, ensure_ascii=False)[:22000]}\n\n"
                        f"SCHEMA:\n{json.dumps(ModelReview.model_json_schema())}"
                    ),
                }
            ],
            extra_headers={"x-manifest-tier": settings.manifest_tier_quality},
        )
        raw = "".join(block.text for block in response.content if block.type == "text")
        model_review = ModelReview.model_validate_json(_strip_json_fence(raw))
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
            for issue in model_review.issues
        ],
    ]
    deterministic_penalty = sum(
        20 if issue.severity == "blocking" else 5 if issue.severity == "warning" else 1
        for issue in rule_issues
    )
    score = min(
        Decimal(str(model_review.score)),
        max(Decimal("0"), Decimal("100") - Decimal(deterministic_penalty)),
    ).quantize(Decimal("0.1"))
    passed = score >= PASS_SCORE and not any(issue.severity == "blocking" for issue in issues)
    return (
        ResumeReviewResult(
            score=score,
            passed=passed,
            page_count=page_count,
            text_selectable=text_selectable,
            issues=issues,
            strengths=model_review.strengths,
            github_projects_checked=checked,
            model_summary=model_review.summary,
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
    github_context, _checked = await load_github_context(doc)
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
        f"OUTPUT SCHEMA:\n{json.dumps(RevisionOutput.model_json_schema())}"
    )
    response = await _client().messages.create(
        model=settings.anthropic_model_tailor,
        max_tokens=7000,
        system=CAREER_OPS_RULES,
        messages=[{"role": "user", "content": prompt}],
        extra_headers={"x-manifest-tier": settings.manifest_tier_quality},
    )
    raw = "".join(block.text for block in response.content if block.type == "text")
    output = RevisionOutput.model_validate_json(_strip_json_fence(raw))

    # Defense in depth: new metrics must already exist in verified facts or
    # the current resume. Chat remains an editor, not a fact-creation path.
    source_numbers = set(
        NUMBER_RE.findall(
            _resume_text(doc) + "\n" + json.dumps(verified_facts, ensure_ascii=False)
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
