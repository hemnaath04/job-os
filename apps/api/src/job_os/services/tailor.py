"""Resume tailoring agent.

Loads the user's master ResumeVersion + a target Job + every verified
ProfileFact and FactBullet, then asks Claude (via the configured Manifest
gateway) which facts/bullets to include and how to lightly edit them. Python
assembles the final JSON Resume deterministically from the agent's decisions
so the no-hallucination contract is enforced server-side, not in the prompt.

Hard rules baked into the prompt:
  - Every bullet in the output must reference a `fact_bullet_id` that exists
    in the provided bullets list. The agent NEVER invents new bullets.
  - Unmet JD requirements become `gap_questions` — surface the gap, do not
    paper over it. TypeScript is allowed only where verified project evidence
    supports it; frontend-heavy experience must never be invented.
  - Light rewrites are allowed (rewording, reordering keywords) as long as
    no metric or fact in the bullet changes.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any, TypedDict
from uuid import UUID

import anthropic
import structlog
from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from job_os.schemas.resumes import (
    GapQuestion,
    ProvenanceEntry,
    SelectedBullet,
    TailorAgentOutput,
)
from job_os.services.career_ops_rules import CAREER_OPS_RULES
from job_os.services.identity import identity_text as _identity_text
from job_os.services.llm_json import (
    JSON_ONLY_RETRY,
    parse_model_json,
    response_text,
)
from job_os.services.resume_writing import (
    MAX_PROJECT_BULLETS,
    MAX_SKILL_GROUPS,
    MAX_WORK_BULLETS,
    bullet_flags,
    dedupe_bullets,
    document_quality_flags,
    normalize_dashes,
)
from job_os.settings import get_settings

# SQLAlchemy + ORM models are only needed by the Postgres-backed `tailor_resume`
# adapter. They are imported lazily (below, inside the loaders) so the pure
# `run_tailor` core — and this whole module — imports cleanly inside the
# Appwrite Function runtime, which has neither SQLAlchemy nor the DB models.
if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from job_os.db.models import FactBullet, Job, ProfileFact, Resume, ResumeVersion, User

log = structlog.get_logger(__name__)


@dataclass
class TailorBullet:
    """Backend-agnostic view of a verified fact bullet (Postgres or Appwrite).

    Ids are strings because the two backends mint different shapes: Postgres
    uses UUIDs, the Appwrite workspace uses Appwrite ids. Adapters stringify."""

    id: str
    fact_id: str
    text: str
    target_role: str | None = None


@dataclass
class TailorFact:
    """Backend-agnostic view of a verified profile fact (Postgres or Appwrite)."""

    id: str
    kind: str
    title: str
    org: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    location: str | None = None
    source_url: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


MAX_ITERATIONS = 3
TARGET_ATS_SCORE = Decimal("80")
NUMBER_RE = re.compile(
    r"(?<!\w)(?:\$?\d[\d,.]*%?|\d+\s?(?:ms|s|sec|min|hours?|days?|x))(?!\w)",
    re.I,
)
TECHNOLOGY_RE = re.compile(
    r"(?<!\w)(?:"
    r"aws|azure|gcp|kubernetes|docker|terraform|react|next\.?js|fastapi|django|"
    r"flask|postgres(?:ql)?|mongodb|redis|kafka|pytorch|tensorflow|scikit-learn|"
    r"langchain|langgraph|openai|claude|c\+\+|c#|java|python|golang|go|typescript"
    r")(?!\w)",
    re.I,
)

SYSTEM_PROMPT = """\
You are a resume tailoring assistant. You receive (a) a parsed job description,
(b) the candidate's master JSON Resume, (c) the candidate's full profile of
verified facts and bullets. Your job is to choose which facts and bullets best
match the JD and how to rephrase the chosen bullets to surface JD keywords.

HARD CONSTRAINTS — these are non-negotiable:
1. Every bullet in `selected_bullets` MUST reference a `fact_bullet_id` that
   appears in the candidate's bullets list. Never invent a new fact_bullet_id.
2. A bullet's `rewritten_text` may rephrase wording, change verb tense, or
   reorder content, but MUST NOT introduce metrics, technologies, or claims
   that are not present in the original bullet text or the parent fact's
   payload. If a JD keyword is missing from the candidate's profile, it goes
   in `gap_questions`, not into a bullet.
3. TypeScript, React, and Next.js may appear only when the candidate's
   verified profile or project evidence supports them. Do not position the
   candidate as a frontend engineer. Frontend-heavy experience that is not
   verified belongs in `gap_questions`; emphasize verified backend, APIs,
   agents, pipelines, testing, concurrency, and infrastructure work instead.
4. `selected_fact_ids` includes the facts to render in the resume. Order
   doesn't matter (Python sorts by date / section).
5. `summary_objective` is a 1-2 sentence tailored summary line for the
   resume's basics.summary, or null to keep the master's summary.

SEMANTIC KEYWORD MATCHING (this is how you raise ATS coverage without
inventing experience):
- Look for SEMANTIC equivalents in the candidate's existing bullets, not just
  verbatim string matches. A bullet that says "built an LLM retrieval system
  over Pinecone" already covers BOTH "RAG" AND "vector stores", so rewrite it
  to use the JD's exact terminology and both keywords match.
- "agent orchestration with role-based agents" IS "multi-agent".
- "transformer fine-tuning with low-rank adapters" IS "LoRA fine-tuning".
- "deployed on Azure Functions" IS "Azure".
- "responsible-AI review of the dataset" IS "responsible AI".
- Rename the thing that was actually built. Do not append the JD's phrasing to
  the end of a bullet that does not describe it.
- Use an important keyword ONCE, in the strongest place, then let the evidence
  carry it. Repeating one JD phrase across the summary, a bullet and the skills
  block is keyword stuffing and it reads as gaming, not as tailoring.

KEYWORD STUFFING IS A FAILURE, NOT A SHORTCUT. Coverage is a diagnostic, not a
target. These rewrites are all forbidden even though none of them invents a
metric:
- Appending JD culture or soft-skill wording: "... in a fast-paced
  environment", "... showing a strong work ethic", "... in fast-moving markets".
- Naming a JD requirement the bullet does not demonstrate: "... applying core
  data-structures, algorithms, and systems fundamentals".
- Padding a bullet with a trailing clause that restates what it already said:
  "... demonstrating strong ownership", "... thereby enabling faster delivery".
If a keyword only fits by padding, it is a gap_question, not a rewrite.

BULLET WRITING (this is what a human reader judges):
- Open every bullet with a concrete past-tense verb: built, wrote, designed,
  extended, migrated, tested, shipped, scored, trained, hardened, traced.
- Never open with "In the ...", "Was part of a team ...", "Responsible for
  ...", "Helped ...", "Worked on ..." when a real verb is available.
- Do not overstate scope. Prefer "worked on" to "owned" or "led" when the
  candidate contributed to something they did not own. Accuracy beats a
  stronger-sounding verb.
- 30 words maximum per bullet, one idea each. Cutting a verified bullet down is
  always allowed and usually improves it. Growing one is how padding gets in.
- No first person. No "I", "my", "we", "our".
- No em dashes, en dashes or double hyphens. Use commas, colons or periods.
- Vary the opening verb. Three bullets in a row starting "Built" reads as
  machine-written.
- Where two bullets in the profile describe the SAME work in different words,
  pick the single best wording. Never select both.
- Do not select more than 4 bullets for one role or more than 3 for one
  project. Python will cut the surplus, so choose deliberately.

ATS:
- `ats_keywords_matched`: JD keywords that appear in your selected bullets or
  facts AFTER your rewrites.
- `ats_keywords_missing`: JD keywords that do not appear and have no matching
  fact. These usually become gap_questions too.

Output: a single JSON object matching the provided schema. No prose, no fences.
"""


class TailorGraphState(TypedDict):
    """State shared by the draft → score → refine LangGraph."""

    messages: list[anthropic.types.MessageParam]
    best_agent: TailorAgentOutput | None
    best_score: Decimal
    iteration_scores: list[float]
    done: bool


def _refine_prompt(
    *,
    coverage: Decimal,
    penalty: Decimal,
    missing: list[str],
    quality: dict[str, list[str]],
    target: Decimal,
) -> str:
    """Feedback turn after a pass that scored below target.

    Two kinds of feedback, because there are two ways to be below target. The
    keywords the pass left on the table say what to surface; the writing flags
    say what a human reader would hold against it. Both come from measuring the
    assembled document, not from the model's own account of how it did, so the
    model cannot close the gap by claiming a better result."""
    lines = [
        f"That pass scored {(coverage - penalty).quantize(Decimal('0.1'))} "
        f"(target {target}): ATS coverage {coverage.quantize(Decimal('0.1'))} "
        f"minus a writing-quality penalty of {penalty.quantize(Decimal('0.1'))}.",
        "Both numbers come from the assembled resume, not from your own "
        "matched/missing lists, so restating them cannot change the score.",
    ]
    if quality:
        lines += [
            "",
            "WRITING PROBLEMS, fix these first. They cost more than a keyword "
            "is worth:",
        ]
        for where, flags in quality.items():
            lines.append(f"  - {where}: {', '.join(flags)}")
        lines += [
            "",
            "How to read those flags: too_long means cut the bullet under 30 "
            "words. jd_padding means you appended JD wording that does not "
            "describe work done, so delete that clause. inflated_rewrite means "
            "your rewrite is longer than the verified bullet it came from. "
            "near_duplicate_bullets means you selected two bullets about the "
            "same work, so keep only the better one. repeated_opening_verb "
            "means vary the verb. weak_opener means start with a real "
            "past-tense verb. first_person means remove I/my/we. dash means "
            "replace an em dash with a comma or a colon.",
        ]
    if missing:
        lines += [
            "",
            f"Keywords still absent from the assembled resume: {json.dumps(missing)}",
            "",
            "For each one, look for an existing bullet whose underlying claim "
            "ALREADY covers the concept and rename what was built so the JD's "
            "own term appears. Valid rewrites replace wording, they do not "
            "append it:",
            "  - 'built an LLM retrieval system over Pinecone' becomes",
            "    'built a RAG pipeline over Pinecone vector stores'",
            "  - 'multi-step agent that delegates to specialised sub-agents' becomes",
            "    'multi-agent orchestration with role-specialised sub-agents'",
            "  - 'fine-tuned a transformer with low-rank adapters' becomes",
            "    'LoRA fine-tuning of a transformer'",
            "",
            "Do NOT add a keyword to a bullet whose underlying work does not "
            "support it. That is hallucination and it breaks the contract. Do "
            "NOT bolt the keyword onto the end of an unrelated bullet either; "
            "that is stuffing, it is penalised, and it lowers the score. A "
            "keyword you cannot surface honestly stays a gap_question.",
        ]
    lines += [
        "",
        "Return the FULL updated TailorAgentOutput JSON, not a diff. Same "
        "schema as before.",
    ]
    return "\n".join(lines)


async def tailor_resume(
    session: AsyncSession,
    *,
    user: User,
    resume: Resume,
    master_version: ResumeVersion,
    job: Job,
) -> tuple[dict[str, Any], list[ProvenanceEntry], list[GapQuestion], Decimal, dict[str, Any], str]:
    """Postgres-backed entry point.

    Loads verified facts/bullets from the DB, adapts them into backend-agnostic
    dataclasses, and delegates to `run_tailor` — the LangGraph agent. Keeping the
    agent flow in `run_tailor` lets the Appwrite Function reuse the exact same
    graph with no database.
    """
    facts_orm = await _load_verified_facts(session, user.id)
    bullets_orm = await _load_bullets(session, [f.id for f in facts_orm])
    facts = [
        TailorFact(
            id=str(f.id),
            kind=f.kind,
            title=f.title,
            org=f.org,
            start_date=f.start_date,
            end_date=f.end_date,
            location=f.location,
            source_url=f.source_url,
            payload=f.payload or {},
        )
        for f in facts_orm
    ]
    bullets_by_fact: dict[str, list[TailorBullet]] = {
        str(fact_id): [
            TailorBullet(
                id=str(b.id),
                fact_id=str(b.fact_id),
                text=b.text,
                target_role=b.target_role,
            )
            for b in bullets
        ]
        for fact_id, bullets in bullets_orm.items()
    }
    return await run_tailor(
        facts=facts,
        bullets_by_fact=bullets_by_fact,
        master_json_resume=master_version.json_resume,
        jd_parsed=job.jd_parsed or {},
        jd_clean=job.jd_clean or "",
    )


async def run_tailor(
    *,
    facts: list[TailorFact],
    bullets_by_fact: dict[str, list[TailorBullet]],
    master_json_resume: dict[str, Any],
    jd_parsed: dict[str, Any],
    jd_clean: str,
    on_progress: Callable[[str, float], None] | None = None,
) -> tuple[dict[str, Any], list[ProvenanceEntry], list[GapQuestion], Decimal, dict[str, Any], str]:
    """Backend-agnostic tailoring agent.

    Runs the draft -> score -> refine LangGraph, then assembles the JSON Resume
    deterministically. No DB access, so both the FastAPI backend and the Appwrite
    Function share this exact agent flow.

    `on_progress(stage, pct)` is an optional coarse progress hook. `pct` is a
    0.0-1.0 fraction. The FastAPI Postgres path passes nothing, so it is a no-op
    there; the Appwrite Function passes a callback that writes progress onto the
    agent job row so the browser can poll it.
    """
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set; cannot run the tailoring agent.")

    # One fact per real job, degree, project or skill before the model sees
    # anything. Re-importing a resume mints a second fact for the same job with
    # the bullets reworded, and showing the model both is what produced a role
    # with seven highlights, three of them saying the same thing twice.
    facts, bullets_by_fact = _merge_duplicate_facts(facts, bullets_by_fact)
    facts_payload = _build_facts_payload(facts, bullets_by_fact)

    user_prompt = _build_user_prompt(
        jd_parsed=jd_parsed,
        jd_clean=jd_clean,
        master_json_resume=master_json_resume,
        facts_payload=facts_payload,
    )
    if on_progress:
        on_progress("Reading job and profile", 0.1)

    client = anthropic.AsyncAnthropic(
        auth_token=settings.anthropic_api_key,
        base_url=settings.anthropic_base_url or None,
    )

    graph = StateGraph(TailorGraphState)

    async def draft_and_score(state: TailorGraphState) -> TailorGraphState:
        """One quality-model pass followed by deterministic Python scoring."""
        iteration = len(state["iteration_scores"]) + 1
        if on_progress:
            on_progress(f"Drafting pass {iteration}", min(0.85, 0.15 + (iteration - 1) * 0.22))
        msg = await client.messages.create(
            model=settings.anthropic_model_tailor,
            # Generous ceiling on purpose: the output carries a rewritten line
            # per selected bullet plus gap questions, and a response truncated
            # mid-JSON fails schema validation and kills the whole run.
            max_tokens=8192,
            system=f"{CAREER_OPS_RULES}\n\n{SYSTEM_PROMPT}",
            messages=state["messages"],
            extra_headers={"x-manifest-tier": settings.manifest_tier_sonnet},
        )
        raw = response_text(msg)
        try:
            attempt = parse_model_json(TailorAgentOutput, raw)
        except ValidationError as e:
            log.warning(
                "tailor.invalid_json",
                error=str(e),
                preview=raw[:400],
                iteration=len(state["iteration_scores"]) + 1,
            )
            if state["best_agent"] is not None:
                return {**state, "done": True}
            # No good pass yet, so a chatty or truncated reply would sink the
            # whole run. Show the model its own output and ask once for the
            # object alone before giving up.
            retry = await client.messages.create(
                model=settings.anthropic_model_tailor,
                max_tokens=8192,
                system=f"{CAREER_OPS_RULES}\n\n{SYSTEM_PROMPT}",
                messages=[
                    *state["messages"],
                    {"role": "assistant", "content": raw[:4000] or "(empty)"},
                    {"role": "user", "content": JSON_ONLY_RETRY},
                ],
                extra_headers={"x-manifest-tier": settings.manifest_tier_sonnet},
            )
            retry_raw = response_text(retry)
            try:
                attempt = parse_model_json(TailorAgentOutput, retry_raw)
            except ValidationError as retry_error:
                log.warning("tailor.invalid_json_after_retry", preview=retry_raw[:400])
                raise RuntimeError(
                    "Tailoring agent returned an invalid response."
                ) from retry_error
            raw = retry_raw

        # Score the resume this pass would actually ship, not the model's own
        # account of how it did. Self-reported matched/missing counts were
        # trivially gamed by claiming more matches, and the loop duly learned to
        # paste JD phrases onto unrelated bullets to raise a number nobody
        # outside the loop ever saw.
        document, _provenance, _selected = _build_document(
            attempt,
            facts=facts,
            bullets_by_fact=bullets_by_fact,
            master_json_resume=master_json_resume,
            facts_payload=facts_payload,
        )
        coverage, coverage_report = _compute_ats_from_document(
            jd_parsed=jd_parsed,
            json_resume=document,
            fallback_matched=attempt.ats_keywords_matched,
            fallback_missing=attempt.ats_keywords_missing,
        )
        quality = document_quality_flags(document)
        penalty = _quality_penalty(quality)
        score = coverage - penalty
        scores = [*state["iteration_scores"], float(score)]
        log.info(
            "tailor.iteration",
            iteration=len(scores),
            score=float(score),
            coverage=float(coverage),
            penalty=float(penalty),
            quality_flags=sorted(
                {flag for flags in quality.values() for flag in flags}
            ),
            target=float(TARGET_ATS_SCORE),
        )
        if on_progress:
            on_progress(f"Scoring pass {iteration}", min(0.85, 0.26 + (iteration - 1) * 0.22))

        best_agent = state["best_agent"]
        best_score = state["best_score"]
        if score > best_score:
            best_agent = attempt
            best_score = score

        # Stop once the target is met, once the passes run out, or once a pass
        # stops improving. A third call that cannot beat the second is pure
        # latency and cost for the user watching a progress bar.
        stalled = len(scores) > 1 and score <= Decimal(str(scores[-2]))
        done = score >= TARGET_ATS_SCORE or len(scores) >= MAX_ITERATIONS or stalled
        messages = state["messages"]
        if not done:
            if on_progress:
                on_progress(
                    f"Refining pass {iteration + 1}",
                    min(0.85, 0.3 + (iteration - 1) * 0.22),
                )
            messages = [
                *messages,
                {"role": "assistant", "content": raw},
                {
                    "role": "user",
                    "content": _refine_prompt(
                        coverage=coverage,
                        penalty=penalty,
                        missing=list(coverage_report.get("missing") or []),
                        quality=quality,
                        target=TARGET_ATS_SCORE,
                    ),
                },
            ]

        return {
            "messages": messages,
            "best_agent": best_agent,
            "best_score": best_score,
            "iteration_scores": scores,
            "done": done,
        }

    def route_after_score(state: TailorGraphState) -> str:
        return END if state["done"] else "draft_and_score"

    graph.add_node("draft_and_score", draft_and_score)
    graph.add_edge(START, "draft_and_score")
    graph.add_conditional_edges("draft_and_score", route_after_score)
    compiled_graph = graph.compile()
    graph_result = await compiled_graph.ainvoke(
        {
            "messages": [{"role": "user", "content": user_prompt}],
            "best_agent": None,
            "best_score": Decimal("-1"),
            "iteration_scores": [],
            "done": False,
        }
    )

    best_agent = graph_result["best_agent"]
    iteration_scores = graph_result["iteration_scores"]

    if best_agent is None:
        raise RuntimeError("Tailoring agent returned no valid response after retries.")
    agent = best_agent

    if on_progress:
        on_progress("Assembling resume", 0.9)

    json_resume, provenance, _selected = _build_document(
        agent,
        facts=facts,
        bullets_by_fact=bullets_by_fact,
        master_json_resume=master_json_resume,
        facts_payload=facts_payload,
    )

    ats_score, ats_report = _compute_ats_from_document(
        jd_parsed=jd_parsed,
        json_resume=json_resume,
        fallback_matched=agent.ats_keywords_matched,
        fallback_missing=agent.ats_keywords_missing,
    )

    # Embed pass-by-pass scores into the report so the FE can show the trail
    # without changing the response schema.
    ats_report["iterations"] = iteration_scores
    ats_report["target_ats_score"] = float(TARGET_ATS_SCORE)
    ats_report["reached_target"] = float(ats_score) >= float(TARGET_ATS_SCORE)
    # What a human reader would hold against the document, alongside what an ATS
    # would. An empty dict is the good outcome and is worth reporting as such.
    ats_report["writing_flags"] = document_quality_flags(json_resume)

    note = agent.agent_note
    n_iter = len(iteration_scores)
    if n_iter > 1:
        trail = " -> ".join(f"{s:.0f}" for s in iteration_scores)
        pass_word = "passes" if n_iter != 1 else "pass"
        if ats_score >= TARGET_ATS_SCORE:
            note += (
                f"\n(Hit target ATS {TARGET_ATS_SCORE} in {n_iter} {pass_word}: {trail})"
            )
        else:
            note += (
                f"\n(Could not reach target ATS {TARGET_ATS_SCORE} after {n_iter} "
                f"passes ({trail}). Remaining gaps need new facts on your Profile.)"
            )

    return (
        json_resume,
        provenance,
        list(agent.gap_questions),
        ats_score,
        ats_report,
        note,
    )


def _build_document(
    agent: TailorAgentOutput,
    *,
    facts: list[TailorFact],
    bullets_by_fact: dict[str, list[TailorBullet]],
    master_json_resume: dict[str, Any],
    facts_payload: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[ProvenanceEntry], list[SelectedBullet]]:
    """Turn one agent pass into the resume it would actually ship.

    Every safety check lives here, so the document the loop scores mid-run is
    byte-for-byte the document the user gets. Scoring a different, friendlier
    object than the one that ships is how the loop came to optimise a number
    nobody outside it could see.
    """
    valid_fact_ids = {f.id for f in facts}
    valid_bullet_ids: dict[str, str] = {
        b.id: b.fact_id for bs in bullets_by_fact.values() for b in bs
    }

    # Enforce the no-hallucination contract: drop any selected_bullet whose
    # fact_bullet_id isn't in our verified bullet set. (The prompt forbids
    # this; defense in depth in case the model slips.)
    safe_bullets = [
        sb for sb in agent.selected_bullets if sb.fact_bullet_id in valid_bullet_ids
    ]
    dropped = len(agent.selected_bullets) - len(safe_bullets)
    if dropped:
        log.warning("tailor.dropped_unknown_bullets", count=dropped)

    safe_fact_ids = {fid for fid in agent.selected_fact_ids if fid in valid_fact_ids}
    # Also include facts that own any selected bullet (so the parent
    # work/project entry renders).
    safe_fact_ids.update(valid_bullet_ids[sb.fact_bullet_id] for sb in safe_bullets)

    selected_facts = [f for f in facts if f.id in safe_fact_ids]
    facts_by_id = {fact.id: fact for fact in facts}
    bullets_by_id = {
        bullet.id: bullet
        for fact_bullets in bullets_by_fact.values()
        for bullet in fact_bullets
    }
    safe_bullets = _sanitize_selected_bullets(
        safe_bullets,
        bullets_by_id=bullets_by_id,
        facts_by_id=facts_by_id,
    )
    summary_objective = _safe_summary(
        agent.summary_objective,
        master_json_resume=master_json_resume,
        facts_payload=facts_payload,
    )
    json_resume, provenance = _assemble_json_resume(
        master_json_resume=master_json_resume,
        all_facts=facts,
        selected_facts=selected_facts,
        selected_bullets=safe_bullets,
        bullets_by_fact=bullets_by_fact,
        summary_objective=summary_objective,
    )
    return json_resume, provenance, safe_bullets


# What one flagged writing problem costs against keyword coverage. Three points
# is deliberately more than one keyword is usually worth on a JD with a dozen of
# them, because a reader notices a duplicated bullet and does not notice a
# missing keyword. The cap keeps a badly worded pass comparable to a
# well-written one that covers less, instead of driving the score negative.
QUALITY_FLAG_PENALTY = Decimal("3")
MAX_QUALITY_PENALTY = Decimal("30")


def _quality_penalty(quality: dict[str, list[str]]) -> Decimal:
    flagged = sum(len(flags) for flags in quality.values())
    return min(MAX_QUALITY_PENALTY, QUALITY_FLAG_PENALTY * Decimal(flagged))


def _fact_identity(fact: TailorFact) -> tuple[str, ...]:
    """The key that decides whether two verified facts are the same thing.

    Mirrors `identity.fact_identity`, which does this for imports, so the vault
    and the renderer cannot disagree about what counts as one job.
    """
    org = _identity_text(fact.org)
    if fact.kind in {"experience", "education"}:
        return (
            fact.kind,
            org,
            str(fact.start_date or ""),
            str(fact.end_date or ""),
        )
    return (fact.kind, org, _identity_text(fact.title))


def _merge_duplicate_facts(
    facts: list[TailorFact], bullets_by_fact: dict[str, list[TailorBullet]]
) -> tuple[list[TailorFact], dict[str, list[TailorBullet]]]:
    """Fold facts describing the same thing into one, keeping every bullet.

    Runs before the prompt is built, so the model chooses between wordings of
    one job instead of selecting from two facts about it and handing back
    duplicate bullets that no later step can tell apart. Bullet ids are
    preserved, so provenance still points at the row the text came from.
    """
    grouped: dict[tuple[str, ...], list[TailorFact]] = {}
    for fact in facts:
        grouped.setdefault(_fact_identity(fact), []).append(fact)

    merged_facts: list[TailorFact] = []
    merged_bullets: dict[str, list[TailorBullet]] = {}
    for identity, group in grouped.items():
        if len(group) == 1:
            fact = group[0]
            merged_facts.append(fact)
            merged_bullets[fact.id] = list(bullets_by_fact.get(fact.id, []))
            continue
        # The variant with the most evidence wins, then the most specific
        # wording, matching how duplicate rendered entries are collapsed.
        ranked = sorted(
            group,
            key=lambda f: (
                len(bullets_by_fact.get(f.id, [])),
                len(f.payload or {}),
                len(f.title or ""),
            ),
            reverse=True,
        )
        canonical = ranked[0]
        payload: dict[str, Any] = {}
        for variant in reversed(ranked):
            for key, value in (variant.payload or {}).items():
                if value not in (None, "", [], {}):
                    payload[key] = value
        winner = TailorFact(
            id=canonical.id,
            kind=canonical.kind,
            title=canonical.title,
            org=canonical.org or next((f.org for f in ranked if f.org), None),
            start_date=canonical.start_date
            or next((f.start_date for f in ranked if f.start_date), None),
            end_date=canonical.end_date
            or next((f.end_date for f in ranked if f.end_date), None),
            location=canonical.location
            or next((f.location for f in ranked if f.location), None),
            source_url=canonical.source_url
            or next((f.source_url for f in ranked if f.source_url), None),
            payload=payload,
        )
        # Every variant's bullets, reparented onto the surviving fact, with
        # rewordings of one accomplishment collapsed to the richer wording.
        pooled: list[TailorBullet] = []
        for variant in ranked:
            pooled.extend(bullets_by_fact.get(variant.id, []))
        by_text = {bullet.text.strip(): bullet for bullet in reversed(pooled)}
        kept_texts = dedupe_bullets([bullet.text for bullet in pooled])
        merged_facts.append(winner)
        merged_bullets[winner.id] = [
            TailorBullet(
                id=by_text[text].id,
                fact_id=winner.id,
                text=text,
                target_role=by_text[text].target_role,
            )
            for text in kept_texts
            if text in by_text
        ]
        log.info(
            "tailor.merged_duplicate_facts",
            identity=identity,
            variants=len(group),
            bullets_before=len(pooled),
            bullets_after=len(merged_bullets[winner.id]),
        )
    return merged_facts, merged_bullets


def _technology_terms(text: str) -> set[str]:
    return {match.group(0).lower() for match in TECHNOLOGY_RE.finditer(text)}


def _sanitize_selected_bullets(
    selected: list[SelectedBullet],
    *,
    bullets_by_id: dict[str, TailorBullet],
    facts_by_id: dict[str, TailorFact],
) -> list[SelectedBullet]:
    """Fall back to verified source text when a rewrite adds risky claims."""
    section_for_kind = {
        "experience": "work",
        "project": "projects",
        "volunteering": "volunteer",
    }
    safe: list[SelectedBullet] = []
    for selected_bullet in selected:
        source = bullets_by_id.get(selected_bullet.fact_bullet_id)
        fact = facts_by_id.get(source.fact_id) if source else None
        if source is None or fact is None:
            # Orphaned bullet (its parent fact is gone or unverified). Dropping
            # it is the safe move: we cannot prove what it belongs to.
            log.warning(
                "tailor.orphan_bullet_dropped",
                bullet_id=str(selected_bullet.fact_bullet_id),
            )
            continue
        expected_section = section_for_kind.get(fact.kind)
        source_context = source.text + "\n" + json.dumps(fact.payload or {}, ensure_ascii=False)
        added_numbers = set(NUMBER_RE.findall(selected_bullet.rewritten_text)) - set(
            NUMBER_RE.findall(source_context)
        )
        added_technologies = _technology_terms(
            selected_bullet.rewritten_text
        ) - _technology_terms(source_context)
        wrong_section = (
            expected_section is None
            or selected_bullet.target_section != expected_section
        )
        # A rewrite that invents nothing can still be worse than the verified
        # wording it replaced: padded with JD culture language, inflated past the
        # original's length, or reworded into the first person. Reverting to the
        # source text is the safe move, and it is available for free because the
        # source text is a verified fact.
        padding_flags = [
            flag
            for flag in bullet_flags(
                selected_bullet.rewritten_text, source_text=source.text
            )
            if flag.startswith(("jd_padding", "inflated_rewrite", "first_person"))
        ]
        if added_numbers or added_technologies or wrong_section or padding_flags:
            log.warning(
                "tailor.unsafe_rewrite_reverted",
                bullet_id=str(source.id),
                added_numbers=sorted(added_numbers),
                added_technologies=sorted(added_technologies),
                wrong_section=wrong_section,
                padding_flags=padding_flags,
            )
            if expected_section is None:
                continue
            safe.append(
                SelectedBullet(
                    fact_bullet_id=source.id,
                    rewritten_text=source.text,
                    target_section=expected_section,
                )
            )
            continue
        safe.append(selected_bullet)
    return safe


def _safe_summary(
    summary: str | None,
    *,
    master_json_resume: dict[str, Any],
    facts_payload: list[dict[str, Any]],
) -> str | None:
    if not summary:
        return None
    source = json.dumps(
        {"master": master_json_resume, "facts": facts_payload},
        ensure_ascii=False,
    )
    if (
        set(NUMBER_RE.findall(summary)) - set(NUMBER_RE.findall(source))
        or _technology_terms(summary) - _technology_terms(source)
    ):
        log.warning("tailor.unsafe_summary_reverted")
        return None
    return summary


# ---- Loaders -----------------------------------------------------------------


async def _load_verified_facts(session: AsyncSession, user_id: UUID) -> list[ProfileFact]:
    # Imported locally so this module stays importable without SQLAlchemy /
    # the DB models (e.g. inside the Appwrite Function, which only calls
    # `run_tailor`).
    from sqlalchemy import select

    from job_os.db.models import ProfileFact

    result = await session.execute(
        select(ProfileFact)
        .where(ProfileFact.user_id == user_id, ProfileFact.verified.is_(True))
        .order_by(
            ProfileFact.end_date.desc().nulls_first(),
            ProfileFact.start_date.desc().nulls_last(),
        )
    )
    return list(result.scalars().all())


async def _load_bullets(
    session: AsyncSession, fact_ids: list[UUID]
) -> dict[UUID, list[FactBullet]]:
    if not fact_ids:
        return {}
    from sqlalchemy import select

    from job_os.db.models import FactBullet

    result = await session.execute(
        select(FactBullet).where(FactBullet.fact_id.in_(fact_ids))
    )
    out: dict[UUID, list[FactBullet]] = {}
    for b in result.scalars().all():
        out.setdefault(b.fact_id, []).append(b)
    return out


# ---- Prompt assembly ---------------------------------------------------------


def _build_facts_payload(
    facts: list[TailorFact], bullets_by_fact: dict[str, list[TailorBullet]]
) -> list[dict[str, Any]]:
    """Compact JSON the LLM sees — only verified facts + their bullets, no PII beyond resume."""
    out: list[dict[str, Any]] = []
    for f in facts:
        out.append(
            {
                "id": str(f.id),
                "kind": f.kind,
                "title": f.title,
                "org": f.org,
                "start_date": f.start_date.isoformat() if f.start_date else None,
                "end_date": f.end_date.isoformat() if f.end_date else None,
                "location": f.location,
                "source_url": f.source_url,
                "payload": f.payload or {},
                "bullets": [
                    {"id": str(b.id), "text": b.text, "target_role": b.target_role}
                    for b in bullets_by_fact.get(f.id, [])
                ],
            }
        )
    return out


def _build_user_prompt(
    *,
    jd_parsed: dict[str, Any],
    jd_clean: str,
    master_json_resume: dict[str, Any],
    facts_payload: list[dict[str, Any]],
) -> str:
    return (
        "JOB DESCRIPTION (parsed):\n"
        f"{json.dumps(jd_parsed or {}, indent=2)}\n\n"
        "JOB DESCRIPTION (clean text, truncated):\n"
        f"<jd>\n{(jd_clean or '')[:8000]}\n</jd>\n\n"
        "CANDIDATE MASTER RESUME (JSON Resume):\n"
        f"{json.dumps(master_json_resume, indent=2)[:6000]}\n\n"
        "CANDIDATE VERIFIED FACTS + BULLETS:\n"
        f"{json.dumps(facts_payload, indent=2)[:12000]}\n\n"
        "Respond with a single JSON object matching this schema (no prose, no fences):\n"
        f"{json.dumps(TailorAgentOutput.model_json_schema())}"
    )


# ---- Assembly ----------------------------------------------------------------

_KIND_TO_SECTION = {
    "experience": "work",
    "project": "projects",
    "volunteering": "volunteer",
    "education": "education",
    "skill": "skills",
    "certification": "certificates",
    "publication": "publications",
    "award": "awards",
}


def _work_identity(entry: dict[str, Any]) -> tuple[str, ...]:
    return (
        _identity_text(entry.get("name")),
        str(entry.get("startDate") or ""),
        str(entry.get("endDate") or ""),
    )


def _education_identity(entry: dict[str, Any]) -> tuple[str, ...]:
    return (
        _identity_text(entry.get("institution")),
        _identity_text(f"{entry.get('studyType') or ''} {entry.get('area') or ''}"),
    )


def _project_identity(entry: dict[str, Any]) -> tuple[str, ...]:
    return (
        _identity_text(entry.get("name")),
        str(entry.get("startDate") or ""),
        str(entry.get("endDate") or ""),
    )


def _volunteer_identity(entry: dict[str, Any]) -> tuple[str, ...]:
    return (
        _identity_text(entry.get("organization")),
        _identity_text(entry.get("position")),
    )


def _merge_duplicate_group(
    group: list[dict[str, Any]], *, list_fields: tuple[str, ...]
) -> dict[str, Any]:
    """Fold entries for the same real thing into one, losing nothing."""
    # The richest variant sets the scalar fields: most evidence first, then the
    # most specific wording, since "Junior Software Test Automation Engineer,
    # Client: ..." says more than "Software Test Automation Engineer".
    ranked = sorted(
        group,
        key=lambda entry: (
            len(entry.get("highlights") or []),
            len(str(entry.get("position") or entry.get("studyType") or "")),
        ),
        reverse=True,
    )
    merged = dict(ranked[0])
    for other in ranked[1:]:
        for key, value in other.items():
            if key not in list_fields and not merged.get(key) and value:
                merged[key] = value
    # Union the lists so a bullet that only existed on the other variant of the
    # same job still appears. Order preserved, exact duplicates collapsed.
    for key in list_fields:
        seen: dict[str, None] = {}
        for entry in ranked:
            for item in entry.get(key) or []:
                seen.setdefault(str(item), None)
        if not seen:
            continue
        if key == "highlights":
            # Two facts for one job carry the same accomplishment worded two
            # ways, which an exact-string union cannot see. Collapsing them here
            # as well as before the prompt means a direct caller of the assembly
            # cannot end up with a role that says the same thing twice.
            merged[key] = dedupe_bullets(seen)[:MAX_WORK_BULLETS]
            continue
        merged[key] = list(seen)
    return merged


def _collapse_duplicate_entries(
    entries: list[dict[str, Any]],
    identity: Callable[[dict[str, Any]], tuple[str, ...]],
    *,
    list_fields: tuple[str, ...],
) -> list[dict[str, Any]]:
    """Emit one entry per real job, degree, or project.

    Re-importing a resume that words a role slightly differently creates a
    second verified fact for the same job, and spelling an institution with a
    dash instead of a comma does the same for a degree. Both facts are
    individually legitimate, so the fix belongs here rather than in the vault:
    render the entity once, and keep every bullet from every variant.
    """
    grouped: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for entry in entries:
        grouped.setdefault(identity(entry), []).append(entry)
    collapsed: list[dict[str, Any]] = []
    for key, group in grouped.items():
        if len(group) == 1:
            collapsed.append(group[0])
            continue
        log.info("tailor.merged_duplicate_entries", identity=key, variants=len(group))
        collapsed.append(_merge_duplicate_group(group, list_fields=list_fields))
    return collapsed


def _assemble_json_resume(
    *,
    master_json_resume: dict[str, Any],
    all_facts: list[TailorFact],
    selected_facts: list[TailorFact],
    selected_bullets: list[SelectedBullet],
    bullets_by_fact: dict[str, list[TailorBullet]],
    summary_objective: str | None,
) -> tuple[dict[str, Any], list[ProvenanceEntry]]:
    """Build the tailored JSON Resume + provenance.

    Sections fall into two buckets:

    **Always-include (the skeleton):** education, work experience, skills,
    certifications, publications, awards. These show every verified fact
    regardless of whether the agent flagged them as JD-relevant — you don't
    HIDE your education or work history when tailoring, you adjust what
    bullets get emphasized inside the role. For work entries, the agent's
    selected bullets (if any) are surfaced; if the agent didn't pick any
    bullets for a role, fall back to ALL its bullets so the entry isn't
    blank.

    **Selection-filtered:** projects, volunteer. These are where real
    tailoring lives — you have many of them and only the JD-relevant ones
    should show.
    """
    basics = dict(master_json_resume.get("basics") or {})
    if summary_objective:
        basics["summary"] = summary_objective

    bullet_map: dict[str, TailorBullet] = {
        b.id: b for bs in bullets_by_fact.values() for b in bs
    }
    by_fact_selected: dict[str, list[SelectedBullet]] = {}
    for sb in selected_bullets:
        parent_fact = bullet_map[sb.fact_bullet_id].fact_id
        by_fact_selected.setdefault(parent_fact, []).append(sb)

    selected_fact_ids = {f.id for f in selected_facts}
    # Provenance has to describe the bullets that reached the page, not the ones
    # the agent asked for. Capping and de-duplication happen below, and a
    # provenance row for a bullet nobody can find on the resume proves nothing.
    rendered_bullets: list[SelectedBullet] = []

    def _facts_of(kind: str, *, only_selected: bool = False) -> list[TailorFact]:
        pool = (
            [f for f in all_facts if f.id in selected_fact_ids]
            if only_selected
            else all_facts
        )
        out = [f for f in pool if f.kind == kind]
        # A missing end date means ongoing, which is the most recent thing the
        # candidate has, not the oldest. Treating it as date.min sorted every
        # current project below a finished one, so a tailored resume led with a
        # 2024 course project and buried this year's flagship work.
        out.sort(
            key=lambda f: (f.end_date or date.max, f.start_date or date.min),
            reverse=True,
        )
        return out

    def _bullets_for(
        f: TailorFact, *, limit: int
    ) -> tuple[list[str], list[SelectedBullet]]:
        """Pick the bullet set to render for a fact.

        Prefer agent-selected (tailored) bullets if any exist for this fact.
        Otherwise fall back to the fact's verified bullets, because an
        un-tailored bullet still beats a blank role on the resume.

        Rewordings of one accomplishment are collapsed and the list is capped,
        since a role showing seven near-identical highlights reads as an
        unedited draft and crowds a whole project off the page. Cutting keeps
        the agent's order, so the bullets it judged most relevant survive.
        """
        chosen = by_fact_selected.get(f.id) or []
        if chosen:
            texts = dedupe_bullets(sb.rewritten_text for sb in chosen)[:limit]
            surviving = set(texts)
            kept = [sb for sb in chosen if sb.rewritten_text.strip() in surviving]
            rendered_bullets.extend(kept)
            return texts, kept
        all_b = bullets_by_fact.get(f.id, []) or []
        return dedupe_bullets(b.text for b in all_b)[:limit], []

    work: list[dict[str, Any]] = []
    for f in _facts_of("experience"):
        bullets, _picked = _bullets_for(f, limit=MAX_WORK_BULLETS)
        payload = f.payload or {}
        work.append(
            {
                "name": f.org or "",
                "position": f.title,
                "startDate": f.start_date.isoformat() if f.start_date else None,
                "endDate": f.end_date.isoformat() if f.end_date else None,
                "location": f.location,
                "summary": payload.get("summary"),
                "url": f.source_url,
                "highlights": bullets,
                "keywords": payload.get("keywords", []),
            }
        )

    projects: list[dict[str, Any]] = []
    project_pool = _facts_of("project", only_selected=True)
    # If the agent didn't pick any projects (or didn't surface a project
    # section at all), fall back to ALL verified projects rather than show a
    # resume with no project section.
    if not project_pool:
        project_pool = _facts_of("project")
    for f in project_pool:
        bullets, _picked = _bullets_for(f, limit=MAX_PROJECT_BULLETS)
        payload = f.payload or {}
        projects.append(
            {
                "name": f.title,
                "description": payload.get("description"),
                "startDate": f.start_date.isoformat() if f.start_date else None,
                "endDate": f.end_date.isoformat() if f.end_date else None,
                "url": f.source_url,
                "highlights": bullets,
                "keywords": payload.get("keywords", []),
                "roles": payload.get("roles", []),
                "entity": payload.get("entity"),
                "type": payload.get("type"),
            }
        )

    volunteer: list[dict[str, Any]] = []
    vol_pool = _facts_of("volunteering", only_selected=True)
    for f in vol_pool:
        bullets, _picked = _bullets_for(f, limit=MAX_PROJECT_BULLETS)
        payload = f.payload or {}
        volunteer.append(
            {
                "organization": f.org or "",
                "position": f.title,
                "startDate": f.start_date.isoformat() if f.start_date else None,
                "endDate": f.end_date.isoformat() if f.end_date else None,
                "url": f.source_url,
                "summary": payload.get("summary"),
                "highlights": bullets,
            }
        )

    education: list[dict[str, Any]] = []
    for f in _facts_of("education"):
        payload = f.payload or {}
        education.append(
            {
                "institution": f.org or "",
                "area": payload.get("area"),
                "studyType": payload.get("studyType"),
                "startDate": f.start_date.isoformat() if f.start_date else None,
                "endDate": f.end_date.isoformat() if f.end_date else None,
                "score": payload.get("score"),
                "courses": payload.get("courses", []),
                "location": f.location,
                "url": f.source_url,
            }
        )

    skills_by_category: dict[str, list[str]] = {}
    for f in _facts_of("skill"):
        payload = f.payload or {}
        category = (payload.get("category") or f.org or "Skills").strip()
        skills_by_category.setdefault(category, []).append(f.title)

    certificates: list[dict[str, Any]] = []
    for f in _facts_of("certification"):
        certificates.append(
            {
                "name": f.title,
                "issuer": f.org,
                "date": f.start_date.isoformat() if f.start_date else None,
                "url": f.source_url,
            }
        )

    publications: list[dict[str, Any]] = []
    for f in _facts_of("publication"):
        payload = f.payload or {}
        publications.append(
            {
                "name": f.title,
                "publisher": f.org,
                "releaseDate": f.start_date.isoformat() if f.start_date else None,
                "url": f.source_url,
                "summary": payload.get("summary"),
            }
        )

    awards: list[dict[str, Any]] = []
    for f in _facts_of("award"):
        payload = f.payload or {}
        awards.append(
            {
                "title": f.title,
                "awarder": f.org,
                "date": f.start_date.isoformat() if f.start_date else None,
                "summary": payload.get("summary"),
            }
        )

    # One entry per real entity. Duplicate verified facts for the same job or
    # degree are the norm once a resume has been imported more than once, and
    # rendering both put EPAM Systems and the Northeastern MS on the resume twice.
    work = _collapse_duplicate_entries(
        work, _work_identity, list_fields=("highlights", "keywords")
    )
    education = _collapse_duplicate_entries(
        education, _education_identity, list_fields=("courses",)
    )
    projects = _collapse_duplicate_entries(
        projects, _project_identity, list_fields=("highlights", "keywords", "roles")
    )
    volunteer = _collapse_duplicate_entries(
        volunteer, _volunteer_identity, list_fields=("highlights",)
    )

    json_resume: dict[str, Any] = {
        "basics": basics,
        "work": work,
        "projects": projects,
        "volunteer": volunteer,
        "education": education,
        "skills": _consolidate_skills(skills_by_category),
        "certificates": certificates,
        "publications": publications,
        "awards": awards,
    }
    # Last step, so no later edit can reintroduce a dash the user's rules forbid.
    json_resume = _normalize_document_text(json_resume)

    provenance: list[ProvenanceEntry] = []
    for sb in rendered_bullets:
        fb = bullet_map[sb.fact_bullet_id]
        provenance.append(
            ProvenanceEntry(
                section=sb.target_section,
                text=normalize_dashes(sb.rewritten_text) or sb.rewritten_text,
                fact_bullet_id=fb.id,
                fact_id=fb.fact_id,
            )
        )

    return json_resume, provenance


# Labels that name no actual category. A profile picks these up when a skill was
# imported without one, and printing a resume row headed "Skills" inside the
# skills section is worse than printing nothing.
_GENERIC_SKILL_LABELS = frozenset(
    {"skills", "skill", "other", "others", "misc", "miscellaneous", "technical skills"}
)
_ADDITIONAL_SKILL_LABEL = "Additional"


def _consolidate_skills(
    skills_by_category: dict[str, list[str]],
) -> list[dict[str, Any]]:
    """One row per real category, each keyword appearing once on the page.

    A profile that has been imported more than once carries the same category
    spelled several ways ("AI / ML" and "AI & ML") and the same tools listed
    under several of them, which rendered eight skill rows where five belonged
    and spent a fifth of a one-page resume repeating itself.
    """
    merged: dict[str, dict[str, Any]] = {}
    for label, titles in skills_by_category.items():
        # Same words, different punctuation, is the same category.
        key = " ".join(sorted(_identity_text(label).split()))
        bucket = merged.setdefault(key, {"name": label.strip(), "keywords": []})
        bucket["keywords"].extend(titles)

    groups: list[dict[str, Any]] = []
    seen_keywords: set[str] = set()
    for bucket in merged.values():
        unique: list[str] = []
        for keyword in bucket["keywords"]:
            folded = _identity_text(keyword)
            if not folded or folded in seen_keywords:
                continue
            seen_keywords.add(folded)
            unique.append(keyword)
        if not unique:
            # Every keyword already appears in an earlier row, so this row would
            # print a heading over nothing.
            continue
        name = bucket["name"]
        if _identity_text(name) in _GENERIC_SKILL_LABELS:
            name = _ADDITIONAL_SKILL_LABEL
        groups.append({"name": name, "keywords": unique})

    # A category with no name of its own belongs at the end, after the ones that
    # tell the reader something.
    groups.sort(key=lambda g: g["name"] == _ADDITIONAL_SKILL_LABEL)
    if len(groups) > MAX_SKILL_GROUPS:
        tail = groups[MAX_SKILL_GROUPS - 1 :]
        folded_keywords: list[str] = []
        for group in tail:
            folded_keywords.extend(group["keywords"])
        groups = [
            *groups[: MAX_SKILL_GROUPS - 1],
            {"name": _ADDITIONAL_SKILL_LABEL, "keywords": folded_keywords},
        ]
    return groups


# Fields where "Name — Subtitle" is the idiom, so a colon reads better than the
# comma used everywhere else. The user's own master resume writes these with a
# colon already.
_TITLE_FIELDS = frozenset({"name", "title", "position", "studyType", "label"})


def _normalize_document_text(value: Any, *, field: str | None = None) -> Any:
    """Strip em dashes, en dashes and double hyphens out of a whole document.

    The rule is global, but the source facts predate it: institutions, project
    names and bullets all arrive carrying dashes. Doing this once over the
    finished document is the only place that catches every field, including the
    titles the PDF template never filtered.
    """
    if isinstance(value, str):
        separator = ": " if field in _TITLE_FIELDS else ", "
        return normalize_dashes(value, separator=separator)
    if isinstance(value, dict):
        return {
            key: _normalize_document_text(item, field=key) for key, item in value.items()
        }
    if isinstance(value, list):
        return [_normalize_document_text(item, field=field) for item in value]
    return value


# A term long enough to be a sentence is a requirement, not an ATS keyword.
ATS_KEYWORD_MAX_WORDS = 5
ATS_KEYWORD_MAX_CHARS = 48

# Terms that are not candidate skills and so do not belong in the score at all.
# A JD lists these under "requirements", but no resume can keyword-match an
# eligibility rule, a description of the employer, or soft-skill boilerplate.
# Counting them as missing understates real coverage: a Point72 JD scored 30
# partly on "Minimum 3.0 GPA", "proprietary trading firm" and "market
# initiatives". Job-type words like "internship" are excluded from the other
# direction, since matching them is not evidence of a relevant skill.
# Word-boundary matched, so "firm" does not catch "firmware".
_NON_SKILL_RE = re.compile(
    r"\b(?:"
    # Eligibility and enrollment
    r"gpa|grade point|bachelors?|masters?|phd|degree|major|"
    r"junior standing|senior standing|graduation date|currently pursuing|"
    r"sponsorship|visa|citizenship|work authorization|clearance|"
    # Availability
    r"ability to start|able to start|willing to|must be able|start date|"
    # The employer or the role, rather than a candidate skill
    r"firm|company|employer|initiatives?|internships?|full[- ]time|part[- ]time|"
    # Soft-skill boilerplate
    r"work ethic|fast[- ]paced|team player|self[- ]starter|detail[- ]oriented|"
    r"passion for|genuine interest|interest in|communication skills"
    r")\b",
    re.I,
)


_MONTH_NAMES = (
    r"january|february|march|april|may|june|july|august|september|october|"
    r"november|december|jan|feb|mar|apr|jun|jul|aug|sept?|oct|nov|dec"
)
_MONTH_RE = re.compile(rf"\b(?:{_MONTH_NAMES})\b", re.I)
_DATE_RESIDUE_RE = re.compile(r"[\d,./\s\-]+")


def _is_date_term(term: str) -> bool:
    """True when a JD term is only a date, like "May 2028" or "June 1, 2027".

    A start date or a graduation window is a scheduling fact, not a skill, so
    crediting one as a matched keyword flatters the score and counting one as
    missing punishes a resume that has nothing to answer with. The Point72
    posting supplied both at once.
    """
    residue = _DATE_RESIDUE_RE.sub(" ", _MONTH_RE.sub(" ", term))
    return bool(term.strip()) and not residue.strip()


def _is_ats_keyword(term: str) -> bool:
    cleaned = term.strip()
    if not cleaned or len(cleaned) > ATS_KEYWORD_MAX_CHARS:
        return False
    return len(cleaned.split()) <= ATS_KEYWORD_MAX_WORDS


def _is_candidate_skill(term: str) -> bool:
    """False for JD terms a resume could never legitimately match."""
    return not _NON_SKILL_RE.search(term) and not _is_date_term(term)


def _compute_ats(*, matched: list[str], missing: list[str]) -> tuple[Decimal, dict[str, Any]]:
    total = len(matched) + len(missing)
    if total == 0:
        score = Decimal("0.0")
    else:
        score = (Decimal(len(matched)) / Decimal(total) * Decimal("100")).quantize(Decimal("0.1"))
    report = {
        "matched": matched,
        "missing": missing,
        "matched_count": len(matched),
        "missing_count": len(missing),
    }
    return score, report


def _ats_source_text(json_resume: dict[str, Any]) -> str:
    """The candidate's own words in the resume, without the schema around them.

    Scoring `json.dumps(document)` put the JSON Resume key names into the text
    being matched, so a JD asking for "location", "score", "date", "keywords" or
    "summary" matched the schema rather than the candidate and inflated coverage
    for free. Only the values are the candidate's claims, so only the values are
    scored.
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
        elif value is not None and not isinstance(value, bool):
            parts.append(str(value))

    walk(json_resume)
    return " ".join(parts).casefold()


def _compute_ats_from_document(
    *,
    jd_parsed: dict[str, Any],
    json_resume: dict[str, Any],
    fallback_matched: list[str],
    fallback_missing: list[str],
) -> tuple[Decimal, dict[str, Any]]:
    """Score only JD terms that actually appear in the assembled resume."""
    parsed = jd_parsed or {}
    candidates: list[str] = []
    for key in (
        "required_skills",
        "preferred_skills",
        "technologies",
        "keywords",
    ):
        value = parsed.get(key, [])
        if isinstance(value, list):
            candidates.extend(str(item).strip() for item in value if str(item).strip())

    # Matching is a substring test, so only keyword-like terms can ever match.
    # JD parsers routinely drop whole requirement sentences into
    # `required_skills` ("Currently pursuing a bachelor's or master's in ..."),
    # which never appear verbatim in a resume and would drag the score to near
    # zero no matter how good the tailoring is. Prose requirements belong to the
    # gap-question path, so keep them out of the keyword denominator and report
    # them separately instead of hiding them.
    prose = [term for term in candidates if not _is_ats_keyword(term)]

    # The agent's own term list is a second source of JD keywords, and it is the
    # one that recovers real skills buried inside a prose requirement: the
    # sentence "computer science fundamentals: data structures, algorithms,
    # systems" is dropped above, yet those three are genuine skills the resume
    # may well cover. Union them in. Every term is still verified against the
    # assembled document below, so this widens what gets checked without
    # crediting anything the resume does not actually say.
    keywords = [
        term
        for term in (*candidates, *fallback_matched, *fallback_missing)
        if _is_ats_keyword(term) and _is_candidate_skill(term)
    ]
    excluded = [
        term
        for term in candidates
        if _is_ats_keyword(term) and not _is_candidate_skill(term)
    ]

    unique: dict[str, str] = {}
    for keyword in keywords:
        unique.setdefault(keyword.casefold(), keyword)
    resume_text = _ats_source_text(json_resume)
    matched = [
        original
        for normalized, original in unique.items()
        if normalized in resume_text
    ]
    missing = [
        original
        for normalized, original in unique.items()
        if normalized not in resume_text
    ]
    score, report = _compute_ats(matched=matched, missing=missing)
    report["scoring"] = "deterministic_final_document"
    report["model_reported_matched"] = fallback_matched
    report["model_reported_missing"] = fallback_missing
    report["prose_requirements"] = prose
    report["excluded_non_skills"] = excluded
    return score, report
