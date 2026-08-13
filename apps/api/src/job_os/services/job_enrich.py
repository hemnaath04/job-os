"""One LLM pass per job at ingest, producing the precomputed match fields.

This is the expensive half of the split described in `schemas/enrichment.py`:
enrichment runs once per job and costs a model call, matching runs per user per
job and costs nothing. Everything user-independent belongs here, so that nothing
user-dependent needs a model at all.

Reuses the gateway plumbing the rest of this codebase already goes through
(`services/llm_json.py`) rather than opening a second path to the same endpoint.
That is not tidiness: `create_message` is where the retry schedule for a
rate-limited or mid-stream-dropped Manifest call lives, and a parallel client
would silently lack all of it.

Failure policy: a job that cannot be enriched still has to reach the index. A
posting with unparseable compensation is a posting with no compensation figures
and every other field intact, never a dropped row. Every partial is recorded in
`extraction_gaps` so the gap is visible instead of looking like a job that
genuinely said nothing.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import anthropic
import structlog
from pydantic import ValidationError

from job_os.schemas.enrichment import (
    ENRICHMENT_SCHEMA_VERSION,
    Commitment,
    Compensation,
    JobEnrichment,
    to_yearly,
)
from job_os.services.llm_json import (
    JSON_ONLY_RETRY,
    create_message,
    extract_json_object,
    response_diagnostics,
    response_text,
)
from job_os.settings import get_settings

log = structlog.get_logger(__name__)

# Where the enrichment document lives on the existing `Job.jd_parsed` JSONB
# column. Storing under a key rather than in new columns means no migration and
# no downtime to start writing, and it keeps `jd_parsed`'s existing contents
# (what `jd_parse.py` wrote at import) readable side by side instead of
# overwritten. Fields that earn an index can be promoted to real columns later,
# with production evidence about which ones those are.
ENRICHMENT_KEY = "enrichment"

# Long enough for the whole document on a dense senior JD (the Cisco posting in
# the evidence set atomizes to 48 skills across must-have and preferred), short
# enough that a runaway reply fails fast instead of billing for prose.
MAX_TOKENS = 4096

# The posting text handed to the model. `jd_parse.py` uses 18000 for a much
# smaller output schema; the same ceiling holds here because JD length past this
# point is almost always boilerplate (benefits, EEO statements, legal) that this
# schema deliberately does not extract.
MAX_JD_CHARS = 18000

SYSTEM_PROMPT = """\
You extract structured, factual fields from one job posting. Your output is \
stored once and then used to match many different candidates against this job, \
so it must describe the JOB only. Never reason about any particular candidate.

Rules, in order of importance:

1. Extract, never infer. If the posting does not state something, say so with \
the schema's own "not mentioned" value. Do not fill a plausible guess. A wrong \
value is worse than an absent one, because an absent one is visibly absent.

2. "Not mentioned" and "no" are different answers. A posting that never \
discusses visa sponsorship is not a posting that refuses it. Use \
"not-mentioned" for silence and "no" only when the posting actually rules it \
out (for example "we are unable to sponsor" or "must be authorized to work \
without sponsorship").

3. Use the posting's own wording for skill names. Do not translate "K8s" to \
"Kubernetes" or expand abbreviations. Normalization happens after you, in code, \
and it works better when it can see what the posting actually said.

4. Split requirements into the atomic skills a matcher can compare, and ALSO \
keep them as the sentences a person can read. Both are required outputs. In \
requirements_prose, keep each bullet as one whole requirement sentence, \
including qualifiers like "or equivalent experience" and "such as AWS, Azure, \
or GCP". In skills, split that same sentence into its separate testable parts.

5. Importance is about this role, not about the industry. Use 3 when the role is \
defined by the skill and a candidate lacking it could not do the job, 2 when the \
posting names it as a real requirement, and 1 when it is mentioned in passing or \
listed among many alternatives. Most skills are 2. Reserve 3 for a handful.

6. necessity is "required" for anything in the must-have or minimum \
qualifications, and "preferred" for anything under preferred, nice to have, \
bonus, or plus. When the posting does not separate the two, use "required" for \
the qualifications section and "preferred" for skills that appear only in the \
responsibilities or the company blurb.

7. For compensation, report ONLY the figure the posting states, in the frequency \
the posting states it. Do not convert between frequencies and do not annualize. \
Code does that afterwards, exactly. If the posting gives a single figure rather \
than a range, put it in both min and max. If it gives no figure, leave the \
compensation fields null and set is_transparent to false.

8. Degrees are recorded per level, not as a single minimum. A posting asking for \
"a BS, and currently enrolled in an MS or PhD" marks bachelors required AND \
masters required AND doctorate required, because that is what it says. Set \
enrolled_student_ok to true when the posting addresses current students, for \
example "currently pursuing", "graduating in", or "must be enrolled".

9. years of experience: set years_experience_mentioned to true only when the \
posting states a number. "Entry level" is a seniority, not a number of years.

Output one raw JSON object and nothing else. No markdown fence, no commentary.\
"""


def _user_prompt(
    jd_text: str,
    *,
    title_hint: str | None,
    company_hint: str | None,
) -> str:
    """The extraction request, with the schema attached.

    Hints are labelled as hints rather than facts because a page title is
    frequently wrong ("Careers at Foo | Jobs") and the posting body is the
    authority. Naming them as hints and telling the model to prefer the body is
    what stops a bad `<title>` becoming a bad `core_job_title` on the card.
    """
    return (
        "Extract the fields below from this job posting.\n\n"
        f"Hints from the page these may be wrong, prefer the posting body:\n"
        f"  page title: {title_hint!r}\n"
        f"  company: {company_hint!r}\n\n"
        f"<posting>\n{jd_text[:MAX_JD_CHARS]}\n</posting>\n\n"
        "Respond with one raw JSON object matching this JSON Schema. Omit any "
        "field the posting does not support rather than inventing a value; every "
        "field has a safe default.\n"
        f"{json.dumps(_prompt_schema(), separators=(',', ':'))}"
    )


def _prompt_schema() -> dict[str, Any]:
    """The schema as shown to the model, minus the fields code owns.

    `canonical`, the six derived compensation frequencies, `location_count`,
    `schema_version`, `enriched_at` and `model` are all filled deterministically
    afterwards. Leaving them in the prompt invites the model to spend output
    tokens on them and, worse, to disagree with the code that is going to
    overwrite them anyway.
    """
    schema = JobEnrichment.model_json_schema()
    defs = schema.get("$defs", {})
    for name, drop in (
        ("SkillRequirement", ("canonical",)),
        (
            "Compensation",
            (
                "yearly_min",
                "yearly_max",
                "monthly_min",
                "monthly_max",
                "bi_weekly_min",
                "bi_weekly_max",
                "weekly_min",
                "weekly_max",
                "daily_min",
                "daily_max",
                "hourly_min",
                "hourly_max",
            ),
        ),
        ("Workplace", ("location_count",)),
    ):
        props = defs.get(name, {}).get("properties", {})
        for key in drop:
            props.pop(key, None)
    for key in ("schema_version", "enriched_at", "model", "extraction_gaps"):
        schema.get("properties", {}).pop(key, None)
    return schema


async def enrich_job(
    jd_text: str,
    *,
    title_hint: str | None = None,
    company_hint: str | None = None,
    posted_at: datetime | None = None,
) -> JobEnrichment:
    """Enrich one posting. Always returns a document, never raises.

    The contract is deliberately total. Enrichment sits in the ingest path, and
    an ingest path that can raise per job is an ingest path that loses jobs. A
    posting the model could not parse comes back as an otherwise-empty document
    carrying the reason in `extraction_gaps`, which is a row the index can hold
    and a human can later see was never really enriched.
    """
    settings = get_settings()
    if not settings.anthropic_api_key:
        log.warning("job_enrich.no_anthropic_key")
        return _fallback(title_hint, posted_at, gap="no_api_key")

    if not jd_text or not jd_text.strip():
        return _fallback(title_hint, posted_at, gap="empty_jd")

    client = anthropic.AsyncAnthropic(
        # `auth_token`, not `api_key`: the Manifest gateway authenticates on
        # `Authorization: Bearer`, and `api_key` would send `x-api-key` instead
        # and be rejected. Same call shape as every other agent in this repo.
        auth_token=settings.anthropic_api_key,
        base_url=settings.anthropic_base_url or None,
    )
    prompt = _user_prompt(jd_text, title_hint=title_hint, company_hint=company_hint)
    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]

    raw = ""
    for attempt in (1, 2):
        try:
            message = await create_message(
                client,
                model=settings.anthropic_model_extract,
                max_tokens=MAX_TOKENS,
                system=SYSTEM_PROMPT,
                messages=messages,
                # Sonnet 5. Enrichment is one pass over one document with a
                # fixed schema, which is the shape the mid tier handles well,
                # and it runs once per job across the whole corpus so the tier
                # choice is the single biggest lever on ingest cost.
                extra_headers={"x-manifest-tier": settings.manifest_tier_sonnet},
            )
        except anthropic.APIError as exc:
            # Already retried on the schedule in `create_message` by this point,
            # so this is a real failure rather than a blip. The row still ships.
            log.warning("job_enrich.gateway_failed", error=str(exc)[:300])
            return _fallback(title_hint, posted_at, gap="gateway_error")

        raw = response_text(message)
        if raw.strip():
            break
        log.warning(
            "job_enrich.empty_reply", attempt=attempt, **response_diagnostics(message)
        )
        if attempt == 1:
            messages = [
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": raw or "(empty)"},
                {"role": "user", "content": JSON_ONLY_RETRY},
            ]
    else:
        return _fallback(title_hint, posted_at, gap="empty_reply")

    enrichment, gaps = _validate_with_salvage(raw)
    if enrichment is None:
        log.warning("job_enrich.unparseable", preview=raw[:300])
        return _fallback(title_hint, posted_at, gap="invalid_json")

    return _finalize(
        enrichment,
        model=settings.anthropic_model_extract,
        posted_at=posted_at,
        title_hint=title_hint,
        gaps=gaps,
    )


def _validate_with_salvage(raw: str) -> tuple[JobEnrichment | None, list[str]]:
    """Validate the reply, dropping only the sub-objects that are broken.

    The whole point of a graceful partial: a model that hallucinated a
    compensation frequency should cost the job its salary figures and nothing
    else. A single `model_validate` would throw the skills, the education and
    the eligibility away with it, which turns one bad field into an unmatchable
    job.

    Each retry drops exactly the sub-objects pydantic named and records them, so
    the result says which parts are missing rather than pretending to be whole.
    """
    payload_text = extract_json_object(raw)
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError:
        return None, []
    if not isinstance(payload, dict):
        return None, []

    gaps: list[str] = []
    # Bounded: each pass removes at least one top-level key, and there are 26.
    for _ in range(len(JobEnrichment.model_fields) + 1):
        try:
            return JobEnrichment.model_validate(payload), gaps
        except ValidationError as exc:
            dropped = _drop_offending_fields(payload, exc)
            if not dropped:
                return None, gaps
            gaps.extend(dropped)
    return None, gaps


def _drop_offending_fields(payload: dict[str, Any], exc: ValidationError) -> list[str]:
    """Remove the top-level keys pydantic complained about. Returns their names.

    Deliberately coarse. Repairing a nested value means guessing what the model
    meant, which is the thing this whole module refuses to do. Dropping the
    smallest top-level container that contains the error keeps every sibling
    field and loses only what was actually wrong.
    """
    dropped: list[str] = []
    for error in exc.errors():
        location = error.get("loc") or ()
        if not location:
            continue
        top = str(location[0])
        if top in payload and top not in dropped:
            payload.pop(top, None)
            dropped.append(top)
    return dropped


def _finalize(
    enrichment: JobEnrichment,
    *,
    model: str | None,
    posted_at: datetime | None,
    title_hint: str | None,
    gaps: list[str],
) -> JobEnrichment:
    """Stamp provenance and fill the fields code owns rather than the model.

    Re-validates at the end so the derived compensation frequencies and the
    location count are recomputed from whatever survived, rather than trusting
    that the sub-model validators ran in the order the mutations happened.
    """
    data = enrichment.model_dump()
    data["schema_version"] = ENRICHMENT_SCHEMA_VERSION
    data["enriched_at"] = datetime.now(UTC)
    data["model"] = model
    data["extraction_gaps"] = sorted(set(gaps))

    if not data.get("core_job_title") and title_hint:
        data["core_job_title"] = title_hint.strip()

    # A real posted date from the source beats an estimate, and saying so is the
    # difference between the reference's honesty and merely copying its field
    # name. `publish_date_is_estimated` is the flag that carries it.
    if posted_at is not None:
        data["estimated_publish_date"] = posted_at
        data["publish_date_is_estimated"] = False
    elif data.get("estimated_publish_date") is not None:
        data["publish_date_is_estimated"] = True

    comp = data.get("compensation") or {}
    if _compensation_is_implausible(comp):
        # A stated figure that cannot be right is worse than no figure, because
        # it silently ranks in salary filters. Drop it and say we dropped it.
        data["compensation"] = Compensation().model_dump()
        data["extraction_gaps"] = sorted({*data["extraction_gaps"], "compensation_implausible"})

    data["commitment"] = _dedupe_commitment(data.get("commitment") or [])
    return JobEnrichment.model_validate(data)


# Bounds on annualized pay, wide enough to hold a volunteer stipend at one end
# and a staff total-comp number at the other. Anything outside is a units error
# (a model reading "$60" as yearly, or "$150,000" as hourly), not a real offer.
MIN_PLAUSIBLE_YEARLY = 1_000.0
MAX_PLAUSIBLE_YEARLY = 2_000_000.0


def _compensation_is_implausible(comp: dict[str, Any]) -> bool:
    frequency = comp.get("listed_frequency")
    if not frequency:
        return False
    for bound in ("listed_min", "listed_max"):
        listed = comp.get(bound)
        if listed is None:
            continue
        yearly = to_yearly(float(listed), frequency)
        if yearly is None:
            continue
        if not MIN_PLAUSIBLE_YEARLY <= yearly <= MAX_PLAUSIBLE_YEARLY:
            return True
    low, high = comp.get("listed_min"), comp.get("listed_max")
    return low is not None and high is not None and float(low) > float(high)


def _dedupe_commitment(values: list[Any]) -> list[Commitment]:
    seen: list[Commitment] = []
    for value in values:
        if isinstance(value, str) and value not in seen:
            seen.append(value)  # type: ignore[arg-type]
    return seen


def _fallback(
    title_hint: str | None, posted_at: datetime | None, *, gap: str
) -> JobEnrichment:
    """An honest empty document, so a job that failed enrichment still indexes.

    Carries no invented values. The scorer reads `extraction_gaps` and refuses
    to present a confident number for one of these, so a failure surfaces as
    "not scored yet" rather than as a job that scored badly.
    """
    return JobEnrichment(
        enriched_at=datetime.now(UTC),
        extraction_gaps=[gap],
        core_job_title=(title_hint or "").strip(),
        estimated_publish_date=posted_at,
        publish_date_is_estimated=posted_at is None,
    )


def store_enrichment(jd_parsed: dict[str, Any] | None, enrichment: JobEnrichment) -> dict[str, Any]:
    """Merge an enrichment document into a job's existing `jd_parsed` JSONB.

    Non-destructive on purpose: whatever `jd_parse.py` wrote at import stays
    where it is. The two are different passes with different schemas and the
    older one is still what parts of the tailor path read.
    """
    merged = dict(jd_parsed or {})
    merged[ENRICHMENT_KEY] = enrichment.model_dump(mode="json")
    return merged


def load_enrichment(jd_parsed: dict[str, Any] | None) -> JobEnrichment | None:
    """Read an enrichment document back, or None when the job has none yet.

    Returns None rather than raising on a document written by a newer schema
    version this code cannot validate, so a rollback finds jobs it cannot read
    instead of a server that will not start. The caller falls back to the
    un-enriched path for those, which is exactly what it does for a job that was
    never enriched at all.
    """
    if not jd_parsed:
        return None
    raw = jd_parsed.get(ENRICHMENT_KEY)
    if not isinstance(raw, dict):
        return None
    try:
        return JobEnrichment.model_validate(raw)
    except ValidationError as exc:
        log.warning(
            "job_enrich.unreadable_stored_document",
            stored_version=raw.get("schema_version"),
            expected_version=ENRICHMENT_SCHEMA_VERSION,
            error=str(exc)[:200],
        )
        return None
