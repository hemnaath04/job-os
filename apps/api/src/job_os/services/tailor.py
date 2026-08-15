"""Resume tailoring agent.

Loads the user's master ResumeVersion + a target Job + every verified
ProfileFact and FactBullet, then asks Claude (via the configured Manifest
gateway) which facts/bullets to include and how to lightly edit them. Python
assembles the final JSON Resume deterministically from the agent's decisions
so the no-hallucination contract is enforced server-side, not in the prompt.

The flow is analyse, then compose, then repair only if a repair can honestly
help:
  1. Python derives the scored requirement list from the JD and word-matches
     every requirement against the whole evidence vault. Free, and it settles
     most of them.
  2. One analyst model call reads only the leftovers and says which existing
     bullet covers each one under a different name, and which are real gaps.
  3. One writing pass composes the resume with that rubric and that plan in hand.
  4. Python scores the assembled document. A repair pass runs only when there is
     something a repair could fix: a writing flag, or a requirement the vault
     genuinely holds and the writer failed to surface.

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
import httpx
import structlog
from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from job_os.schemas.resumes import (
    GapQuestion,
    ProvenanceEntry,
    SelectedBullet,
    TailorAgentOutput,
    TailorAnalysis,
)
from job_os.services.career_ops_rules import CAREER_OPS_RULES, UNPRINTABLE_SKILLS
from job_os.services.identity import identity_text as _identity_text
from job_os.services.llm_json import (
    EMPTY_REPLY_RETRY,
    JSON_ONLY_RETRY,
    create_message,
    parse_model_json,
    response_diagnostics,
    response_text,
)
from job_os.services.resume_writing import (
    MAX_PROJECT_BULLETS,
    MAX_SKILL_GROUPS,
    MAX_WORK_BULLETS,
    bullet_flags,
    dedupe_bullets,
    document_quality_flags,
    drops_team_credit,
    normalize_dashes,
    records_provisional_status,
    upgrades_status,
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
    # When this fact was last written. Used only to decide, between two facts
    # about the same job, which WORDING of the title the candidate is currently
    # using. An ISO string rather than a datetime because the two backends store
    # it differently and only the ordering matters.
    updated_at: str | None = None


@dataclass(frozen=True)
class TailorStage:
    """One honest, user-visible step of a tailor run.

    The run takes minutes, so a silent bar reads as a hang. Every field here is
    something that actually happened: `step` is a stable id the UI keys its
    checklist off, `label` is the line a person reads, and `detail` is a fact
    measured from the run so far. Nothing in here is a timer or a guess, which
    means the screen only changes when the work does.
    """

    step: str
    label: str
    detail: str | None = None
    pct: float = 0.0


# The old flow drafted blind and then spent up to five more full rewrites, each a
# two-minute model call, rediscovering two things Python already knew: the exact
# requirement list the score is computed from, and the exact writing rules the
# penalty is computed from. A measured run took 579 seconds over five passes to
# climb 49 -> 87 against a list `_jd_requirements` derives in under a millisecond.
#
# So the answer key is handed over before a word is written. Python matches every
# requirement against the whole evidence vault, one analyst pass decides which of
# the leftovers existing work covers under another name, and only then does the
# writer compose, with the rubric in hand.
#
# Two, measured rather than chosen. On the same good-fit posting, allowing three
# writing passes took 406s and finished on Job Match 73.9; allowing two took 188s
# and finished on the same 73.9. The third pass bought nothing but the wait. On a
# stretch posting the loop stops itself after one.
MAX_COMPOSE_PASSES = 2
# Below this the gain is not worth another call and a longer wait. A repair pass
# is told exactly what is wrong, so one that lands flat has hit the limit of the
# evidence rather than paused on its way up, and the old two-pass patience only
# bought a slower way to reach the same answer.
MIN_IMPROVEMENT = Decimal("0.5")
TARGET_ATS_SCORE = Decimal("80")

# Generous ceiling on purpose, and larger than the output alone needs, because the
# gateway routes to a model with extended thinking and `max_tokens` covers the
# thinking block too. A refine pass on a long conversation spent the entire 16000
# on thinking and returned zero text blocks: stop_reason max_tokens,
# block_types ['thinking'], output_tokens 16000. Every pass after that point failed
# the same way, which silently capped the loop at two or three passes however high
# the pass budget was set. The budget has to hold the reasoning AND the answer.
DRAFT_MAX_TOKENS = 32000
# More room again for the recovery attempt, since the reason it is happening is
# that the answer did not fit. It must exceed what thinking already consumed, or
# the retry reproduces the failure exactly.
RETRY_MAX_TOKENS = 48000
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
4. `selected_fact_ids` decides only the sections that are genuinely optional:
   projects, volunteering and certifications. List ids for those. Education, work
   experience, skills, publications and awards are always rendered, so listing
   their ids changes nothing and only costs you output length. Order doesn't
   matter (Python sorts by date / section).
   On certifications, be selective. A certificate earns its line when it is
   evidence for THIS role; a generic or dated course certificate does not, and the
   space is worth more as another project bullet. Leaving all of them out is a
   normal outcome.
5. `summary_objective` is a 1-2 sentence tailored summary line for the
   resume's basics.summary, or null to keep the master's summary. It prints at
   the top of the page, so keep it under 45 words and do not restate a JD
   requirement in the employer's own words ("a strong grasp of data structures,
   algorithms, and systems"). Say what he has built, not what they asked for.

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
- Where the evidence names a TEAM, the rewrite keeps the team. Opening with a
  real verb and deleting the people is not a fix, it is a bigger claim: "was
  part of a team building an AI agent" becomes "Built, with a team, an AI agent
  that ...", never "Built agentic workflows". Python reverts a rewrite that
  drops the team back to the verified wording, so you lose the better opener too.
- Never upgrade a fact's STATUS. If the evidence says demoed, pending approval,
  prototype, hackathon build, trial or mock, the bullet cannot say shipped,
  launched, released, delivered or in production, and neither can the summary.
  Carry the qualifier through in the bullet instead: "demoed end to end, pending
  approval" is a stronger line than a claim an interviewer punctures in one
  question. The summary names capabilities and need not repeat every qualifier,
  but it must not claim provisional work shipped, and it must not pluralise one
  instance into several ("knowledge-distillation pipelines" when there is one).
- 30 words maximum per bullet, one idea each. Cutting a verified bullet down is
  always allowed and usually improves it. Growing one is how padding gets in.
- No first person. No "I", "my", "we", "our".
- No em dashes, en dashes or double hyphens. Use commas, colons or periods.
- Banned words, with no exceptions: leveraged, utilized, spearheaded,
  cutting-edge, state-of-the-art, innovative, robust, seamlessly, synergized,
  revolutionized, facilitated, enabled, end-to-end. They are the vocabulary of a
  brochure, and a reader who has seen twenty resumes today reads them as filler
  or as machine-written. Say the plain verb: built, wrote, tested, migrated,
  measured, fixed. "Utilized Python to facilitate data ingestion" is
  "Wrote a Python ingestion job".
- Prefer the constraint over the technique. "Running the big model on every
  image cost too much, so it labelled a small set and a smaller model scored the
  rest" beats "implemented knowledge distillation": it shows the judgement, which
  is the part an interviewer asks about.
- Vary the opening verb. Three bullets in a row starting "Built" reads as
  machine-written.
- Where two bullets in the profile describe the SAME work in different words,
  pick the single best wording. Never select both.

FILL THE PAGE, WITH EVIDENCE, NEVER WITH PADDING, AND DO NOT OVERFLOW IT. The
output is exactly one Letter page. A half-empty resume reads as a thin candidate,
and a page that spills is a two-page resume, which is a failure and not a fuller
one. The budget, measured on real renders: roles and projects together hold about
30 lines, counting one line for each entry's heading plus one line for every 13
words of bullet. One role at 4 bullets and two projects at 3 fits. Add a third
project and something has to come out.
Aim for 9 to 11 bullets across the whole page, 3 to 4 on each role and 2 to 3 on
each project. Those are also the ceilings: Python cuts the surplus at 4 per role
and 3 per project, so choose deliberately rather than listing everything. When a
JD is a poor fit, lead with the closest honest evidence and still fill the page;
do not shrink the resume to signal the mismatch, that is what gap_questions are
for. A project that owns only ONE bullet still earns its place when it is
relevant and the budget has room, and the reader has no way to know the profile
held only one bullet for it.

ATS:
- `ats_keywords_matched`: JD keywords that appear in your selected bullets or
  facts AFTER your rewrites.
- `ats_keywords_missing`: JD keywords that do not appear and have no matching
  fact. These usually become gap_questions too.

KEEP THE NON-BULLET OUTPUT SHORT. The bullets are the product; the rest is
scaffolding the user skims once, and every extra word there is time the user
waits for no gain:
- `gap_questions.why_no_match`: a short phrase, not a sentence. "no cloud
  deployment in the profile", not a paragraph explaining it. The requirement
  plus the phrase is all the user needs to fill it or dismiss it.
- `agent_note`: one sentence, under 25 words. A quick orientation for the user,
  not a recap of your reasoning. The reasoning belongs in the resume, not here.

TREAT THIS PASS AS THE ONE THAT SHIPS. The analysis is done, the rubric is in
front of you, and there is at most one repair pass behind you, each one costing
the user another minute of waiting. Everything below is measured by Python the
moment you answer, and a pass that leaves any of it on the table is a pass that
wasted the user's time:
- Every JD requirement you can honestly surface, surfaced, by renaming what was
  actually built rather than by appending the JD's words.
- Every bullet under 30 words, opening with a different concrete past-tense verb,
  no first person, no em dashes, no two bullets about the same work, no clause
  repeated between two bullets in the same entry.
- Every number the verified bullet already carries, kept. A bullet that has a
  metric and loses it in your rewrite is strictly worse than the original.
- The page filled: 3 to 4 bullets per role, 2 to 3 per project, 3 to 4 projects.
- A summary line that names capabilities without claiming provisional work
  shipped.
Work through that list before you answer, not after.

Output: a single JSON object matching the provided schema. No prose, no fences.
"""


ANALYST_SYSTEM_PROMPT = """\
You are the analyst step of a resume tailoring pipeline. You do not write the
resume and you do not choose its final wording. You answer one question: for each
job requirement below, does the candidate's VERIFIED evidence already describe
that work under a different name, or is it a genuine gap?

Our scorer has already word-matched every requirement against the whole profile.
The ones you are given are exactly the ones whose own words appear NOWHERE in it,
so a verbatim match is not what you are hunting for. You are hunting for the same
work called something else:
- "built an LLM retrieval system over Pinecone" IS retrieval-augmented generation
  and IS a vector store.
- "an agent that delegates to specialised sub-agents" IS multi-agent orchestration.
- "fine-tuned a transformer with low-rank adapters" IS LoRA.
- "deployed on Azure Functions" IS Azure.

Every requirement gets one of two answers and never a third:
1. `covered`: the ONE fact_bullet_id whose underlying work genuinely demonstrates
   it, plus `rename`, a short instruction for how to reword that bullet so the
   employer's term appears in place of the candidate's own. A rename REPLACES
   wording. It never appends the requirement to the end of a bullet, and it never
   adds a metric, a technology or a claim the bullet does not already carry.
2. `gaps`: the requirement, and one plain sentence on why nothing in the profile
   demonstrates it.

A requirement you are unsure about is a gap. Being wrong towards `covered` puts a
claim on a resume the candidate cannot defend in an interview, which is the exact
failure this pipeline exists to prevent. Being wrong towards `gaps` costs a few
points on a coverage number. Those are not comparable, so when the evidence is
thin, say gap.

Two more decisions the writer should not have to rediscover:
- `shortlist_fact_ids`: the 3 or 4 project facts, plus any certification that is
  real evidence for THIS job, strongest first. The page holds that many, a
  half-empty page reads as a thin candidate, and a flagship project left off is a
  worse mistake than a keyword left uncovered.
- `positioning`: one sentence on how to frame this candidate for this role, in
  terms of what he has actually built. No employer adjectives.

Output: a single JSON object matching the provided schema. No prose, no fences.
"""

# The analyst returns a short object, but the budget has to hold the extended
# thinking in front of it as well, and a JD with thirty unresolved requirements is
# not a short thing to reason about. A real run capped at 16000 came back cut off
# mid-object at column 6650, which cost the writer the whole plan silently and
# then, worse, made every remaining miss look unreachable and ended the run a pass
# early at Job Match 61 where the same flow with a working analysis reached 78.
ANALYSIS_MAX_TOKENS = 32000

# Sections Python renders whether or not the writer selects them. A requirement
# already named inside one of these is on the page for free, and telling the
# writer to chase it wastes a bullet on work that is already done.
_ALWAYS_RENDERED_KINDS = frozenset(
    {"skill", "education", "publication", "award", "experience"}
)


class TailorGraphState(TypedDict):
    """State shared by the compose → score → repair LangGraph."""

    messages: list[anthropic.types.MessageParam]
    best_agent: TailorAgentOutput | None
    best_score: Decimal
    iteration_scores: list[float]
    # What the next pass has been asked to fix, in the words the user will read.
    # A repair step that says only "pass 2" tells them nothing about whether the
    # wait is buying anything.
    repair_note: str | None
    done: bool


def _log_prompt_cache(step: str, iteration: int, message: Any) -> None:
    """Report whether this call's prompt was read from cache or paid for again.

    The Manifest gateway caches prompts on its own, without this code asking. It
    is not documented and nothing here controls it, so it is logged rather than
    assumed: every call in a measured run reported input_tokens=2 with the rest
    read or written as cache, and a repair pass read back the whole prefix its
    first pass had written. If that ever stops, the repair passes quietly start
    costing ten times what they cost now, and this line is what shows it.

    Marking our own cache_control breakpoints on top of that was tried and
    reverted. Probed against the live gateway, an unmarked prompt cached exactly
    as well as a marked one: cold wrote 2475 tokens, the same prompt again read
    2475, and a grown conversation read back 8486 of its 8504. The one thing
    marking did add was letting the analyst, the writer and the reviewer share
    one entry for the rules they all start with, worth about two thousand tokens
    of write turned into a read, twice a run. That is under a cent and well
    under a second against a run that takes minutes, and it is not worth
    splitting the writer's system prompt to get.
    """
    usage = getattr(message, "usage", None)
    log.info(
        "tailor.prompt_cache",
        step=step,
        iteration=iteration,
        cache_read=getattr(usage, "cache_read_input_tokens", None),
        cache_written=getattr(usage, "cache_creation_input_tokens", None),
        uncached_input=getattr(usage, "input_tokens", None),
    )


def _plural(count: int, noun: str) -> str:
    """"1 gap" / "3 gaps", for progress lines and notes a person reads."""
    if count == 1:
        return f"1 {noun}"
    suffix = "es" if noun.endswith(("s", "x", "ch", "sh")) else "s"
    return f"{count} {noun}{suffix}"


def _repair_note(flag_count: int, reachable: list[str]) -> str:
    """What the repair pass is actually going after, said plainly."""
    parts: list[str] = []
    if flag_count:
        parts.append(_plural(flag_count, "writing problem"))
    if reachable:
        parts.append(f"{_plural(len(reachable), 'requirement')} your evidence covers")
    return "Fixing " + " and ".join(parts) if parts else "Looking for a better draft"


def _refine_prompt(
    *,
    coverage: Decimal,
    penalty: Decimal,
    reachable: list[str],
    unreachable: list[str],
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
            "same work, so keep only the better one. repeated_phrase means two "
            "bullets in one entry share a clause word for word, so cut it from "
            "the weaker bullet. repeated_opening_verb means vary the verb. "
            "weak_opener means start with a real past-tense verb. first_person "
            "means remove I/my/we. upgraded_status means you claimed something "
            "shipped that the evidence records as pending or a prototype, which "
            "includes calling it production or production-ready. "
            "dropped_team_credit means the evidence says a team did this work and "
            "your rewrite deleted the team: keep a real opening verb AND the team, "
            "as in 'Built, with a team, an AI agent that ...'. "
            "unevidenced_domain means the summary claims a subject-matter domain "
            "that none of the selected bullets demonstrate: either drop that "
            "domain from the summary, or select a project that actually shows it. "
            "dash "
            "means replace an em dash with a comma or a colon. thin_page means "
            "the resume stops short of filling its one page: select another "
            "relevant project, or another verified bullet on one you already "
            "chose, up to 4 per role and 3 per project. over_page is the opposite "
            "and is worse, because the content spills onto a second page: cut the "
            "weakest project or its weakest bullet, or shorten the longest "
            "bullets, until it fits.",
        ]
    if unreachable:
        lines += [
            "",
            "These requirements are absent because the profile does not hold the "
            "work, which Python has confirmed against every verified fact and "
            "bullet. They are gap_questions. Do not spend a sentence, a rename or "
            f"a skills entry on them: {json.dumps(unreachable)}",
        ]
    if reachable:
        lines += [
            "",
            "Requirements still absent from the assembled resume that your own "
            f"evidence CAN cover: {json.dumps(reachable)}",
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
            updated_at=f.updated_at.isoformat() if f.updated_at else None,
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
    on_progress: Callable[[TailorStage], None] | None = None,
) -> tuple[dict[str, Any], list[ProvenanceEntry], list[GapQuestion], Decimal, dict[str, Any], str]:
    """Backend-agnostic tailoring agent.

    Reads the job against the evidence, composes once with the scoring rubric in
    hand, repairs only what a repair can honestly fix, then assembles the JSON
    Resume deterministically. No DB access, so both the FastAPI backend and the
    Appwrite Function share this exact agent flow.

    `on_progress(stage)` is an optional hook receiving a `TailorStage` per step.
    The FastAPI Postgres path passes nothing, so it is a no-op there; the Appwrite
    Function passes a callback that writes the stage onto the agent job row so the
    browser can show what the run is actually doing.
    """
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set; cannot run the tailoring agent.")

    def report(step: str, label: str, detail: str | None, pct: float) -> None:
        if on_progress:
            on_progress(TailorStage(step=step, label=label, detail=detail, pct=pct))

    # One fact per real job, degree, project or skill before the model sees
    # anything. Re-importing a resume mints a second fact for the same job with
    # the bullets reworded, and showing the model both is what produced a role
    # with seven highlights, three of them saying the same thing twice.
    facts, bullets_by_fact = _merge_duplicate_facts(facts, bullets_by_fact)
    facts_payload = _build_facts_payload(facts, bullets_by_fact)
    bullets_by_id = {b.id: b for bs in bullets_by_fact.values() for b in bs}
    facts_by_id = {f.id: f for f in facts}

    # Read the job the way the scorer reads it, before spending a model call on
    # guessing. `_jd_requirements` is the same function that grades the finished
    # page, so this is the rubric rather than an approximation of it, and
    # `_requirement_coverage` then says which requirements the vault already
    # answers. Both are pure Python and take under a millisecond.
    requirements, _prose, _excluded = _jd_requirements(jd_parsed)
    coverage = _requirement_coverage(
        requirements, _evidence_items(facts, bullets_by_fact)
    )
    must_have = [req for req in requirements if not req.preferred]
    backed = [req for req in must_have if coverage[req.label].found]
    report(
        "read_role",
        "Reading the role",
        f"{_plural(len(must_have), 'must-have requirement')}, "
        f"{len(requirements) - len(must_have)} nice to have",
        0.06,
    )
    report(
        "match_evidence",
        "Matching your verified evidence",
        f"{len(backed)} of {len(must_have)} already backed by your profile",
        0.14,
    )

    client = anthropic.AsyncAnthropic(
        auth_token=settings.anthropic_api_key,
        base_url=settings.anthropic_base_url or None,
    )

    # Only the requirements Python could not settle reach a model, and the one
    # that reads them returns a short mapping rather than a resume.
    unresolved = [req for req in requirements if not coverage[req.label].found]
    analysis = TailorAnalysis()
    if unresolved:
        report(
            "find_gaps",
            "Finding the real gaps",
            f"Checking {_plural(len(unresolved), 'requirement')} your wording does "
            "not already name",
            0.20,
        )
        analysis = await _analyse_requirements(
            client,
            # Sonnet, though this step only classifies, and classifying is what
            # a cheap tier is for. Haiku on the fast tier was measured against it
            # and it really is faster: 10.7s against 21.1s on a short JD, 18.9s
            # against 67.5s on a long one, and 238s against 353s for the whole
            # run. On the short JD it is also just as good, same Job Match, same
            # page.
            #
            # It was dropped on the long JD, where it answered far less: 1,672
            # tokens of analysis against Sonnet's 8,018, leaving most of the
            # unresolved requirements unclassified. The writer got a thin plan
            # and the page ended on Job Match 52.2 where the Sonnet plan reached
            # 73.9 and 78.3. Those are real matches the candidate can defend,
            # dropped to save two minutes.
            #
            # Worth knowing that the honesty review does not catch this. It
            # scored the Haiku pages 90 and 95, its best pair, because a resume
            # that claims less is easier to defend. Job Match is the number that
            # moved, so Job Match is the number that decides this.
            model=settings.anthropic_model_tailor,
            tier=settings.manifest_tier_sonnet,
            jd_parsed=jd_parsed,
            jd_clean=jd_clean,
            facts_payload=facts_payload,
            unresolved=unresolved,
            valid_bullet_ids=set(bullets_by_id),
        )
        report(
            "find_gaps",
            "Finding the real gaps",
            f"{len(analysis.covered)} covered by work you have already done, "
            f"{_plural(len(analysis.gaps), 'genuine gap')}",
            0.32,
        )
    else:
        report(
            "find_gaps",
            "Finding the real gaps",
            "Your profile already answers every requirement",
            0.32,
        )
    # Whether the gaps were genuinely checked, as opposed to left unchecked by an
    # analyst call that failed or came back empty. Only a checked gap proves a
    # requirement is out of reach, and only proof is worth ending a run on.
    analysis_settled = not unresolved or bool(analysis.covered or analysis.gaps)

    user_prompt = _build_user_prompt(
        jd_parsed=jd_parsed,
        jd_clean=jd_clean,
        master_json_resume=master_json_resume,
        facts_payload=facts_payload,
        briefing=_requirement_briefing(
            requirements,
            coverage,
            status=_status_briefing(facts, bullets_by_fact),
        ),
        plan=_analysis_block(
            analysis, bullets_by_id=bullets_by_id, facts_by_id=facts_by_id
        ),
    )

    graph = StateGraph(TailorGraphState)

    # The keyword set has to be the same on every pass or the passes are not
    # comparable. It is seeded from the JD and widened once, by the first pass's
    # own term lists, which is how skills buried inside a prose requirement get
    # recovered. After that it is frozen: a later pass that simply names more
    # missing keywords was otherwise growing the denominator and scoring itself
    # down, which made a genuine improvement look like a regression and ended the
    # loop early. Real run: coverage 25.0 then 14.3 on a better draft.
    frozen_terms: dict[str, list[str]] = {}

    async def compose_and_score(state: TailorGraphState) -> TailorGraphState:
        """One writing pass followed by deterministic Python scoring.

        Reached with the rubric and the analyst's findings already in the first
        message, so the first visit here is meant to be the last one.
        """
        iteration = len(state["iteration_scores"]) + 1
        if iteration == 1:
            report(
                "compose",
                "Composing your resume",
                f"Writing from {_plural(len(bullets_by_id), 'verified bullet')}",
                0.36,
            )
        else:
            report(
                "repair",
                "Tightening the weak spots",
                state.get("repair_note") or None,
                min(0.82, 0.36 + 0.30 * (iteration - 1)),
            )
        try:
            msg = await create_message(
                client,
                model=settings.anthropic_model_tailor,
                max_tokens=DRAFT_MAX_TOKENS,
                system=f"{CAREER_OPS_RULES}\n\n{SYSTEM_PROMPT}",
                messages=state["messages"],
                extra_headers={"x-manifest-tier": settings.manifest_tier_sonnet},
            )
        except (anthropic.APIError, httpx.HTTPError) as exc:
            # A refine pass is an improvement on something that already works, so a
            # transient gateway failure on one must never throw away the passes that
            # succeeded. A real run reached 78.3 over three good passes and then lost
            # all of it to a 429 on the fourth, which is the opposite of the point:
            # running the loop harder should not make the whole call more fragile.
            #
            # `httpx.HTTPError` because a stream that dies mid-reply raises the
            # transport error raw, past every anthropic class, and a run that lost
            # a good pass to one would be this comment's own failure again.
            log.warning(
                "tailor.pass_failed_keeping_best",
                iteration=iteration,
                error=repr(exc)[:200],
                have_best=state["best_agent"] is not None,
            )
            if state["best_agent"] is not None:
                return {**state, "done": True}
            # Nothing to ship yet, so the caller has to hear about it.
            raise
        _log_prompt_cache("compose", iteration, msg)
        raw = response_text(msg)
        try:
            attempt = parse_model_json(TailorAgentOutput, raw)
        except ValidationError as e:
            log.warning(
                "tailor.invalid_json",
                error=str(e),
                preview=raw[:400],
                iteration=len(state["iteration_scores"]) + 1,
                **response_diagnostics(msg),
            )
            if state["best_agent"] is not None:
                return {**state, "done": True}
            # No good pass yet, so a chatty or truncated reply would sink the
            # whole run. Ask once more before giving up, and treat an empty reply
            # differently from a chatty one: a model that produced nothing at all
            # ran out of output room, so telling it "that was not valid JSON" and
            # handing it back "(empty)" as its own turn helps nobody. Ask for the
            # same decisions in less text, with more room to land them.
            empty = not raw.strip()
            retry_messages: list[anthropic.types.MessageParam] = (
                [*state["messages"], {"role": "user", "content": EMPTY_REPLY_RETRY}]
                if empty
                else [
                    *state["messages"],
                    {"role": "assistant", "content": raw[:4000]},
                    {"role": "user", "content": JSON_ONLY_RETRY},
                ]
            )
            retry = await create_message(
                client,
                model=settings.anthropic_model_tailor,
                max_tokens=RETRY_MAX_TOKENS if empty else DRAFT_MAX_TOKENS,
                system=f"{CAREER_OPS_RULES}\n\n{SYSTEM_PROMPT}",
                messages=retry_messages,
                extra_headers={"x-manifest-tier": settings.manifest_tier_sonnet},
            )
            retry_raw = response_text(retry)
            try:
                attempt = parse_model_json(TailorAgentOutput, retry_raw)
            except ValidationError as retry_error:
                log.warning(
                    "tailor.invalid_json_after_retry",
                    preview=retry_raw[:400],
                    **response_diagnostics(retry),
                )
                raise RuntimeError(
                    "Tailoring agent returned an invalid response."
                ) from retry_error
            raw = retry_raw

        # Score the resume this pass would actually ship, not the model's own
        # account of how it did. Self-reported matched/missing counts were
        # trivially gamed by claiming more matches, and the loop duly learned to
        # paste JD phrases onto unrelated bullets to raise a number nobody
        # outside the loop ever saw.
        document, _provenance, summary_rejection = _build_document(
            attempt,
            facts=facts,
            bullets_by_fact=bullets_by_fact,
            master_json_resume=master_json_resume,
            facts_payload=facts_payload,
        )
        frozen_terms.setdefault("matched", list(attempt.ats_keywords_matched))
        frozen_terms.setdefault("missing", list(attempt.ats_keywords_missing))
        matched_share, coverage_report = _compute_ats_from_document(
            jd_parsed=jd_parsed,
            json_resume=document,
            fallback_matched=frozen_terms["matched"],
            fallback_missing=frozen_terms["missing"],
        )
        quality = document_quality_flags(document)
        if summary_rejection:
            # A refused summary leaves the page without its lede, so it costs the
            # pass points and the model is told why.
            quality["basics.summary"] = [summary_rejection]
        penalty = _quality_penalty(quality)
        score = matched_share - penalty
        scores = [*state["iteration_scores"], float(score)]
        flag_count = sum(len(flags) for flags in quality.values())
        missing = list(coverage_report.get("missing") or [])
        reachable = _reachable_missing(missing, coverage=coverage, analysis=analysis)
        unreachable = [label for label in missing if label not in reachable]
        log.info(
            "tailor.iteration",
            iteration=len(scores),
            score=float(score),
            coverage=float(matched_share),
            penalty=float(penalty),
            quality_flags=sorted(
                {flag for flags in quality.values() for flag in flags}
            ),
            reachable_missing=reachable,
            unreachable_missing=unreachable,
            target=float(TARGET_ATS_SCORE),
        )
        report(
            # A repair gets its own check step so the browser's checklist only
            # ever moves forwards. Reusing one id would re-activate a row the
            # user already watched tick, which reads as the run going backwards.
            "check_claims" if iteration == 1 else "check_repair",
            "Checking every claim is backed",
            f"Job Match {score.quantize(Decimal('1'))}, "
            + (
                f"{_plural(flag_count, 'writing problem')} to fix"
                if flag_count
                else "no writing problems"
            ),
            min(0.86, 0.62 + 0.18 * (iteration - 1)),
        )

        best_agent = state["best_agent"]
        best_score = state["best_score"]
        best_score_before = best_score
        # `best_agent is None` rather than a numeric sentinel: a pass can now
        # score below zero, since the writing penalty is subtracted from
        # coverage, and a sentinel of -1 meant a heavily penalised first pass was
        # never adopted at all. The run then ended with no agent and raised,
        # failing outright on exactly the resumes that most needed the feedback.
        if best_agent is None or score > best_score:
            best_agent = attempt
            best_score = score

        # Stop when the target is met, when the budget runs out, when the pass
        # gained nothing, or, the rule that matters most on a stretch role, when
        # nothing is left that another pass could honestly fix. A requirement the
        # vault does not hold is not a keyword the writer forgot, it is work the
        # candidate has not done, and the old loop spent four minutes proving that
        # to itself on every stretch job. Python already knows, so the run ends
        # and the gap is reported instead.
        #
        # That last rule only holds when the analyst actually answered. Word
        # matching alone cannot see that a retrieval system IS RAG, so with no
        # analysis "unreachable" means "unchecked", and a truncated analyst reply
        # duly ended a real run one pass early at 61 where the same flow with a
        # working analysis reached 78.
        #
        # The first pass has nothing to improve on, and the starting sentinel is
        # a number a heavily penalised draft can score below, so measuring it
        # against one would end the run before the repair it plainly needs.
        improved = len(scores) == 1 or score > best_score_before + MIN_IMPROVEMENT
        nothing_left = analysis_settled and not reachable and not quality
        done = (
            score >= TARGET_ATS_SCORE
            or len(scores) >= MAX_COMPOSE_PASSES
            or not improved
            or nothing_left
        )
        messages = state["messages"]
        repair_note = None
        if not done:
            repair_note = _repair_note(flag_count, reachable)
            messages = [
                *messages,
                {"role": "assistant", "content": raw},
                {
                    "role": "user",
                    "content": _refine_prompt(
                        coverage=matched_share,
                        penalty=penalty,
                        reachable=reachable,
                        unreachable=unreachable,
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
            "repair_note": repair_note,
            "done": done,
        }

    def route_after_score(state: TailorGraphState) -> str:
        return END if state["done"] else "compose_and_score"

    graph.add_node("compose_and_score", compose_and_score)
    graph.add_edge(START, "compose_and_score")
    graph.add_conditional_edges("compose_and_score", route_after_score)
    compiled_graph = graph.compile()
    graph_result = await compiled_graph.ainvoke(
        {
            "messages": [{"role": "user", "content": user_prompt}],
            "best_agent": None,
            "best_score": Decimal("-1"),
            "iteration_scores": [],
            "repair_note": None,
            "done": False,
        }
    )

    best_agent = graph_result["best_agent"]
    iteration_scores = graph_result["iteration_scores"]

    if best_agent is None:
        raise RuntimeError("Tailoring agent returned no valid response after retries.")
    agent = best_agent

    report("assemble", "Assembling the page", None, 0.90)

    json_resume, provenance, _summary_rejection = _build_document(
        agent,
        facts=facts,
        bullets_by_fact=bullets_by_fact,
        master_json_resume=master_json_resume,
        facts_payload=facts_payload,
    )

    # Same frozen keyword set the loop used, so the score the user sees is the
    # score the loop was working against rather than a differently scaled one.
    ats_score, ats_report = _compute_ats_from_document(
        jd_parsed=jd_parsed,
        json_resume=json_resume,
        fallback_matched=frozen_terms.get("matched") or agent.ats_keywords_matched,
        fallback_missing=frozen_terms.get("missing") or agent.ats_keywords_missing,
    )
    # Display the winning pass's own account of its coverage, not the first
    # pass's, which is all the frozen set is for.
    ats_report["model_reported_matched"] = list(agent.ats_keywords_matched)
    ats_report["model_reported_missing"] = list(agent.ats_keywords_missing)

    # Embed pass-by-pass scores into the report so the FE can show the trail
    # without changing the response schema.
    ats_report["iterations"] = iteration_scores
    ats_report["target_ats_score"] = float(TARGET_ATS_SCORE)
    ats_report["reached_target"] = float(ats_score) >= float(TARGET_ATS_SCORE)
    # What a human reader would hold against the document, alongside what an ATS
    # would. An empty dict is the good outcome and is worth reporting as such.
    ats_report["writing_flags"] = document_quality_flags(json_resume)
    # Which of the misses are the candidate's to close. A requirement absent from
    # every verified fact and bullet is not a keyword the writer skipped, and
    # saying so is more use than another percentage.
    final_missing = list(ats_report.get("missing") or [])
    still_reachable = _reachable_missing(
        final_missing, coverage=coverage, analysis=analysis
    )
    ats_report["missing_needs_new_facts"] = [
        label for label in final_missing if label not in still_reachable
    ]

    # The score and the pass-by-pass trail are not repeated here. Both are
    # already on the page: the score as the Job Match ring, the trail as
    # `ats_report["iterations"]` above, sent structurally rather than as
    # prose. A note that restated them was the same number appearing a
    # third and fourth time on one screen.
    note = agent.agent_note
    passes = len(iteration_scores)
    if ats_score >= TARGET_ATS_SCORE:
        note += f"\n(Hit the Job Match target in {_plural(passes, 'pass')}.)"
    elif ats_report["missing_needs_new_facts"] and not still_reachable:
        note += (
            "\n(Every requirement still missing is one your verified profile "
            "does not hold, so another pass cannot close it. Add the evidence "
            "on your Profile and run this again.)"
        )
    else:
        note += f"\n(Did not reach the Job Match target after {_plural(passes, 'pass')}.)"

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
) -> tuple[dict[str, Any], list[ProvenanceEntry], str | None]:
    """Turn one agent pass into the resume it would actually ship.

    Returns the document, its provenance, and the reason the tailored summary was
    refused if it was, so the loop can tell the model rather than dropping the
    line without explanation.

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
    summary_objective, summary_rejection = _safe_summary(
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
    return json_resume, provenance, summary_rejection


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


_TITLE_QUALIFIER_RE = re.compile(r"\s*(?:·|—|–|\||,|:)\s+")


def _merged_title(variants: list[TailorFact]) -> str:
    """The title to print for one job described by several facts.

    Ranking by evidence decides which fact survives, but it is the wrong signal
    for the TITLE, because the wording a candidate is currently using is the one
    they saved most recently, not the one attached to the most bullets. Re-importing
    a resume a month later with the role reworded is how a person changes how they
    describe it, and the older wording kept winning: a profile holding both
    "Software Test Automation Engineer" and, from five weeks earlier, "Junior
    Software Test Automation Engineer · Client: leading global rideshare platform
    (Fares team)" printed the older one.

    Detail the newer wording dropped is kept rather than lost, since both wordings
    are verified: a trailing qualifier from an older variant whose role name
    matches is appended. This mirrors the field-level merge below, where a field
    only one variant carried is filled in rather than discarded.
    """
    titled = [v for v in variants if (v.title or "").strip()]
    if not titled:
        return variants[0].title
    # Undated facts sort first so a dated one outranks them.
    newest = max(titled, key=lambda v: v.updated_at or "")
    base = newest.title.strip()
    base_identity = _identity_text(base)
    if not base_identity:
        return base
    for variant in titled:
        if variant is newest:
            continue
        parts = _TITLE_QUALIFIER_RE.split(variant.title.strip(), 1)
        if len(parts) < 2:
            continue
        role, qualifier = parts[0], parts[1].strip()
        if not qualifier:
            continue
        # Only append a qualifier that belongs to this same role, judged by the
        # newer wording appearing inside the older one's role name.
        if base_identity not in _identity_text(role):
            continue
        if _identity_text(qualifier) in base_identity:
            continue
        return f"{base}, {qualifier}"
    return base


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
        title = _merged_title(ranked)
        payload: dict[str, Any] = {}
        for variant in reversed(ranked):
            for key, value in (variant.payload or {}).items():
                if value not in (None, "", [], {}):
                    payload[key] = value
        winner = TailorFact(
            id=canonical.id,
            kind=canonical.kind,
            title=title,
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
        # A rewrite can invent no metric and no technology and still promote a
        # demoed prototype into something that shipped.
        if upgrades_status(selected_bullet.rewritten_text, source_context):
            padding_flags.append("upgraded_status")
        # Or keep the work and drop the people. "Was part of a team building an AI
        # agent" came back as "Built agentic workflows", which the review flagged
        # as ownership inflation. Reverting costs nothing: the verified wording is
        # right there and it is already true.
        if drops_team_credit(selected_bullet.rewritten_text, source.text):
            padding_flags.append("dropped_team_credit")
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
) -> tuple[str | None, str | None]:
    """The summary if it is safe to print, plus why it was refused if not.

    The reason matters as much as the refusal. Dropping the line silently cost
    the resume its lede and left the model with no idea it had been rejected, so
    the next pass wrote the same overstatement again. Handing the reason back
    turns a silent loss into something the loop can fix.
    """
    if not summary:
        return None, None
    source = json.dumps(
        {"master": master_json_resume, "facts": facts_payload},
        ensure_ascii=False,
    )
    if (
        set(NUMBER_RE.findall(summary)) - set(NUMBER_RE.findall(source))
        or _technology_terms(summary) - _technology_terms(source)
    ):
        log.warning("tailor.unsafe_summary_reverted")
        return None, "summary_rejected(introduced an unverified metric or technology)"
    # The summary is one line about several facts, so it is the easiest place to
    # promote a status. A real run wrote "has shipped ... an AI agent for
    # automated test generation" about work the fact records as demoed and
    # pending senior approval. Only the facts that the summary could plausibly be
    # describing are checked, which is any fact whose own text says a thing is
    # provisional.
    for fact in facts_payload:
        for bullet in fact.get("bullets") or []:
            # text_is_about_source is False here on purpose, which keeps this
            # check exactly as strict as it has always been. The summary is one
            # line about the whole page and this loop tries it against every
            # bullet in the vault, so a bare "production" cannot be attributed to
            # whichever bullet is currently under test. A real tailored summary
            # reads "backed by experience automating tests for a production
            # rideshare pricing engine", which is true of the client's live
            # system; folding the bullet-level adjective check in here would have
            # rejected it because an unrelated EPAM bullet is provisional, and
            # cost the page its lede. The explicit claims, shipped and launched
            # and in production, still count here as they always did.
            if upgrades_status(
                summary, str(bullet.get("text") or ""), text_is_about_source=False
            ):
                log.warning(
                    "tailor.summary_upgraded_status_reverted",
                    fact=str(fact.get("title") or "")[:80],
                )
                return None, (
                    "summary_rejected(claimed completed or shipped work that "
                    f"{str(fact.get('title') or 'a fact')[:40]} records as "
                    "provisional; describe the capability without claiming it "
                    "shipped, and do not pluralise one instance)"
                )
    return summary, None


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
        if f.kind == "skill":
            # A skill fact is a category and a name. Spelling out its empty
            # dates, location, source_url, payload and bullet list for each of
            # sixty-odd of them crowded the real evidence out of the prompt
            # window, and the ids are never selected against because the skills
            # section is always rendered in full.
            out.append(
                {
                    "id": str(f.id),
                    "kind": "skill",
                    "title": f.title,
                    "category": (f.payload or {}).get("category") or f.org,
                }
            )
            continue
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
    briefing: str,
    plan: str,
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
        f"{briefing}\n\n"
        f"{plan}\n\n"
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
    # Selection-filtered, not always-included. Three undated MOOC certificates
    # printed on every tailored resume regardless of the role, and the independent
    # review called them low signal for an MS CS candidate on four separate runs
    # while asking for the page space to go to a project instead. A credential the
    # agent judges relevant still appears; one it does not is simply left off.
    for f in _facts_of("certification", only_selected=True):
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
_PARENTHETICAL_RE = re.compile(r"\(([^)]*)\)")


def _skill_aliases(keyword: str) -> set[str]:
    """Every spelling that means the same skill as this one.

    A skills row carried both "RAG" and "Retrieval-Augmented Generation (RAG)",
    and both "LLM Integration" and "LLM integration (OpenAI, Anthropic, Qwen)".
    A parenthetical is an expansion or an acronym of what precedes it, so those
    are one skill written twice.

    Deliberately narrow. Matching on token containment instead would fold "Async
    Python" into "Python", which are two different claims that both belong.
    """
    aliases = {_identity_text(keyword)}
    inner = _PARENTHETICAL_RE.findall(keyword)
    base = _identity_text(_PARENTHETICAL_RE.sub(" ", keyword))
    if base:
        aliases.add(base)
    for group in inner:
        # Only a single term in parentheses is an alias for the whole. A list
        # ("OpenAI, Anthropic, Qwen") names providers, not the skill.
        if "," not in group:
            folded = _identity_text(group)
            if folded:
                aliases.add(folded)
    return {alias for alias in aliases if alias}


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
    # Keyed by every spelling a kept skill answers to, mapped to where it is
    # listed, so a later, fuller spelling can replace a bare one in place.
    seen_keywords: dict[str, tuple[list[str], int]] = {}
    for bucket in merged.values():
        unique: list[str] = []
        for keyword in bucket["keywords"]:
            folded = _identity_text(keyword)
            if not folded:
                continue
            if folded in UNPRINTABLE_SKILLS:
                # The fact stays in the vault; it just does not reach the page.
                # The playbook fixes the Languages row, and a skill the candidate
                # cannot defend in an interview costs more than it adds.
                log.info("tailor.skill_withheld_from_page", skill=keyword)
                continue
            aliases = _skill_aliases(keyword)
            existing = next(
                (seen_keywords[alias] for alias in aliases if alias in seen_keywords),
                None,
            )
            if existing is not None:
                target, index = existing
                # "Retrieval-Augmented Generation (RAG)" says everything "RAG"
                # says and more, so the fuller spelling wins the slot. The review
                # read the pair as keyword stuffing, and it was right.
                if len(keyword) > len(target[index]):
                    target[index] = keyword
                continue
            unique.append(keyword)
            for alias in aliases:
                seen_keywords[alias] = (unique, len(unique) - 1)
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
# colon already. `position` is deliberately absent: a job title carrying a client
# tag already has a colon of its own, and normalising to a second one produced
# "Software Test Automation Engineer: Client: leading global rideshare platform",
# which the independent review read as an awkward heading.
_TITLE_FIELDS = frozenset({"name", "title", "studyType", "label"})


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
    r"passion for|genuine interest|interest in|communication skills|"
    # Career stage and spoken languages, which a skills match cannot speak to.
    # A real posting supplied "New grad or early-career engineer" and "English
    # required, French a plus", and both were scored as missing skills.
    r"new grad|early[- ]career|entry[- ]level|years? of experience|"
    r"english|french|german|spanish|mandarin|fluent|native speaker|"
    r"a plus|nice to have|preferred|coursework|thesis|law degree"
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


# Where a prose requirement hides a list of real skills: after a colon, and
# between commas, semicolons, "and" and "or". Deliberately NOT on "/", which
# joins compounds rather than separating items: splitting there turned "CI/CD
# concepts" into "CI" plus "CD concepts" and "Pub/Sub" into "Pub" plus "Sub", and
# scored all four as requirements.
_PROSE_SPLIT_RE = re.compile(r"[,;()]| and | or |\bincluding\b|\bsuch as\b", re.I)
# Nouns a JD hangs off the end of a skill without adding to it. Trimming them lets
# "CI/CD concepts" match the resume's "CI/CD", which it plainly satisfies, instead
# of being scored as a separate unmet requirement.
_FILLER_TAIL_RE = re.compile(
    r"\s+(?:concepts?|thinking|mindset|fundamentals|experience|knowledge|skills?|"
    r"tooling|frameworks?|systems? design|hands[- ]on)$",
    re.I,
)
# A recovered fragment longer than this is a clause someone wrote, not the name of
# a skill. "failure modes" survives; "backend systems that other code calls" and
# "ship production code daily" do not.
_RECOVERED_MAX_WORDS = 3
# A requirement that is about eligibility rather than capability. No resume
# answers these with a skill, so the whole sentence is set aside instead of having
# field names mined out of it: "Currently pursuing a bachelor's or master's in
# Computer Science, Computer Engineering, or a similar technical field" was
# yielding "Computer Engineering" and scoring it as a missing skill.
_ELIGIBILITY_REQUIREMENT_RE = re.compile(
    r"\b(?:currently pursuing|bachelors?|masters?|phd|degree|gpa|grade point|"
    r"junior standing|senior standing|graduation|enrolled|"
    r"work authorization|sponsorship|visa|citizenship|clearance|"
    r"ability to start|able to start|start date|new grad|early[- ]career)\b",
    re.I,
)
# A fragment that opens with one of these is a clause, not a skill name.
_CLAUSE_OPENER_RE = re.compile(
    r"^(?:how|what|why|which|who|where|when|that|with|for|in|on|to|of|at|by|"
    r"from|using|via|about|a|an|the|able|ability|comfortable|experience|"
    r"strong|solid|genuine|currently|at least|minimum|must|willing|"
    # A verb at the front means the sentence's own clause survived the split, not a
    # skill name. "Built APIs" and "deploying code" were both being scored, and
    # neither appears in a resume as written.
    r"built|build|building|ship|ships|shipped|shipping|deploy|deploys|deployed|"
    r"deploying|own|owns|owned|owning|manage|manages|managed|managing|"
    r"work|works|worked|working|write|writes|wrote|writing)\b",
    re.I,
)
# Pronouns give away a clause that survived the opener check.
_PRONOUN_RE = re.compile(r"\b(?:them|they|it|its|you|your|we|our|us|he|she)\b", re.I)
# Splitting "one or more of C++, Python or TypeScript" on " or " leaves the
# fragment "more of C++", which is not a skill and was being scored as one. Strip
# the quantifier rather than dropping the fragment, or the skill goes with it.
_QUANTIFIER_PREFIX_RE = re.compile(
    r"^(?:at least\s+)?(?:one|two|more|either|both|any|all)\s+(?:of\s+)?|^of\s+", re.I
)
# A requirement offering alternatives is satisfied by any ONE of them. Counting
# the others as misses is what pushed a genuine match down: the candidate writes
# Python, so "one or more of C++, Python or TypeScript" is met, yet C++ and
# TypeScript were each scored as a separate failure.
_ALTERNATIVES_RE = re.compile(r"\bor\b", re.I)


def _skills_inside_prose(requirement: str) -> list[str]:
    """The skill names buried in a requirement sentence.

    A JD parser routinely drops a whole sentence into `required_skills`, and the
    real skills sit inside it: "A solid grasp of computer science fundamentals:
    data structures, algorithms, systems" holds three. Recovering them from the
    JD text rather than from the model's own term list is what makes the score
    reproducible. Reading them off the model meant the denominator changed with
    however many keywords that pass happened to enumerate, and the same JD scored
    20.0 on one run and 42.9 on the next.
    """
    tail = requirement.split(":", 1)[-1] if ":" in requirement else requirement
    found: list[str] = []
    for raw in _PROSE_SPLIT_RE.split(tail):
        fragment = _QUANTIFIER_PREFIX_RE.sub("", raw.strip(" .’'\"()")).strip()
        fragment = _FILLER_TAIL_RE.sub("", fragment).strip()
        if _is_recoverable_skill(fragment):
            found.append(fragment)
    return list(dict.fromkeys(found))


def _is_recoverable_skill(fragment: str) -> bool:
    """Whether a fragment pulled out of a sentence names a skill worth scoring."""
    if not fragment or not any(char.isalpha() for char in fragment):
        return False
    if len(fragment.split()) > _RECOVERED_MAX_WORDS:
        return False
    if not _is_ats_keyword(fragment) or not _is_candidate_skill(fragment):
        return False
    return not (_CLAUSE_OPENER_RE.match(fragment) or _PRONOUN_RE.search(fragment))


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


def _mentions(haystack: str, term: str) -> bool:
    """Whether `haystack` names `term` as a word, not merely as a substring.

    A plain `in` test credits a resume for skills it never claims. "RAG" matches
    inside "Cloud Storage", and "Go" matches inside "MongoDB", so a resume listing
    MongoDB was credited with the Go language. Word boundaries are hand-rolled
    rather than \\b because the terms include C++, CI/CD and .NET, where the edge
    character is not a word character and \\b lands in the wrong place.
    """
    cleaned = term.strip()
    if not cleaned:
        return False
    return bool(
        re.search(rf"(?<!\w){re.escape(cleaned.casefold())}(?!\w)", haystack)
    )


@dataclass(frozen=True)
class _Requirement:
    """One thing the JD asks for, and every wording that would satisfy it.

    A requirement rather than a keyword is the scored unit, because a JD asks for
    things, not for strings. "Comfortable with one or more of C++, Python or
    TypeScript" is ONE requirement that Python satisfies outright, and scoring its
    three languages as three separate keywords marked a met requirement two-thirds
    failed.
    """

    label: str
    alternatives: tuple[str, ...]
    preferred: bool

    def covered_by(self, resume_text: str) -> bool:
        return any(_mentions(resume_text, alt) for alt in self.alternatives)


# JD sections that describe what the employer would LIKE, not what the role
# demands. Missing a nice-to-have is not the same failure as missing a must-have,
# and averaging the two together is what made a strong match read as a weak one.
_PREFERRED_FIELDS = ("preferred_skills", "nice_to_have", "bonus_skills")
# Fields where the employer said outright that they require the thing.
_MUST_HAVE_FIELDS = ("required_skills", "qualifications")
# Fields a parser fills with whatever it saw, with no marking either way. A term
# here is a must-have unless the preferred section also names it.
_UNLABELLED_FIELDS = ("technologies", "keywords")
_REQUIRED_FIELDS = _MUST_HAVE_FIELDS + _UNLABELLED_FIELDS


def _jd_requirements(
    jd_parsed: dict[str, Any],
) -> tuple[list[_Requirement], list[str], list[str]]:
    """Turn a parsed JD into scored requirements, plus what was set aside.

    Returns the requirements, the prose requirements reported separately, and the
    terms excluded as things no resume can keyword-match.
    """
    parsed = jd_parsed or {}

    def entries(keys: tuple[str, ...]) -> list[str]:
        out: list[str] = []
        for key in keys:
            value = parsed.get(key, [])
            if isinstance(value, list):
                out.extend(str(item).strip() for item in value if str(item).strip())
        return out

    preferred_entries = entries(_PREFERRED_FIELDS)
    required_entries = entries(_REQUIRED_FIELDS)
    # Anything named anywhere in the preferred section is a nice-to-have, even
    # when the parser also copied it into `technologies`. Real postings do exactly
    # that: a "Nice to have" paragraph listing Weaviate and Terraform, and a flat
    # technologies list that repeats them with no marking.
    preferred_text = " ".join(preferred_entries).casefold()

    requirements: list[_Requirement] = []
    prose: list[str] = []
    excluded: list[str] = []
    seen: set[tuple[str, ...]] = set()

    def add(label: str, alternatives: list[str], *, preferred: bool) -> None:
        unique = list(dict.fromkeys(alt for alt in alternatives if alt.strip()))
        if not unique:
            return
        key = tuple(sorted(alt.casefold() for alt in unique))
        if key in seen:
            return
        seen.add(key)
        requirements.append(
            _Requirement(label=label, alternatives=tuple(unique), preferred=preferred)
        )

    # Terms the employer named as required are must-haves even when the preferred
    # section mentions them too. A posting asking for FastAPI outright and again
    # under "nice to have: production FastAPI systems" is still asking for FastAPI,
    # and reading the preferred mention last demoted it to a bonus.
    #
    # Only the explicitly-required fields count here. Including `technologies` made
    # every technology its own proof of being required, so Weaviate, Terraform and
    # GCP were must-haves on a posting that listed all three under "Nice to have".
    required_text = " ".join(entries(_MUST_HAVE_FIELDS)).casefold()

    def is_bonus(term: str, *, section_is_preferred: bool) -> bool:
        if not section_is_preferred and _mentions(required_text, term):
            return False
        return section_is_preferred or _mentions(preferred_text, term)

    for entry, section_is_preferred in [
        *((entry, False) for entry in required_entries),
        *((entry, True) for entry in preferred_entries),
    ]:
        if _is_ats_keyword(entry):
            if _is_candidate_skill(entry):
                add(
                    entry,
                    [entry],
                    preferred=is_bonus(entry, section_is_preferred=section_is_preferred),
                )
            else:
                excluded.append(entry)
            continue
        # A whole requirement sentence never appears verbatim in a resume, so it
        # is reported rather than scored, and the skills inside it are recovered.
        prose.append(entry)
        if _ELIGIBILITY_REQUIREMENT_RE.search(entry):
            excluded.append(entry)
            continue
        recovered = _skills_inside_prose(entry)
        if not recovered:
            continue
        if _ALTERNATIVES_RE.search(entry) and len(recovered) > 1:
            # "X, Y or Z" is satisfied by any one of them, so it is one unit.
            add(
                entry,
                recovered,
                preferred=is_bonus(entry, section_is_preferred=section_is_preferred),
            )
        else:
            for term in recovered:
                add(
                    term,
                    [term],
                    preferred=is_bonus(term, section_is_preferred=section_is_preferred),
                )

    return requirements, prose, excluded


@dataclass(frozen=True)
class _EvidenceItem:
    """One thing the candidate can point at, and where a reader would find it."""

    where: str
    text: str
    bullet_id: str | None
    always_on_page: bool


def _evidence_items(
    facts: list[TailorFact], bullets_by_fact: dict[str, list[TailorBullet]]
) -> list[_EvidenceItem]:
    """Everything the candidate has, flattened into matchable, citable units."""
    items: list[_EvidenceItem] = []
    for fact in facts:
        label = " at ".join(part for part in (fact.title, fact.org) if part) or fact.kind
        always = fact.kind in _ALWAYS_RENDERED_KINDS
        items.append(
            _EvidenceItem(
                where=f"{fact.kind}: {label}",
                text=" ".join(
                    [
                        fact.title or "",
                        fact.org or "",
                        json.dumps(fact.payload or {}, ensure_ascii=False),
                    ]
                ),
                bullet_id=None,
                # Only the fact's own fields are guaranteed to print. Its bullets
                # are selected, so they are never free.
                always_on_page=always,
            )
        )
        for bullet in bullets_by_fact.get(fact.id, []):
            items.append(
                _EvidenceItem(
                    where=f"{label} bullet {bullet.id}",
                    text=bullet.text,
                    bullet_id=bullet.id,
                    always_on_page=False,
                )
            )
    return items


@dataclass(frozen=True)
class _Coverage:
    """Where a requirement's own words already appear in the vault.

    Split two ways because the writer's job is different in each case. `free`
    means the words sit in a section Python always renders, so the requirement is
    met and chasing it again is wasted page space. `selectable` means a verified
    bullet carries the words and they only reach the page if that bullet is
    picked, which is a decision, not a rewrite.
    """

    free: tuple[str, ...]
    selectable: tuple[str, ...]

    @property
    def found(self) -> bool:
        return bool(self.free or self.selectable)


# Three citations is enough to point the writer at the bullet. Listing every hit
# for a common term like "Python" crowds the real evidence out of the prompt.
_MAX_COVERAGE_CITATIONS = 3


def _requirement_coverage(
    requirements: list[_Requirement], evidence: list[_EvidenceItem]
) -> dict[str, _Coverage]:
    """Word-match every requirement against the whole vault, before any model call.

    This is the half of the old refine loop that never needed a model. The loop
    used to learn, one two-minute pass at a time, that "FastAPI" was sitting in a
    bullet it had not selected. Python can say so for free, so it does.
    """
    coverage: dict[str, _Coverage] = {}
    for requirement in requirements:
        free: list[str] = []
        selectable: list[str] = []
        for item in evidence:
            haystack = item.text.casefold()
            if not any(_mentions(haystack, alt) for alt in requirement.alternatives):
                continue
            bucket = free if item.always_on_page else selectable
            bucket.append(item.where)
        coverage[requirement.label] = _Coverage(
            free=tuple(free[:_MAX_COVERAGE_CITATIONS]),
            selectable=tuple(selectable[:_MAX_COVERAGE_CITATIONS]),
        )
    return coverage


def _requirement_briefing(
    requirements: list[_Requirement],
    coverage: dict[str, _Coverage],
    *,
    status: str = "",
) -> str:
    """The scoring rubric, handed to the model before it writes anything.

    The writer used to guess which terms mattered and find out afterwards. It now
    reads the same list the score is computed from, which is the single change
    that makes a first pass worth shipping.
    """
    must = [req for req in requirements if not req.preferred]
    bonus = [req for req in requirements if req.preferred]
    free = [req.label for req in must if coverage[req.label].free]
    selectable = [
        req
        for req in must
        if not coverage[req.label].free and coverage[req.label].selectable
    ]
    absent = [req.label for req in must if not coverage[req.label].found]

    lines = [
        "HOW THIS PAGE IS SCORED. Our scorer derives the list below from the job "
        "description and measures it against the finished page, so this is the "
        "actual rubric rather than your estimate of it. Job Match = must-have "
        "requirements met, divided by must-have requirements total, minus a "
        "penalty for every writing problem Python finds.",
        "",
        f"MUST-HAVES ({len(must)}). One is met when any of its wordings appears "
        "anywhere on the finished page, including the skills row.",
    ]
    if free:
        lines += [
            "  ALREADY MET, nothing to do. These words sit in sections that always "
            f"render: {', '.join(free)}",
        ]
    if selectable:
        lines.append(
            "  MET ONLY IF YOU SELECT THE BULLET THAT CARRIES THE WORDS, AND YOUR "
            "REWRITE KEEPS THEM:"
        )
        for req in selectable:
            where = "; ".join(coverage[req.label].selectable)
            lines.append(f"    - {req.label}  ->  {where}")
        lines.append(
            "    Selecting the bullet is not enough. A rewrite that drops the word "
            "un-meets a requirement the candidate genuinely satisfies, and that is "
            "the most common way a pass loses points it already had."
        )
    if absent:
        lines += [
            "  WORDED THIS WAY NOWHERE IN THE PROFILE. Either an existing bullet "
            "describes the same work under another name and you rename it, or it "
            f"is a gap_question: {', '.join(absent)}",
        ]
    if bonus:
        lines += [
            "",
            "NICE TO HAVE, reported separately and NOT part of the number, so "
            "never trade a must-have or a clean sentence for one: "
            f"{', '.join(req.label for req in bonus)}",
        ]
    lines += [
        "",
        "This list is a diagnostic, not a checklist to satisfy. Padding a bullet "
        "so a word appears costs more than the word is worth, and it is the one "
        "failure this tool exists to prevent. A requirement you cannot name "
        "honestly stays a gap_question.",
    ]
    if status:
        lines += ["", status]
    return "\n".join(lines)


def _status_briefing(
    facts: list[TailorFact], bullets_by_fact: dict[str, list[TailorBullet]]
) -> str:
    """The work the evidence records as unfinished, named before anything is written.

    Python already refuses a summary that promotes provisional work, and on every
    measured baseline run it refused one, pass after pass, for the same fact. The
    refusal costs the page its opening line and the model only learned about it
    afterwards. Saying it up front is the same rule enforced a minute earlier.
    """
    provisional: list[str] = []
    for fact in facts:
        for bullet in bullets_by_fact.get(fact.id, []):
            if not records_provisional_status(bullet.text):
                continue
            label = " at ".join(part for part in (fact.title, fact.org) if part)
            provisional.append(f"    - {label}: \"{bullet.text[:150]}\"")
            break
    if not provisional:
        return ""
    return "\n".join(
        [
            "WORK YOUR EVIDENCE RECORDS AS UNFINISHED. Neither a bullet nor the "
            "summary may say any of this shipped, launched, was delivered, or is "
            "in production or production-ready. Carry the qualifier through "
            "instead:",
            *provisional,
            "    Python refuses a summary that breaks this, and a refused summary "
            "leaves the page with no opening line at all.",
        ]
    )


def _reachable_missing(
    missing: list[str],
    *,
    coverage: dict[str, _Coverage],
    analysis: TailorAnalysis,
) -> list[str]:
    """Missing requirements another pass could honestly still fix.

    A requirement whose words are in the vault, or that the analyst tied to a real
    bullet, is missing because the writer did not surface it, and a repair pass
    can. One that is in neither is missing because the candidate has not done that
    work, and no number of passes will change it. Separating them is what lets the
    loop stop early against an unreachable target instead of burning the budget.
    """
    from_analysis = {match.requirement.casefold() for match in analysis.covered}
    return [
        label
        for label in missing
        if (label in coverage and coverage[label].found)
        or label.casefold() in from_analysis
    ]


def _analysis_block(
    analysis: TailorAnalysis,
    *,
    bullets_by_id: dict[str, TailorBullet],
    facts_by_id: dict[str, TailorFact],
) -> str:
    """The analyst's findings, rendered for the writer that follows it."""
    if not (
        analysis.covered
        or analysis.gaps
        or analysis.shortlist_fact_ids
        or analysis.positioning
    ):
        # Either every requirement was settled by word matching or the analyst
        # found nothing. Announcing an empty analysis would be worse than saying
        # nothing, because the writer would read it as "there is nothing here".
        return ""
    lines = [
        "YOUR ANALYSIS OF THIS JOB, already done and already checked against the "
        "evidence. Use it rather than redoing it.",
    ]
    if analysis.positioning:
        lines += ["", f"POSITIONING: {analysis.positioning}"]
    if analysis.covered:
        lines += [
            "",
            "REQUIREMENTS EXISTING WORK COVERS ONCE THE BULLET IS REWORDED. Select "
            "each of these bullets and apply the rename. The rename replaces "
            "wording, it is never appended:",
        ]
        for match in analysis.covered:
            source = bullets_by_id.get(match.fact_bullet_id)
            if source is None:
                continue
            lines.append(
                f"    - {match.requirement}  ->  bullet {match.fact_bullet_id}: "
                f'"{source.text[:160]}"'
            )
            if match.rename:
                lines.append(f"        reword as: {match.rename}")
    if analysis.gaps:
        lines += [
            "",
            "GENUINE GAPS. Do not chase these into a bullet. They belong in "
            "gap_questions:",
        ]
        for gap in analysis.gaps:
            lines.append(f"    - {gap.requirement}: {gap.why_no_match}")
    shortlist = [
        facts_by_id[fact_id].title
        for fact_id in analysis.shortlist_fact_ids
        if fact_id in facts_by_id
    ]
    if shortlist:
        lines += [
            "",
            "EVIDENCE WORTH THE PAGE for this role, strongest first: "
            f"{', '.join(shortlist)}. Select these fact ids unless you can say why "
            "one does not belong.",
        ]
    return "\n".join(lines)


async def _analyse_requirements(
    client: anthropic.AsyncAnthropic,
    *,
    model: str,
    tier: str,
    jd_parsed: dict[str, Any],
    jd_clean: str,
    facts_payload: list[dict[str, Any]],
    unresolved: list[_Requirement],
    valid_bullet_ids: set[str],
) -> TailorAnalysis:
    """Read the job against the evidence once, before anything is written.

    Fails soft on purpose. The deterministic briefing already carries most of what
    the writer needs, so a malformed analysis degrades this run to the old
    behaviour rather than sinking it.
    """
    prompt = (
        "JOB DESCRIPTION (parsed):\n"
        f"{json.dumps(jd_parsed or {}, indent=2)}\n\n"
        "JOB DESCRIPTION (clean text, truncated):\n"
        f"<jd>\n{(jd_clean or '')[:8000]}\n</jd>\n\n"
        "CANDIDATE VERIFIED FACTS + BULLETS:\n"
        f"{json.dumps(facts_payload, indent=2)[:12000]}\n\n"
        "REQUIREMENTS WHOSE OWN WORDS APPEAR NOWHERE IN THAT PROFILE:\n"
        f"{json.dumps([req.label for req in unresolved], indent=2)}\n\n"
        "Respond with a single JSON object matching this schema (no prose, no "
        f"fences):\n{json.dumps(TailorAnalysis.model_json_schema())}"
    )
    try:
        msg = await create_message(
            client,
            model=model,
            max_tokens=ANALYSIS_MAX_TOKENS,
            system=f"{CAREER_OPS_RULES}\n\n{ANALYST_SYSTEM_PROMPT}",
            messages=[{"role": "user", "content": prompt}],
            extra_headers={"x-manifest-tier": tier},
        )
        _log_prompt_cache("analyst", 1, msg)
        analysis = parse_model_json(TailorAnalysis, response_text(msg))
    except (anthropic.APIError, httpx.HTTPError, ValidationError) as exc:
        # An empty analysis is a planned degradation: `analysis_settled` downstream
        # knows the gaps were never checked. A dropped stream has to land here too,
        # or the one step that is allowed to fail softly takes the run down with it.
        log.warning("tailor.analysis_failed", error=repr(exc)[:200])
        return TailorAnalysis()
    # A bullet id the analyst invented would send the writer looking for evidence
    # that does not exist, so it never reaches the writer.
    kept = [
        match for match in analysis.covered if match.fact_bullet_id in valid_bullet_ids
    ]
    if len(kept) != len(analysis.covered):
        log.warning(
            "tailor.analysis_dropped_unknown_bullets",
            count=len(analysis.covered) - len(kept),
        )
    return analysis.model_copy(update={"covered": kept})


def _compute_ats_from_document(
    *,
    jd_parsed: dict[str, Any],
    json_resume: dict[str, Any],
    fallback_matched: list[str],
    fallback_missing: list[str],
) -> tuple[Decimal, dict[str, Any]]:
    """Score the JD's must-have requirements against the assembled resume.

    Must-haves set the headline number. Nice-to-haves are scored and reported
    separately rather than averaged in, because a posting that lists five bonus
    technologies would otherwise cap an otherwise-perfect match at half marks. On
    a real AI-engineering posting the candidate genuinely fits, every one of the
    absent terms came from a paragraph headed "Nice to have", and the score read
    35 for it.
    """
    requirements, prose, excluded = _jd_requirements(jd_parsed)
    resume_text = _ats_source_text(json_resume)

    core = [req for req in requirements if not req.preferred]
    bonus = [req for req in requirements if req.preferred]
    core_met = [req for req in core if req.covered_by(resume_text)]
    core_gap = [req for req in core if not req.covered_by(resume_text)]
    bonus_met = [req for req in bonus if req.covered_by(resume_text)]
    bonus_gap = [req for req in bonus if not req.covered_by(resume_text)]

    score, report = _compute_ats(
        matched=[req.label for req in core_met],
        missing=[req.label for req in core_gap],
    )
    report["scoring"] = "deterministic_required_requirements"
    report["model_reported_matched"] = fallback_matched
    report["model_reported_missing"] = fallback_missing
    report["prose_requirements"] = prose
    report["excluded_non_skills"] = excluded
    # Reported so the score can be read as "met 6 of 9 must-haves" rather than as
    # a bare percentage, and so a stretch role reads as a stretch rather than as a
    # broken tool.
    report["required_met"] = len(core_met)
    report["required_total"] = len(core)
    report["preferred_matched"] = [req.label for req in bonus_met]
    report["preferred_missing"] = [req.label for req in bonus_gap]
    report["preferred_coverage"] = (
        float(
            (Decimal(len(bonus_met)) / Decimal(len(bonus)) * Decimal("100")).quantize(
                Decimal("0.1")
            )
        )
        if bonus
        else None
    )
    return score, report
