"""Structured JD extraction via the configured Anthropic-compatible endpoint.

Model is whatever `settings.anthropic_model_extract` points to — defaults to
`manifest/auto` so the Manifest gateway picks the real model. Returns a
Pydantic-validated dict; never invents the company name (callers fall back to URL hint).
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Literal

import anthropic
import httpx
import structlog
from pydantic import BaseModel, Field, ValidationError

from job_os.services.llm_json import (
    complete_json_via_openrouter,
    create_message,
    response_text,
)
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
_JD_PARSE_TIMEOUT_ERRORS = (
    anthropic.APITimeoutError,
    httpx.TimeoutException,
    # asyncio.wait_for raises the builtin TimeoutError on 3.11+, and the bound
    # below is what usually fires rather than the client's own clock.
    TimeoutError,
)

# Budget for the WHOLE call, retry included, not per attempt.
#
# The retry above had no relationship to the time its caller was willing to
# wait. The client allowed each attempt 30s while `/jobs/parse-description`
# allows the whole thing 27s, so one attempt could consume the caller's entire
# budget and a second could never finish. In production that turned a fast,
# honest empty parse into a 27 second wait for the same empty parse: the
# gateway answered 200 in about three seconds, the reply was unusable, the
# retry started, and the caller's deadline killed it 24 seconds later.
#
# Set below the tightest caller budget so this fires first and reports what
# happened, leaving the caller's own timeout as a backstop rather than the
# thing that ends the request.
_JD_PARSE_DEADLINE_SECONDS = 25.0

_JD_PARSE_RETRY_DELAY_SECONDS = 2.0

# Under this there is not enough time left for another attempt to land, so
# starting one only delays an answer that is already decided.
_JD_PARSE_MIN_ATTEMPT_SECONDS = 6.0


# Measured against the live gateway: a healthy parse of a full-length internship
# JD returns in about five seconds using 508 to 555 output tokens, so 2048 was
# four times what the answer needs and still was not enough. In a degraded
# window the tier answers slowly and spends the budget before finishing the
# JSON, which arrives cut off mid-value or empty, and 2048 is what it runs out
# of. The extra headroom costs nothing on the normal path, since billing is on
# tokens produced rather than the ceiling.
_JD_PARSE_MAX_TOKENS = 4096
# Indirected for the same reason llm_json._sleep is: a test can shorten this
# without patching asyncio.sleep globally, which would slow down every other
# test that happens to await something in the same process.
_sleep = asyncio.sleep
# Same indirection, so a test can drive the deadline without waiting on it.
_monotonic = time.monotonic


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


def _first_attempt_seconds(remaining: float) -> float:
    """Half of what is left, less the backoff, rather than all of it.

    The first attempt used to get `remaining`, which on the first pass is the
    entire budget, so a timeout consumed every second there was and the retry
    could never run: the second attempt opened past the deadline and returned
    at the out_of_time guard. Seen in production on 2026-08-27 (request
    5df6920c), where attempt 1 spent the full 25s, the 2s backoff followed, and
    attempt 2 began at remaining=-2.0, leaving the job saved as "Untitled" with
    nothing parsed. The retry was reachable only for a reply that came back
    fast and unusable, never for the slow gateway it was added to survive.

    Halving rather than taking a smaller slice: a healthy parse returns in
    about five seconds, so half of the standard budget is already twice what
    the answer needs, and an attempt cut short before the gateway would have
    answered spends time without learning anything. The floor matters for the
    same reason, and keeps a caller that hands down a short budget from
    splitting it into two attempts that neither of them can land.
    """
    return max(
        _JD_PARSE_MIN_ATTEMPT_SECONDS,
        (remaining - _JD_PARSE_RETRY_DELAY_SECONDS) / 2,
    )


# The fields that carry something a resume can be measured against. Title,
# level, function, company and location are page metadata: they are read off a
# heading or a URL and say nothing about what the job asks for.
_REQUIREMENT_BEARING_FIELDS = (
    "required_skills",
    "preferred_skills",
    "technologies",
    "responsibilities",
    "qualifications",
    "keywords",
)


def _extracted_nothing(parsed: ParsedJD) -> bool:
    """True when a valid reply named nothing a resume could be measured against.

    Every field on ParsedJD is optional with a default, so a bare `{}` from the
    gateway validates cleanly and dumps to all-empty with parse_incomplete
    False: indistinguishable from a genuine parse of a JD that stated no
    requirements. That is the same confusion _incomplete exists to prevent,
    reached through the one door it does not cover.

    This used to ask whether the reply named ANY field, which let the failure
    through the door it was built to close. A title is the one thing the call
    gets for free: it is handed in as `title_hint`, and it is what a model
    returns when it could not read the body. Six real postings are stored with
    a title, sometimes a seniority and a location, and not one skill,
    technology, qualification or responsibility between them, each recorded as
    a successful parse. Among them are a Disney error page, a Greenhouse
    applications dashboard, a 139-character Tesla stub, and, less forgivably,
    NVIDIA's and Millennium's real postings with 7KB and 15KB of description
    sitting in `jd_clean` unread.

    So the question is now whether anything scoreable came back, not whether
    anything came back. In the case where a posting really does state nothing
    scoreable, "we could not read it" remains the honest report: both leave the
    scorer with no requirements, and only one of them is a true 0% match.
    """
    return not any(getattr(parsed, field) for field in _REQUIREMENT_BEARING_FIELDS)


def _user_prompt_for(jd_text: str, title_hint: str | None) -> str:
    """The extraction prompt, shared by both sides of the race.

    Built once so the two providers are answering the same question. A prompt
    that drifted between them would make the race a coin toss between two
    different extractions rather than two attempts at one.
    """
    return (
        "Extract structured fields from this job description. "
        f"Hint. Page title: {title_hint!r}.\n\n"
        f"<jd>\n{jd_text[:18000]}\n</jd>\n\n"
        "Respond with a single JSON object matching this schema:\n"
        f"{ParsedJD.model_json_schema()}"
    )


async def _parse_via_gateway(
    jd_text: str,
    *,
    title_hint: str | None = None,
    deadline_seconds: float | None = None,
) -> dict[str, Any]:
    """The primary path, unchanged: Manifest, with its own retry and deadline."""
    settings = get_settings()
    if not settings.anthropic_api_key:
        log.warning("jd_parse.no_anthropic_key")
        return _incomplete(title_hint)

    # The SDK's own default timeout is ten minutes, which is fine for a
    # background pass but not for a request a user is sitting in front of.
    # A structured-output call over one job description has no business
    # taking longer than this even on a slow day.
    budget = deadline_seconds or _JD_PARSE_DEADLINE_SECONDS
    deadline = _monotonic() + budget
    client = anthropic.AsyncAnthropic(
        auth_token=settings.anthropic_api_key,
        base_url=settings.anthropic_base_url or None,
        # Was a flat 30s, which is longer than any caller waits. Each attempt
        # is bounded by what is actually left below; this is only the ceiling.
        timeout=budget,
    )

    user_prompt = _user_prompt_for(jd_text, title_hint)

    for attempt in (1, 2):
        remaining = deadline - _monotonic()
        if remaining < _JD_PARSE_MIN_ATTEMPT_SECONDS:
            # Starting an attempt that cannot finish only delays an answer that
            # is already decided.
            log.warning(
                "jd_parse.out_of_time", attempt=attempt, remaining=round(remaining, 1)
            )
            return _incomplete(title_hint)
        attempt_seconds = (
            _first_attempt_seconds(remaining) if attempt == 1 else remaining
        )
        try:
            # wait_for around the whole call, not just the HTTP timeout:
            # create_message streams and runs its own retry schedule for a
            # rate-limited gateway, so a per-request timeout alone does not
            # bound how long this can take.
            msg = await asyncio.wait_for(
                create_message(
                    client,
                    model=settings.anthropic_model_extract,
                    max_tokens=_JD_PARSE_MAX_TOKENS,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": user_prompt}],
                    # The reply is fed straight to `ParsedJD.model_validate_json`,
                    # so a fallback that answers in prose is the same as one that
                    # did not answer. Measured: without this, the free models
                    # returned unparseable JSON on nearly every attempt.
                    fallback_json=True,
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
                ),
                timeout=attempt_seconds,
            )
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

        # Parsing lives inside the loop so that an answer which arrived but is
        # unusable gets the same second chance a timeout already got. It used to
        # sit after it, so a reply cut off mid-value was final: one truncated
        # JSON object and the job kept an empty parse for good, with the user
        # told only that no details could be read. Observed live three times in
        # a row and then not at all across eleven consecutive runs on the same
        # key and model, so it is a window rather than a property of the input,
        # which is exactly the shape a retry answers.
        text = response_text(msg)
        raw = _strip_json_fence(text)
        try:
            parsed = ParsedJD.model_validate_json(raw)
        except ValidationError as e:
            if attempt == 1:
                log.warning(
                    "jd_parse.invalid_json_retrying",
                    error=str(e)[:300],
                    # stop_reason and output_tokens are what distinguish "the
                    # model rambled past the ceiling" from "the gateway sent
                    # prose": worth having in the log so the next person can
                    # tell which without reproducing it.
                    stop_reason=getattr(msg, "stop_reason", None),
                    output_tokens=getattr(getattr(msg, "usage", None), "output_tokens", None),
                    preview=raw[:300],
                )
                # No sleep. Unlike a timeout, nothing here is rate limited, and
                # the callers are interactive requests inside Heroku's 30s
                # ceiling: two seconds of waiting is two seconds the retry does
                # not get.
                continue
            log.warning("jd_parse.invalid_json", error=str(e), preview=raw[:300])
            return _incomplete(title_hint)

        # Valid JSON is not the same as an answer. Retried on the first pass for
        # the same reason a truncated reply is: it costs one call to find out
        # whether the empty answer was the window or the input.
        if _extracted_nothing(parsed):
            if attempt == 1:
                log.warning("jd_parse.empty_retrying", preview=raw[:300])
                continue
            log.warning("jd_parse.empty", preview=raw[:300])
            # Flagged, but not blanked. `_incomplete` returns the title and
            # nothing else, and a reply that read no requirements has often
            # still read the company, the location and the seniority correctly
            # off the page: Millennium's came back with "Millennium", "Miami,
            # Florida" and "intern" attached to no skills at all. Those are
            # what the board shows on the card, so discarding them to report
            # the parse honestly would fix the score by breaking the display.
            degraded = parsed.model_dump(exclude_none=False)
            degraded["parse_incomplete"] = True
            if title_hint and not degraded.get("title"):
                degraded["title"] = title_hint
            return degraded

        return parsed.model_dump(exclude_none=False)

    return _incomplete(title_hint)  # pragma: no cover - the loop returns on every path


async def _parse_via_free_model(
    jd_text: str, title_hint: str | None
) -> dict[str, object] | None:
    """The same extraction, asked of the free model ladder directly.

    Returns None rather than a degraded document: this is one side of a race,
    and a loser has nothing to say. The gateway side is what degrades honestly.
    """
    text = await complete_json_via_openrouter(
        system=SYSTEM_PROMPT,
        user=_user_prompt_for(jd_text, title_hint),
        max_tokens=_JD_PARSE_MAX_TOKENS,
    )
    if not text:
        return None
    try:
        parsed = ParsedJD.model_validate_json(_strip_json_fence(text))
    except ValidationError as exc:
        log.info("jd_parse.free_model_invalid", error=str(exc)[:200])
        return None
    if _extracted_nothing(parsed):
        return None
    return parsed.model_dump(exclude_none=False)


async def parse_jd(
    jd_text: str,
    *,
    title_hint: str | None = None,
    deadline_seconds: float | None = None,
) -> dict:
    """Extract the JD, taking whichever provider answers usefully first.

    The primary gateway and the free model ladder are asked at the same time
    and the first usable answer wins. This is a latency change, not a quality
    one, and it is measured rather than assumed. Twelve real postings from this
    workspace, 16KB to 30KB each, same schema, same prompt:

                        median    p90     max    usable
        Manifest         15.6s   22.4s   79.9s    10/12
        free ladder       5.1s   11.3s   13.1s    12/12

    Three times faster at the median, and the tail is the part that matters:
    the interactive budget is 25 seconds, the gateway's p90 was 22.4s against
    it, and one posting took 79.9s after a timeout and a retry. The free rung
    did not come close to the deadline on any of the twelve, and returned at
    least as many fields.

    Raced rather than tried in order, because the free tier is rate limited and
    answers 429 in about a tenth of a second when it is busy. Starting the
    gateway only after that would add its full latency back on exactly the runs
    that are already unlucky. Racing costs one extra request and no money.

    The gateway still runs on every parse and is still the one that degrades
    honestly, so nothing here can turn a parse into a failure that would
    otherwise have succeeded: if the free side loses, is invalid, or returns
    nothing scoreable, the result is exactly what it was before this existed.
    """
    settings = get_settings()
    primary = asyncio.ensure_future(
        _parse_via_gateway(
            jd_text, title_hint=title_hint, deadline_seconds=deadline_seconds
        )
    )
    if not settings.openrouter_api_key:
        return await primary

    free = asyncio.ensure_future(_parse_via_free_model(jd_text, title_hint))
    pending = {primary, free}
    try:
        while pending:
            done, pending = await asyncio.wait(
                pending, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                try:
                    if task is free:
                        from_free = free.result()
                        if from_free is not None:
                            log.info("jd_parse.free_model_won")
                            return from_free
                        # Nothing usable from the free side. The gateway is
                        # still running and is the answer.
                        continue
                    # The gateway answered. It is authoritative even when
                    # degraded, because it is the side that knows how to say
                    # "we could not read this".
                    return primary.result()
                except Exception:
                    # The gateway's own failures are handled inside it, so
                    # anything raising here is the free side. Let the other
                    # finish rather than surfacing a racer's error.
                    log.info("jd_parse.racer_failed", side="free", exc_info=True)
                    continue
    finally:
        for task in (primary, free):
            if not task.done():
                task.cancel()
    return _incomplete(title_hint)  # pragma: no cover - the loop returns first


def _strip_json_fence(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        # remove ```json ... ``` fences
        first_nl = t.find("\n")
        t = t[first_nl + 1 :] if first_nl != -1 else t
        if t.endswith("```"):
            t = t[: -3]
    return t.strip()
