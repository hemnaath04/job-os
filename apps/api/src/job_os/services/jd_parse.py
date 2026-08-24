"""Structured JD extraction via the configured Anthropic-compatible endpoint.

Model is whatever `settings.anthropic_model_extract` points to — defaults to
`manifest/auto` so the Manifest gateway picks the real model. Returns a
Pydantic-validated dict; never invents the company name (callers fall back to URL hint).
"""
from __future__ import annotations

import asyncio
from typing import Literal

import anthropic
import httpx
import structlog
from pydantic import BaseModel, Field, ValidationError

from job_os.services.llm_json import create_message, response_text
from job_os.settings import get_settings

log = structlog.get_logger(__name__)

# `create_message`'s own retry ladder deliberately excludes timeouts (see
# llm_json._NON_RETRYABLE_TRANSPORT_ERRORS): a timeout means the gateway
# stopped answering rather than the connection glitching, and retrying one
# there would trade a clean failure for a run that could exceed a caller's own
# deadline. That reasoning holds for tailoring, which is already a multi-
# minute background job with nothing waiting on one extra try. A JD parse is
# different: it blocks add-job-from-url/text, which a user IS sitting in
# front of, and one retry with a short, fixed backoff is a small, bounded cost
# against a real failure rate a normal-length JD hit 3/3 in practice. Scoped
# to this one call, not the shared retry ladder: raising a caller's own
# tolerance for a timeout is that caller's call to make, not a change to what
# every other agent in this codebase considers safe to retry.
_JD_PARSE_TIMEOUT_ERRORS = (anthropic.APITimeoutError, httpx.TimeoutException)
_JD_PARSE_RETRY_DELAY_SECONDS = 2.0
# Indirected for the same reason llm_json._sleep is: a test can shorten this
# without patching asyncio.sleep globally, which would slow down every other
# test that happens to await something in the same process.
_sleep = asyncio.sleep


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
    # True only on the degraded path: the extraction call failed (timeout,
    # gateway error) or came back as invalid JSON, so every list field below
    # is empty for lack of an answer, not because the JD stated none of them.
    # _compute_ats_from_document reads this to tell "we could not check" apart
    # from "this job genuinely asks for nothing scoreable" -- both currently
    # read as zero requirements, and only one of those is a real 0% match.
    parse_incomplete: bool = False


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


def _incomplete(title_hint: str | None) -> dict:
    """The degraded return every failure path below shares.

    Not just `{"title": title_hint}`: that shape is indistinguishable from a
    JD that genuinely named nothing, which is what let a timed-out parse
    reach the scorer as a confident 0% Keyword Match instead of an honest
    "we don't know."
    """
    result: dict = {"parse_incomplete": True}
    if title_hint:
        result["title"] = title_hint
    return result


async def parse_jd(jd_text: str, *, title_hint: str | None = None) -> dict:
    settings = get_settings()
    if not settings.anthropic_api_key:
        log.warning("jd_parse.no_anthropic_key")
        return _incomplete(title_hint)

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

    msg = None
    for attempt in (1, 2):
        try:
            msg = await create_message(
                client,
                model=settings.anthropic_model_extract,
                max_tokens=2048,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
                # temperature=0 was here for reproducibility (same JD -> same
                # required_skills -> same ats_score) but is reverted for now:
                # Anthropic's SDK v1.0.0 (2026-08-20) drops temperature/top_p/
                # top_k from Messages methods entirely, the Appwrite function
                # this runs in resolves anthropic unpinned above 1.0 on its
                # next rebuild (requirements.txt: anthropic>=0.40.0, no
                # ceiling), and a rebuild already broke the same kwarg on the
                # compose call. Re-add once that pin is capped below 1.0 and
                # redeployed.
                extra_headers={"x-manifest-tier": settings.manifest_tier_fast},
            )
            break
        except _JD_PARSE_TIMEOUT_ERRORS as exc:
            if attempt == 1:
                log.warning("jd_parse.timeout_retrying", error=str(exc)[:300])
                await _sleep(_JD_PARSE_RETRY_DELAY_SECONDS)
                continue
            log.warning("jd_parse.gateway_failed", error=str(exc)[:300])
            return _incomplete(title_hint)
        except anthropic.APIError as exc:
            # Already retried, and tried the fallback provider, inside
            # create_message. A job can still be added without structured JD
            # fields -- they just don't get filled in -- so this degrades the
            # same way the no-key branch above does, rather than failing the
            # add-job request outright over an extraction step.
            log.warning("jd_parse.gateway_failed", error=str(exc)[:300])
            return _incomplete(title_hint)
    assert msg is not None  # every loop exit above either breaks with msg set or returns

    text = response_text(msg)
    raw = _strip_json_fence(text)
    try:
        return ParsedJD.model_validate_json(raw).model_dump(exclude_none=False)
    except ValidationError as e:
        log.warning("jd_parse.invalid_json", error=str(e), preview=raw[:300])
        return _incomplete(title_hint)


def _strip_json_fence(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        # remove ```json ... ``` fences
        first_nl = t.find("\n")
        t = t[first_nl + 1 :] if first_nl != -1 else t
        if t.endswith("```"):
            t = t[: -3]
    return t.strip()
