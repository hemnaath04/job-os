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
from job_os.services.llm_json import (
    JSON_ONLY_RETRY,
    parse_model_json,
    response_text,
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

SEMANTIC KEYWORD MATCHING (this is how you maximise ATS coverage without
inventing experience):
- Look for SEMANTIC equivalents in the candidate's existing bullets, not just
  verbatim string matches. A bullet that says "built an LLM retrieval system
  over Pinecone" already covers BOTH "RAG" AND "vector stores" — rewrite it
  to use the JD's exact terminology so both keywords match.
- "agent orchestration with role-based agents" IS "multi-agent".
- "transformer fine-tuning with low-rank adapters" IS "LoRA fine-tuning".
- "deployed on Azure Functions" IS "Azure".
- "responsible-AI review of the dataset" IS "responsible AI".
- If you can honestly rephrase an existing bullet to surface a missing
  keyword without changing what was actually done, DO IT. That's the whole
  point of tailoring.

ATS:
- `ats_keywords_matched`: JD keywords that appear in your selected bullets or
  facts AFTER your rewrites.
- `ats_keywords_missing`: JD keywords that do not appear and have no matching
  fact — these usually become gap_questions too.

Output: a single JSON object matching the provided schema. No prose, no fences.
"""


class TailorGraphState(TypedDict):
    """State shared by the draft → score → refine LangGraph."""

    messages: list[anthropic.types.MessageParam]
    best_agent: TailorAgentOutput | None
    best_score: Decimal
    iteration_scores: list[float]
    done: bool


def _refine_prompt(prev: TailorAgentOutput, target: Decimal) -> str:
    """Feedback turn after a pass that came in below the ATS target.

    We tell Claude exactly which keywords it left on the table and remind it
    that semantic matches inside existing bullets are fair game. The hard
    no-hallucination constraint stays."""
    matched = prev.ats_keywords_matched
    missing = prev.ats_keywords_missing
    current = (
        Decimal(len(matched)) / Decimal(len(matched) + len(missing)) * Decimal("100")
        if (len(matched) + len(missing)) > 0
        else Decimal("0")
    )
    return (
        f"That pass landed at ATS {current.quantize(Decimal('0.1'))} (target {target}).\n"
        f"Still missing: {json.dumps(missing)}\n\n"
        "Look back at the candidate's facts + bullets. For each missing\n"
        "keyword, find an existing bullet whose underlying claim ALREADY\n"
        "covers the concept (even if the keyword isn't verbatim) and rewrite\n"
        "that bullet to use the JD's exact terminology. Examples of valid\n"
        "rewrites:\n"
        "  - 'built an LLM retrieval system over Pinecone' →\n"
        "    'built a RAG pipeline over Pinecone vector stores'\n"
        "  - 'multi-step agent that delegates to specialised sub-agents' →\n"
        "    'multi-agent orchestration with role-specialised sub-agents'\n"
        "  - 'fine-tuned a transformer with low-rank adapters' →\n"
        "    'LoRA fine-tuning of a transformer'\n\n"
        "Do NOT add a keyword to a bullet whose underlying work does not\n"
        "support it — that's hallucination and breaks the contract. If a\n"
        "keyword can't be honestly surfaced, leave it as a gap_question.\n\n"
        "Return the FULL updated TailorAgentOutput JSON — not a diff. Same\n"
        "schema as before."
    )


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

        score = _agent_quick_score(attempt)
        scores = [*state["iteration_scores"], float(score)]
        log.info(
            "tailor.iteration",
            iteration=len(scores),
            score=float(score),
            target=float(TARGET_ATS_SCORE),
        )
        if on_progress:
            on_progress(f"Scoring pass {iteration}", min(0.85, 0.26 + (iteration - 1) * 0.22))

        best_agent = state["best_agent"]
        best_score = state["best_score"]
        if score > best_score:
            best_agent = attempt
            best_score = score

        done = score >= TARGET_ATS_SCORE or len(scores) >= MAX_ITERATIONS
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
                    "content": _refine_prompt(attempt, TARGET_ATS_SCORE),
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

    if on_progress:
        on_progress("Assembling resume", 0.9)

    json_resume, provenance = _assemble_json_resume(
        master_json_resume=master_json_resume,
        all_facts=facts,
        selected_facts=selected_facts,
        selected_bullets=safe_bullets,
        bullets_by_fact=bullets_by_fact,
        summary_objective=summary_objective,
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
        if added_numbers or added_technologies or wrong_section:
            log.warning(
                "tailor.unsafe_rewrite_reverted",
                bullet_id=str(source.id),
                added_numbers=sorted(added_numbers),
                added_technologies=sorted(added_technologies),
                wrong_section=wrong_section,
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

    def _facts_of(kind: str, *, only_selected: bool = False) -> list[TailorFact]:
        pool = (
            [f for f in all_facts if f.id in selected_fact_ids]
            if only_selected
            else all_facts
        )
        out = [f for f in pool if f.kind == kind]
        out.sort(
            key=lambda f: (f.end_date or date.min, f.start_date or date.min),
            reverse=True,
        )
        return out

    def _bullets_for(f: TailorFact) -> tuple[list[str], list[SelectedBullet]]:
        """Pick the bullet set to render for a fact.

        Prefer agent-selected (tailored) bullets if any exist for this fact.
        Otherwise fall back to ALL the fact's verified bullets — better an
        un-tailored bullet than a blank role on the resume."""
        chosen = by_fact_selected.get(f.id) or []
        if chosen:
            return [sb.rewritten_text for sb in chosen], chosen
        all_b = bullets_by_fact.get(f.id, []) or []
        return [b.text for b in all_b], []

    work: list[dict[str, Any]] = []
    for f in _facts_of("experience"):
        bullets, _picked = _bullets_for(f)
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
        bullets, _picked = _bullets_for(f)
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
        bullets, _picked = _bullets_for(f)
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

    json_resume: dict[str, Any] = {
        "basics": basics,
        "work": work,
        "projects": projects,
        "volunteer": volunteer,
        "education": education,
        "skills": [
            {"name": cat, "keywords": kws} for cat, kws in skills_by_category.items()
        ],
        "certificates": certificates,
        "publications": publications,
        "awards": awards,
    }

    provenance: list[ProvenanceEntry] = []
    for sb in selected_bullets:
        fb = bullet_map[sb.fact_bullet_id]
        provenance.append(
            ProvenanceEntry(
                section=sb.target_section,
                text=sb.rewritten_text,
                fact_bullet_id=fb.id,
                fact_id=fb.fact_id,
            )
        )

    return json_resume, provenance


def _agent_quick_score(agent: TailorAgentOutput) -> Decimal:
    """Mid-loop ATS score from the agent's own matched/missing counts.

    Cheap proxy for `_compute_ats` so we don't have to re-assemble the resume
    inside the iteration loop."""
    total = len(agent.ats_keywords_matched) + len(agent.ats_keywords_missing)
    if total == 0:
        return Decimal("0")
    return (
        Decimal(len(agent.ats_keywords_matched))
        / Decimal(total)
        * Decimal("100")
    ).quantize(Decimal("0.1"))


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


def _is_ats_keyword(term: str) -> bool:
    cleaned = term.strip()
    if not cleaned or len(cleaned) > ATS_KEYWORD_MAX_CHARS:
        return False
    return len(cleaned.split()) <= ATS_KEYWORD_MAX_WORDS


def _is_candidate_skill(term: str) -> bool:
    """False for JD terms a resume could never legitimately match."""
    return not _NON_SKILL_RE.search(term)


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
    resume_text = json.dumps(json_resume, ensure_ascii=False).casefold()
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
