"""Structured JD extraction via the configured Anthropic-compatible endpoint.

Model is whatever `settings.anthropic_model_extract` points to — defaults to
`manifest/auto` so the Manifest gateway picks the real model. Returns a
Pydantic-validated dict; never invents the company name (callers fall back to URL hint).
"""
from __future__ import annotations

from typing import Literal

import anthropic
import structlog
from pydantic import BaseModel, Field, ValidationError

from job_os.services.llm_json import create_message, response_text
from job_os.settings import get_settings

log = structlog.get_logger(__name__)


class ParsedJD(BaseModel):
    title: str | None = None
    company: str | None = None
    company_domain: str | None = None
    level: Literal["intern", "new-grad", "mid", "senior", "staff", "unknown"] | None = None
    function: Literal[
        "swe", "ml", "ai", "data", "research", "sre", "infra", "security", "pm", "design", "other"
    ] | None = None
    location: str | None = None
    remote: Literal["onsite", "hybrid", "remote", "unknown"] | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    salary_currency: str | None = None
    required_skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    responsibilities: list[str] = Field(default_factory=list)
    qualifications: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    sponsorship: str | None = None
    years_experience: str | None = None


SYSTEM_PROMPT = (
    "You extract structured job description fields. "
    "Return ONLY valid JSON matching the schema. "
    "Never invent values — leave a field null or empty if the JD doesn't state it. "
    "Use the exact phrasing from the JD for skills/technologies (do not paraphrase). "
    "When the JD asks for one or more of a list ('one or more of Go, Node.js or "
    "Python', 'proficiency in at least one of the following languages: ...'), "
    "keep that WHOLE list as a single required_skills/qualifications entry, "
    "written out with 'or' between the items, exactly as the JD phrases it. Do "
    "not split it into separate entries: a candidate meeting any one of them "
    "meets the requirement, and splitting the list is what scores that "
    "candidate as missing the other eight languages they were never asked for. "
    "Do not extract an internal team, product, or organization name (e.g. "
    "'you'll work with our Infra and Foundational AI teams') into "
    "required_skills, technologies, or keywords — a resume can never state a "
    "team name it has never heard of, so counting one as a missing skill "
    "penalizes every candidate for not having read the org chart."
)


async def parse_jd(jd_text: str, *, title_hint: str | None = None) -> dict:
    settings = get_settings()
    if not settings.anthropic_api_key:
        log.warning("jd_parse.no_anthropic_key")
        return {"title": title_hint} if title_hint else {}

    # The SDK's own default timeout is ten minutes, which is fine for a
    # background pass but not for a request a user is sitting in front of.
    # A structured-output call over one job description has no business
    # taking longer than this even on a slow day.
    client = anthropic.AsyncAnthropic(
        auth_token=settings.anthropic_api_key,
        base_url=settings.anthropic_base_url or None,
        timeout=30.0,
    )

    user_prompt = (
        "Extract structured fields from this job description. "
        f"Hint — page title: {title_hint!r}.\n\n"
        f"<jd>\n{jd_text[:18000]}\n</jd>\n\n"
        "Respond with a single JSON object matching this schema:\n"
        f"{ParsedJD.model_json_schema()}"
    )

    try:
        msg = await create_message(
            client,
            model=settings.anthropic_model_extract,
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
            extra_headers={"x-manifest-tier": settings.manifest_tier_fast},
        )
    except anthropic.APIError as exc:
        # Already retried, and tried the fallback provider, inside
        # create_message. A job can still be added without structured JD
        # fields -- they just don't get filled in -- so this degrades the
        # same way the no-key branch above does, rather than failing the
        # add-job request outright over an extraction step.
        log.warning("jd_parse.gateway_failed", error=str(exc)[:300])
        return {"title": title_hint} if title_hint else {}

    text = response_text(msg)
    raw = _strip_json_fence(text)
    try:
        return ParsedJD.model_validate_json(raw).model_dump(exclude_none=False)
    except ValidationError as e:
        log.warning("jd_parse.invalid_json", error=str(e), preview=raw[:300])
        return {"title": title_hint} if title_hint else {}


def _strip_json_fence(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        # remove ```json ... ``` fences
        first_nl = t.find("\n")
        t = t[first_nl + 1 :] if first_nl != -1 else t
        if t.endswith("```"):
            t = t[: -3]
    return t.strip()
