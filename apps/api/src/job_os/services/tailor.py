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
import time
from collections.abc import Callable, Sequence
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
from job_os.services.availability import (
    AVAILABILITY_GAP_REQUIREMENT,
    AVAILABILITY_GAP_WHY,
    AVAILABILITY_GAP_WHY_PARTIAL,
    Availability,
    derive_availability,
    posting_asks_for_availability,
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
    MAX_PAGE_LINES,
    MAX_PROJECT_BULLETS,
    MAX_SKILL_GROUPS,
    MAX_WORK_BULLETS,
    MIN_PAGE_BULLETS,
    bullet_flags,
    dedupe_bullets,
    document_quality_flags,
    drops_team_credit,
    estimated_page_lines,
    mentions_word,
    normalize_dashes,
    over_length_bullets,
    printed_bullets,
    records_provisional_status,
    split_long_bullet,
    unlinked_projects,
    upgrades_status,
)
from job_os.services.role_lane import jd_lanes, text_lanes
from job_os.services.skill_match import alias_variants
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


class TailorInputError(RuntimeError):
    """This posting cannot be tailored against, and no pass would change that.

    Distinct from the agent failing: retrying an agent error can work, retrying
    this one cannot, because the problem is that there is nothing to tailor
    against. The caller writes the message onto the agent job, and the tailor
    page already renders that, so the reason reaches the person who can fix it.
    """


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

# How much of what is actually achievable counts as done.
#
# 80 was a fixed number in a world where the ceiling moves with the posting.
# Measured against a real enterprise AI-engineer JD and this candidate's whole
# vault: 67 requirements, of which the vault can evidence 15, so the honest
# ceiling is 22.4%. The run scored 23.3 -- at the ceiling, the best resume those
# facts can produce -- and was told it had failed, then spent a second compose
# pass trying to beat a maximum. Every pass costs a model call the user waits
# for, and the repair loop spends them chasing requirements no fact can support
# instead of improving the ones that can.
#
# Not 100% of achievable: coverage counts a requirement as reachable when some
# fact touches it, and a page has room for a subset of that. Leaving headroom
# keeps the target honest without making it trivial.
ACHIEVABLE_TARGET_SHARE = Decimal("0.9")

# How much of the posting's own text reaches the writer and the analyst. Named
# rather than inlined twice because it is also the cap the API puts on the field
# it serves the browser (`JD_CLEAN_MAX_CHARS` in schemas/jobs.py): a caller has
# no reason to carry bytes that get truncated on arrival. The two are kept equal
# by hand -- the schema layer deliberately does not import this module, which
# pulls in the Anthropic client and the whole agent.
JD_CLEAN_PROMPT_CHARS = 8000

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

# The gateway's default posture is "high" effort, which on an adaptive-thinking
# model means "almost always thinks" -- the thinking block DRAFT_MAX_TOKENS
# above budgets room for. Measured directly against the live gateway on this
# call's own system prompt: the compose call at high (unset) effort took 37.6s
# with a thinking block consuming most of its 4045 output tokens; the same
# call at "medium" took 19.7s, still thought, and still parsed as valid
# TailorAgentOutput JSON. Applied here and not to the analyst call: a prior
# real run already tried reducing power on THAT step (swapping it to Haiku)
# and lost real, defensible matches the Sonnet analysis had found -- Job Match
# 52.2 against 73.9 and 78.3, see the comment where `_analyse_requirements` is
# called. Compose is a different bet: cutting thinking depth on the WRITER
# rather than the step that decides WHAT gets written, backed by the
# deterministic checks (bullet_flags, document_quality_flags, the
# no-hallucination sanitizers) that exist specifically to catch a bad rewrite
# regardless of which model or effort level produced it, and by the repair
# loop that already re-runs this same call when those checks find something.
COMPOSE_EFFORT = "medium"
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
   For projects specifically, a PROJECT RELEVANCE ranking appears below,
   computed from the same requirements the Job Match number is built from, not
   from a guess. Select the highest-scoring projects the page has room for. A
   more recent date, a longer bullet list, or a more finished feel is not a
   reason to feature a lower-scoring project ahead of a higher-scoring one;
   only a genuine weakness in the higher-scoring project's own verified
   evidence is.
   On certifications, be selective. A certificate earns its line when it is
   evidence for THIS role; a generic or dated course certificate does not, and the
   space is worth more as another project bullet. Leaving all of them out is a
   normal outcome.
5. `summary_objective` is a 1-2 sentence tailored summary line for the
   resume's basics.summary, or null to keep the master's summary. It prints at
   the top of the page, so keep it under 45 words and do not restate a JD
   requirement in the employer's own words ("a strong grasp of data structures,
   algorithms, and systems"). Say what he has built, not what they asked for.
   It is checked against the same banned-word list as every bullet below
   ("end to end" included); a summary that fails it comes back with no
   summary at all, which costs the page its lede for nothing.

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

FEW-SHOT BULLET REWRITING EXAMPLES (Follow these exact patterns):
- Example 1 (Keyword Alignment & Tightening):
  Original: "Developed and maintained Python backend microservices deployed on Azure Functions with Pinecone vector search for automated semantic query processing."
  JD Ask: "Experience with RAG pipelines, vector databases, and cloud deployment."
  Rewrite: "Built a Python RAG pipeline using Pinecone vector databases and deployed serverless microservices on Azure Functions."
- Example 2 (Team Credit & Action Verb):
  Original: "Was part of an internal engineering group that created an LLM evaluation framework evaluating model hallucination rates across 500 test cases."
  JD Ask: "Multi-agent systems, LLM evaluation, and prompt engineering."
  Rewrite: "Built, with an engineering team, an LLM evaluation harness benchmarking model hallucination rates across 500 test cases."
- Example 3 (Status Qualification & Metric Preservation):
  Original: "Prototyped and demoed a real-time data streaming pipeline using Kafka and FastAPI handling 10k events/sec, pending production rollout."
  JD Ask: "Event-driven architecture with Kafka and streaming data."
  Rewrite: "Designed and demoed an event-driven streaming pipeline with Kafka and FastAPI processing 10k events/sec, pending production approval."

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
  A verified bullet that arrives OVER 30 words is not exempt: keeping it whole
  because it is safe leaves the reader a three-line paragraph. Shorten it by
  deleting words, or split it into two bullets at a boundary already in the
  sentence. Deleting is always safe; adding is never.
- Lead with the result, then say how. A bullet reads action, then the outcome
  the evidence records, then the method: "Cut nightly suite runtime from 40 to
  12 minutes by parallelising fixtures" beats "Worked on test performance using
  parallel fixtures". If the verified fact carries a number, that number is the
  reason the bullet exists and it goes near the front.
- If there is no verified number, do not manufacture one and do not gesture at
  one. No "significantly", "drastically", "substantially", "by a large margin".
  Say the concrete thing that happened instead: what was built, what it handles,
  what changed. A specific unquantified bullet is stronger than a vague
  quantified-sounding one, and infinitely stronger than an invented figure.
- No first person. No "I", "my", "we", "our".
- No em dashes, en dashes or double hyphens. Use commas, colons or periods.
- Banned words, with no exceptions: leveraged, utilized, spearheaded,
  cutting-edge, state-of-the-art, innovative, robust, seamlessly, synergized,
  revolutionized, facilitated, enabled, end-to-end (with or without the
  hyphens), foundational, passionate, comprehensive, sophisticated, holistic,
  meticulous, pivotal. They are the vocabulary of a brochure, and a reader who
  has seen twenty resumes today reads them as filler or as machine-written. Say
  the plain verb: built, wrote, tested, migrated, measured, fixed. "Utilized
  Python to facilitate data ingestion" is "Wrote a Python ingestion job".
- Banned phrases, for the same reason: "applying problem solving and
  communication" (name the actual thing done, not the two skills it supposedly
  used), "addressing responsible-AI/security/reliability considerations" (name
  the specific guardrail, e.g. "refusing off-topic queries", not the category
  it belongs to), "a natural-language, prompt-based generative-AI application"
  (say what it actually does: a search, a chatbot, a query answerer). Any
  phrase that names a skill category instead of showing the skill is this same
  failure, banned or not on this list: delete it and say what was built.
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

SKILLS: the Skills section is assembled from the candidate's skill facts above
(the ones with `"kind": "skill"`), grouped by category, exactly as written --
you do not rewrite or select them, and you do not order them either. Python
already reorders each row so the languages and tools THIS posting names come
first among the ones the candidate actually has, in the posting's own order. A
posting asking for "C/C++/Java/Go/Python" is asking for any one of them, and the
row shows the ones in the profile; the ones that are not in the profile are not
added, by you or by anything else. So do not restate a language in a bullet to
compensate for it being absent from the row, and do not treat a language the
candidate does not have as something a rewrite can reach. What you CAN do is
`skills_dedup_drop`: a
list of skill keyword strings, copied verbatim from those facts, that are safe
to remove because the same vendor, tool, or library already appears in another
row. A profile that has one skill titled "LLM integration (OpenAI, Anthropic,
Qwen)" and a separate one titled "OpenAI / Anthropic SDKs" is naming OpenAI and
Anthropic twice; put "OpenAI / Anthropic SDKs" in `skills_dedup_drop` and keep
the fuller listing. This is exactly the no-keyword-stuffing rule above, applied
to the one section that is not bullets: a name is evidence once, and reading it
twice does not add a second name's worth of it. Every string you list here must
match one of the candidate's own skill titles character for character; a string
that does not match anything real is silently ignored, so there is no reward
for guessing. An empty list is a normal, common outcome: most profiles do not
carry a duplicate.

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
  real evidence for THIS job, strongest first. A PROJECT RELEVANCE ranking is
  included below, computed by Python from the same requirements you are
  classifying gaps against: start from that ranking rather than your own read
  of the raw facts list, and only reorder it when a project's own bullets prove
  a stronger fit than its score shows. The page holds that many, a half-empty
  page reads as a thin candidate, and a flagship project left off is a
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
    # Split by who caused it. A flag the page inherited from the vault is not a
    # thing this pass did wrong, and listing it under "fix these first, they
    # cost you points" told the writer to earn back points by departing from
    # verified wording. It is shown, because shortening one is allowed and
    # sometimes possible, and it is shown as costing nothing, because the honest
    # answer is often that his sentence needs all of its words.
    charged = {
        where: [
            flag for flag in flags if INHERITED_FLAG_SUFFIX not in flag.split("(", 1)[0]
        ]
        for where, flags in quality.items()
    }
    inherited = {
        where: [
            flag for flag in flags if INHERITED_FLAG_SUFFIX in flag.split("(", 1)[0]
        ]
        for where, flags in quality.items()
    }
    charged = {where: flags for where, flags in charged.items() if flags}
    inherited = {where: flags for where, flags in inherited.items() if flags}
    if charged:
        lines += [
            "",
            "WRITING PROBLEMS, fix these first. They cost more than a keyword "
            "is worth:",
        ]
        for where, flags in charged.items():
            lines.append(f"  - {where}: {', '.join(flags)}")
    if inherited:
        lines += [
            "",
            "INHERITED FROM THE VERIFIED FACTS. These cost you nothing and you "
            "are not required to fix them:",
        ]
        for where, flags in inherited.items():
            lines.append(f"  - {where}: {', '.join(flags)}")
        lines += [
            "",
            "A _verbatim flag means you printed the verified bullet exactly as "
            "the candidate wrote it, and it is his wording that is long or "
            "repetitive. You may shorten one by DELETING a clause it already "
            "contains. You may not reword it, compress it into new phrasing, or "
            "drop the part that carries the evidence. If it cannot lose a clause "
            "without changing what it claims, print it as it stands: an honest "
            "long bullet beats a short one that says something he did not say.",
        ]
    if quality:
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
            "page_opener means one verb opens most of the bullets on the whole "
            "page, across different roles and projects, which reads as one "
            "sentence rewritten: vary the openers you write. "
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
            "bullets, until it fits. "
            "no_graduation_month_and_year means the education entry shows a bare "
            "year: a recruiter screens on which cycle he is available for, so "
            "carry the month and the year from the verified education fact. "
            "missing_education means the page has no education entry at all. "
            "no_github_link and no_linkedin_link mean the page carries no such "
            "URL: reviewers do click them, so take the URL from the verified "
            "facts or the master resume, and never guess one. "
            "unevidenced_skill names a skill the skills block claims that no "
            "bullet on the page demonstrates: either select a bullet that shows "
            "it being used, or drop it from the skills block, because a "
            "technology listed without showing how it was used is the gap a "
            "reviewer probes first. "
            "no_quantified_bullets means not one bullet carries a number: "
            "surface a figure a verified bullet already has, and small numbers "
            "count. Never invent one to clear this flag, it is the one flag "
            "worth leaving set.",
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
) -> tuple[dict[str, Any], list[ProvenanceEntry], list[GapQuestion], Decimal | None, dict[str, Any], str]:
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


def _finalize_ats_score(
    ats_score: Decimal,
    ats_report: dict[str, Any],
    jd_parsed: dict[str, Any],
) -> tuple[Decimal | None, str | None]:
    """The customer-facing score, or an honest reason there isn't one.

    `_compute_ats`'s total==0 branch returns a confident 0.0 whether the JD
    named zero requirements or the requirement list simply could not be built
    -- a JD-parse failure (gateway timeout, invalid model JSON) reaches here
    with `jd_parsed` empty or `{"parse_incomplete": True, ...}`, and
    `_jd_requirements` correctly reads that as zero requirements too. Those
    are different facts: one is "this resume was never actually measured
    against this JD," which a bare 0% reports as a real, fabricated score.

    Deliberately not inside `_compute_ats`/`_compute_ats_from_document`
    themselves: the compose/repair loop above calls those every iteration, and
    a 0.0 there is a reasonable "no coverage yet" signal for that internal
    loop to keep working with. This runs once, at the one point the score
    leaves `run_tailor` for the caller.
    """
    if jd_parsed.get("parse_incomplete"):
        ats_report["scoring"] = "unavailable_parse_incomplete"
        return None, "unavailable_parse_incomplete"
    if ats_report.get("required_total") == 0:
        # The JD parsed fine and genuinely named nothing this scorer could
        # check -- rare, but a bare 0% here reads as "matches nothing" when
        # the honest fact is "nothing to measure against."
        ats_report["scoring"] = "no_scoreable_requirements"
        return None, "no_scoreable_requirements"
    return ats_score, None


async def run_tailor(
    *,
    facts: list[TailorFact],
    bullets_by_fact: dict[str, list[TailorBullet]],
    master_json_resume: dict[str, Any],
    jd_parsed: dict[str, Any],
    jd_clean: str,
    on_progress: Callable[[TailorStage], None] | None = None,
) -> tuple[dict[str, Any], list[ProvenanceEntry], list[GapQuestion], Decimal | None, dict[str, Any], str]:
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
    # The vault wording behind the page, so the review can tell a bullet the
    # writer padded from one it printed exactly as the fact holds it. Without
    # this the two report identically, and the second was being charged to the
    # writer: `_sanitize_selected_bullets` reverts any rewrite that adds a
    # number or a technology, which correctly teaches the writer that verbatim
    # source is the safe answer, and the penalty then billed it for the length
    # that answer guarantees. Eleven of the fifteen bullets in this user's vault
    # are over the cap, so the loop was paying the model to drift from the
    # verified text on almost every bullet it printed.
    verified_sources = [b.text for b in bullets_by_id.values()]
    # Everything the profile holds, for the skills check. A one-page resume
    # prints three projects out of a whole career, so asking "does this page
    # demonstrate it" of a truthful skills list flagged thirty-four skills on a
    # real run, each costing points: the more complete the vault, the worse the
    # page scored. The question worth asking is whether anything is behind the
    # claim at all, and the vault is where that answer lives.
    vault_evidence = [
        *verified_sources,
        *(f.title for f in facts),
        *(f.org for f in facts if f.org),
        *(
            str(value)
            for f in facts
            for value in (f.payload or {}).values()
            if isinstance(value, str)
        ),
        *(
            str(item)
            for f in facts
            for value in (f.payload or {}).values()
            if isinstance(value, list)
            for item in value
            if isinstance(item, str)
        ),
    ]
    facts_by_id = {f.id: f for f in facts}

    # Read the job the way the scorer reads it, before spending a model call on
    # guessing. `_jd_requirements` is the same function that grades the finished
    # page, so this is the rubric rather than an approximation of it, and
    # `_requirement_coverage` then says which requirements the vault already
    # answers. Both are pure Python and take under a millisecond.
    requirements, _prose, _excluded = _jd_requirements(jd_parsed)
    # Refused here, before a single model call. A posting with no requirements
    # cannot be tailored against: there is nothing to match, nothing to score,
    # and nothing a repair pass could improve.
    #
    # It used to run anyway. A real Jane Street posting, whose parse came back
    # with every list empty and one keyword, produced four runs of an 8-bullet
    # page with `coverage=0.0`, a suppressed score and no analyst step at all,
    # and reported them as successes. The only hint was a parenthetical inside
    # the agent's own note. That is a resume the candidate could send believing
    # it had been aimed at the job.
    #
    # Refusing costs him nothing and saves the run: `_jd_requirements` is pure
    # Python and returns in under a millisecond, so this fires before roughly
    # ninety seconds of gateway time is spent proving what is already known.
    if not requirements:
        if jd_parsed.get("parse_incomplete"):
            raise TailorInputError(
                "This job description could not be read, so there is nothing to "
                "tailor against yet. Try again in a moment, and if it keeps "
                "failing, paste the description in by hand."
            )
        raise TailorInputError(
            "This posting records no requirements, so there is nothing to tailor "
            "against. Open the job and add its description, then tailor again: a "
            "resume built against an empty posting is not aimed at anything."
        )
    coverage = _requirement_coverage(
        requirements, _evidence_items(facts, bullets_by_fact)
    )
    # What kind of engineering this posting is for, which the requirement count
    # cannot see and a reader decides in a glance. Plural, because a full-stack
    # posting is hiring for two kinds at once and reading it as one dropped the
    # product/UI project first on every tie. Used only to break ties between
    # equally-matching projects; see `_evidence_rank`.
    lanes = jd_lanes(jd_parsed, jd_clean)
    # Same idea as the requirement coverage above, applied to the one decision
    # that had no Python signal behind it at all: which of the optional project
    # facts is actually worth the page. See `_ProjectScore` for the bug this closes.
    project_scores = _project_relevance(
        facts, bullets_by_fact, requirements, lanes=lanes
    )
    project_briefing = _project_relevance_briefing(project_scores, lanes=lanes)
    # The posting's own order for its languages and tools, so the printed skills
    # row answers this JD rather than whatever order the vault happens to hold.
    skill_order = jd_skill_order(jd_parsed)
    # Answered on the page only when the posting asked and the profile can back
    # it. Both halves matter: an unasked-for line is clutter, and an invented one
    # is the failure this whole service exists to prevent.
    availability = Availability()
    availability_asked = posting_asks_for_availability(jd_parsed, jd_clean)
    if availability_asked:
        availability = derive_availability(
            facts, basics=master_json_resume.get("basics") or {}
        )
        log.info(
            "tailor.availability",
            asked=True,
            answered=bool(availability),
            explicit=availability.explicit,
        )
    must_have = [req for req in requirements if not req.preferred]
    backed = [req for req in must_have if coverage[req.label].found]
    # What this posting is actually winnable at, given these facts. See
    # `_achievable_ats_score`: the fixed 80 was a target for a different JD.
    achievable = _achievable_ats_score(requirements, coverage)
    target_score = _effective_target(achievable)
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
            project_briefing=project_briefing,
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

    briefing = _requirement_briefing(
        requirements,
        coverage,
        status=_status_briefing(facts, bullets_by_fact),
        achievable=achievable,
    )
    if project_briefing:
        # Appended rather than folded into `_requirement_briefing` itself: that
        # function is the ATS rubric specifically, and the project ranking is a
        # different, JD-relevance question the writer needs answered before it
        # ever gets to `selected_fact_ids`.
        briefing = f"{briefing}\n\n{project_briefing}"
    availability_briefing = _availability_briefing(availability, availability_asked)
    if availability_briefing:
        briefing = f"{briefing}\n\n{availability_briefing}"

    user_prompt = _build_user_prompt(
        jd_parsed=jd_parsed,
        jd_clean=jd_clean,
        master_json_resume=master_json_resume,
        facts_payload=facts_payload,
        briefing=briefing,
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
        started = time.perf_counter()
        try:
            msg = await create_message(
                client,
                model=settings.anthropic_model_tailor,
                max_tokens=DRAFT_MAX_TOKENS,
                system=f"{CAREER_OPS_RULES}\n\n{SYSTEM_PROMPT}",
                messages=state["messages"],
                extra_headers={"x-manifest-tier": settings.manifest_tier_sonnet},
                output_config={"effort": COMPOSE_EFFORT},
            )
        except (anthropic.APIError, httpx.HTTPError, TimeoutError) as exc:
            # A refine pass is an improvement on something that already works, so a
            # transient gateway failure on one must never throw away the passes that
            # succeeded. A real run reached 78.3 over three good passes and then lost
            # all of it to a 429 on the fourth, which is the opposite of the point:
            # running the loop harder should not make the whole call more fragile.
            #
            # `httpx.HTTPError` because a stream that dies mid-reply raises the
            # transport error raw, past every anthropic class, and a run that lost
            # a good pass to one would be this comment's own failure again.
            #
            # `TimeoutError` because `create_message`'s own wall-clock deadline
            # (see llm_json.py's `_STREAM_WALL_CLOCK_TIMEOUT_SECONDS`) raises the
            # bare builtin, past every anthropic/httpx class too, for the same
            # "gateway went quiet" case a dropped stream already covers here.
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
        log.info(
            "tailor.call_timing",
            step="compose",
            iteration=iteration,
            seconds=round(time.perf_counter() - started, 1),
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
            retry_started = time.perf_counter()
            retry = await create_message(
                client,
                model=settings.anthropic_model_tailor,
                max_tokens=RETRY_MAX_TOKENS if empty else DRAFT_MAX_TOKENS,
                system=f"{CAREER_OPS_RULES}\n\n{SYSTEM_PROMPT}",
                messages=retry_messages,
                extra_headers={"x-manifest-tier": settings.manifest_tier_sonnet},
                output_config={"effort": COMPOSE_EFFORT},
            )
            log.info(
                "tailor.call_timing",
                step="compose_json_retry",
                iteration=iteration,
                seconds=round(time.perf_counter() - retry_started, 1),
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
        (
            document,
            _provenance,
            summary_rejection,
            _subs,
            _cuts,
            _trims,
        ) = _build_document(
            attempt,
            facts=facts,
            bullets_by_fact=bullets_by_fact,
            master_json_resume=master_json_resume,
            facts_payload=facts_payload,
            project_scores=project_scores,
            requirements=requirements,
            skill_order=skill_order,
            availability=availability,
        )
        frozen_terms.setdefault("matched", list(attempt.ats_keywords_matched))
        frozen_terms.setdefault("missing", list(attempt.ats_keywords_missing))
        matched_share, coverage_report = _compute_ats_from_document(
            jd_parsed=jd_parsed,
            json_resume=document,
            fallback_matched=frozen_terms["matched"],
            fallback_missing=frozen_terms["missing"],
        )
        quality = document_quality_flags(
            document,
            verified_sources=verified_sources,
            vault_evidence=vault_evidence,
        )
        if summary_rejection:
            # A refused summary leaves the page without its lede, so it costs the
            # pass points and the model is told why.
            quality["basics.summary"] = [summary_rejection]
        penalty = _quality_penalty(quality)
        score = matched_share - penalty
        scores = [*state["iteration_scores"], float(score)]
        # Counted the way the penalty counts, so the note the user reads and the
        # score they see agree about how much is actually wrong.
        chargeable = {
            where: [
                flag
                for flag in flags
                if INHERITED_FLAG_SUFFIX not in flag.split("(", 1)[0]
            ]
            for where, flags in quality.items()
        }
        chargeable = {where: flags for where, flags in chargeable.items() if flags}
        flag_count = sum(len(flags) for flags in chargeable.values())
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
            target=float(target_score),
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
        # Inherited flags do not keep the loop alive. A pass exists to fix
        # something, and the only way to clear "your verified bullet is 46
        # words" is to stop printing his verified bullet. Left in this test, a
        # run with nothing else to do would spend its remaining passes, and its
        # minutes, being told again about facts only he can edit.
        nothing_left = analysis_settled and not reachable and not chargeable
        # The reachable target is only as trustworthy as the analysis behind it,
        # for the same reason `nothing_left` waits on `analysis_settled`. A
        # ceiling computed while the analyst is silent says the vault cannot
        # cover this posting, when it may only mean nobody checked, and stopping
        # on it would lock in exactly the one-pass-early ending that rule exists
        # to prevent. Unsettled, the fixed target applies and the run tries again.
        pass_target = target_score if analysis_settled else TARGET_ATS_SCORE
        # Clearing a LOWERED floor is not the same as being done.
        #
        # `target_score` moves with what this vault can reach, so on a stretch
        # posting it lands well under TARGET_ATS_SCORE. A pass that scrapes over
        # it stops the run, and a measured A/B caught what that costs: two runs
        # whose first pass came in at 24.2 against a target of 22.8 stopped
        # there and shipped 30.2, while two whose first pass came in at 21.9,
        # one point short, took a repair and shipped 34.9. A two-point
        # difference on the first pass decided a five-point difference in the
        # resume, and the run that did WORSE first got the better document.
        #
        # So a lowered floor only ends the run when there is also nothing left
        # to do. `reachable` and `chargeable` are the two things a repair pass
        # can actually act on, and they are already computed above: a
        # requirement the vault can still cover, or a writing flag the writer
        # introduced. With either in hand, the pass is worth taking.
        #
        # This does not spend passes on a posting that cannot improve. When
        # nothing is reachable and nothing is charged, `nothing_left` already
        # ends the run, and `not improved` ends it the moment a pass stops
        # paying for itself. MAX_COMPOSE_PASSES still caps the whole thing.
        done = _run_is_done(
            score=score,
            pass_target=pass_target,
            reachable=reachable,
            chargeable=chargeable,
            passes=len(scores),
            improved=improved,
            nothing_left=nothing_left,
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
                        target=target_score,
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

    (
        json_resume,
        provenance,
        _summary_rejection,
        selection_corrections,
        page_cuts,
        page_trims,
    ) = _build_document(
        agent,
        facts=facts,
        bullets_by_fact=bullets_by_fact,
        master_json_resume=master_json_resume,
        facts_payload=facts_payload,
        project_scores=project_scores,
        requirements=requirements,
        skill_order=skill_order,
        availability=availability,
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
    ats_report["target_ats_score"] = float(target_score)
    # Reported so a low score reads as "this is what these facts can do here"
    # rather than as the tailor underperforming.
    ats_report["achievable_ats_score"] = float(achievable)
    ats_report["reached_target"] = float(ats_score) >= float(target_score)
    # The measurement the one-shot work is accountable to. `iterations` has one
    # entry per composition, so its length IS the number of writer model calls
    # this run spent: 1 means the first pass was shipped, 2+ means a repair ran.
    # Telling the writer the ceiling can only make the first pass land closer to
    # it, and the stop rule is untouched, so this number can fall but not rise.
    # Logged per run so "did the aim cost us calls" is answerable from the fleet
    # rather than argued from first principles.
    log.info(
        "tailor.model_calls",
        compose_passes=len(iteration_scores),
        repaired=len(iteration_scores) > 1,
        first_pass_score=float(iteration_scores[0]) if iteration_scores else None,
        final_score=float(ats_score),
        achievable=float(achievable),
        target=float(target_score),
        reached_target=float(ats_score) >= float(target_score),
    )
    # What a human reader would hold against the document, alongside what an ATS
    # would. An empty dict is the good outcome and is worth reporting as such.
    # Which arm produced this run, recorded where it cannot be lost. The same
    # value is logged next to the analyst timing, but Appwrite drops an
    # execution's logs at random: of three consecutive runs, two kept their logs
    # and one came back with zero bytes despite `logging: true` and a clean
    # 200. A measurement that cannot say which condition produced a number is
    # not a measurement, and `ats_report` is persisted on the version row, so
    # the label outlives the log.
    ats_report["analyst_effort"] = _analyst_effort_label(get_settings().analyst_effort)
    ats_report["writing_flags"] = document_quality_flags(
        json_resume,
        verified_sources=verified_sources,
        vault_evidence=vault_evidence,
    )
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
    # The writer's own account of the run, with one class of claim checked
    # before it is printed: a project left off "for lacking verified bullets"
    # when it has them. That reason describes a rule this module does not have,
    # and it was still being written about a fact with three bullets after #39
    # and #40 landed, because both of those corrected the SELECTION and neither
    # looked at the prose explaining it.
    written_note, invented = _false_bullet_excuses(
        agent.agent_note, facts=facts, bullets_by_fact=bullets_by_fact
    )
    on_page = {
        _project_short_name(str(entry.get("name") or ""))
        for entry in (json_resume.get("projects") or [])
    }
    note = (
        written_note
        + _selection_correction_note(selection_corrections)
        + _page_trim_note(page_trims)
        + _page_cut_note(page_cuts)
        + _honest_exclusion_note(invented, on_page=on_page, scored=project_scores)
    )
    passes = len(iteration_scores)
    ats_score, incomplete_reason = _finalize_ats_score(ats_score, ats_report, jd_parsed)
    if incomplete_reason == "unavailable_parse_incomplete":
        note += (
            "\n(Could not parse this job description, so Keyword Match is "
            "unavailable rather than a real score. Try tailoring again.)"
        )
    elif incomplete_reason == "no_scoreable_requirements":
        note += (
            "\n(This job description named no requirements this score could "
            "check, so Keyword Match is not shown.)"
        )
    elif ats_report["missing_needs_new_facts"] and not still_reachable:
        # Ordered ahead of hitting the target on purpose. Once the target moves
        # with what the vault can reach, a stretch posting hits it at a low
        # number, and "hit the target" next to a Job Match of 23 tells the
        # reader nothing they can act on. What is missing, and that it is
        # missing from the profile rather than from the writing, does.
        note += (
            "\n(Every requirement still missing is one your verified profile "
            "does not hold, so another pass cannot close it. Add the evidence "
            "on your Profile and run this again.)"
        )
    elif ats_score >= target_score:
        if target_score < TARGET_ATS_SCORE:
            # Saying "hit the target" without saying which target would read as
            # a good score on a posting this profile cannot cover.
            note += (
                f"\n(Covered what your profile can evidence for this posting, in "
                f"{_plural(passes, 'pass')}. The rest of what it asks for is work "
                "you have not done yet, not wording this could fix.)"
            )
        else:
            note += f"\n(Hit the Job Match target in {_plural(passes, 'pass')}.)"
    else:
        note += f"\n(Did not reach the Job Match target after {_plural(passes, 'pass')}.)"

    return (
        json_resume,
        provenance,
        _profile_gaps(
            list(agent.gap_questions),
            json_resume=json_resume,
            availability=availability,
            availability_asked=availability_asked,
        ),
        ats_score,
        ats_report,
        note,
    )


def _profile_gaps(
    gaps: list[GapQuestion],
    *,
    json_resume: dict[str, Any],
    availability: Availability,
    availability_asked: bool,
) -> list[GapQuestion]:
    """The model's gap questions, plus the ones Python can prove.

    A gap question has always been "the JD asks for this and your profile does
    not have it", decided by a model reading the posting. Three of them do not
    need a model at all, and were silently absent because of it: a posting that
    asked when you can start against a profile with no dates, a project on the
    page with no link on its heading, and a bullet still over the cap after the
    page tried to split it.

    Each names the edit rather than the defect. "Your saved bullet is 36 words"
    is a diagnosis; "split it into two on Profile" is something a person who has
    never heard of a resume parser can do this afternoon.
    """
    out = list(gaps)

    def add(requirement: str, why: str) -> None:
        if any(g.requirement == requirement for g in out):
            return
        out.append(GapQuestion(requirement=requirement, why_no_match=why))

    if availability_asked and not availability:
        add(AVAILABILITY_GAP_REQUIREMENT, AVAILABILITY_GAP_WHY)
    elif availability_asked and not availability.explicit:
        # A graduation date is on the page and it is not the window the posting
        # asked for, so the user is told what would answer it in full.
        add(AVAILABILITY_GAP_REQUIREMENT, AVAILABILITY_GAP_WHY_PARTIAL)

    for name in unlinked_projects(json_resume):
        add(
            f"A link for {name}",
            "This project is on the page with nothing to click. Add a link on "
            "Profile if it has a public repo, demo or write-up, and it goes on "
            "the heading.",
        )

    for text in over_length_bullets(json_resume):
        words = len(text.split())
        add(
            f"A shorter version of: {_gap_excerpt(text)}",
            f"This bullet runs to {words} words and prints as three lines, so "
            "the point of it gets lost. It has no sentence break to split on. "
            "Rewrite it as two shorter bullets on Profile.",
        )

    return out


# Enough of a bullet to recognise it on the Profile page without reprinting the
# whole thing inside a card that is already showing the fix.
_GAP_EXCERPT_WORDS = 8


def _gap_excerpt(text: str) -> str:
    words = text.split()
    if len(words) <= _GAP_EXCERPT_WORDS:
        return text
    return " ".join(words[:_GAP_EXCERPT_WORDS]) + "..."


def _build_document(
    agent: TailorAgentOutput,
    *,
    facts: list[TailorFact],
    bullets_by_fact: dict[str, list[TailorBullet]],
    master_json_resume: dict[str, Any],
    facts_payload: list[dict[str, Any]],
    project_scores: list[_ProjectScore] | None = None,
    requirements: list[_Requirement] | None = None,
    skill_order: list[str] | None = None,
    availability: Availability | None = None,
) -> tuple[
    dict[str, Any],
    list[ProvenanceEntry],
    str | None,
    list[tuple[str, str]],
    list[str],
    list[str],
]:
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
    # The ranking stops being advice here. Until this, `selected_fact_ids` was
    # taken as given and the measured ordering was something the prompt merely
    # asked the writer to respect. See `_enforce_project_ranking`.
    substitutions: list[tuple[str, str]] = []
    if project_scores:
        safe_fact_ids, substitutions = _enforce_project_ranking(
            safe_fact_ids, project_scores, bullets_by_fact
        )
        for passed_over, restored in substitutions:
            log.warning(
                "tailor.selection_corrected",
                dropped_by_writer=restored,
                in_favour_of=passed_over,
                note=agent.agent_note or "",
            )
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
    # After sanitising, never before: reverting an unsafe rewrite compares the
    # bullet against its verified source, and a half of a split bullet is not
    # that source. Splitting last means the check still sees whole bullets and
    # the page still gets short ones.
    safe_bullets = _split_over_length(safe_bullets)
    summary_objective, summary_rejection = _safe_summary(
        agent.summary_objective,
        master_json_resume=master_json_resume,
        facts_payload=facts_payload,
    )
    def assemble(
        chosen: list[TailorFact], bullets: list[SelectedBullet]
    ) -> tuple[dict[str, Any], list[ProvenanceEntry]]:
        return _assemble_json_resume(
            master_json_resume=master_json_resume,
            all_facts=facts,
            selected_facts=chosen,
            selected_bullets=bullets,
            bullets_by_fact=bullets_by_fact,
            summary_objective=summary_objective,
            skills_dedup_drop=agent.skills_dedup_drop,
            jd_skill_order=skill_order,
            availability=availability,
        )

    json_resume, provenance = assemble(selected_facts, safe_bullets)

    # A page that spills has to lose something, and `over_page` was only ever a
    # flag in the prompt, so the response to a two-page draft was to ask the
    # writer to fix it. That leaves the editorial decision unmade: the cheapest
    # way to satisfy "make it fit" is to shorten everything, which prints four
    # projects in six words each rather than the best three at readable length.
    #
    # Cutting is the decision, and the ranking already knows which one to cut.
    # Done by removing the fact and reassembling rather than by trimming the
    # finished document, so the provenance keeps describing the page that ships.
    # Shed the cheap lines before shedding evidence, cheapest first. Keywords
    # the posting never mentioned cost a word each and say nothing; the summary
    # is a written sentence about his work and the first thing anyone reads; a
    # project is the work itself.
    #
    # These two ran the other way round, and a real AMD co-op page shows what
    # that buys: the summary was deleted for space while forty-three skill
    # keywords stayed, filling about a quarter of the page. The run then
    # flagged the result `thin_page(8 bullets)`, which was correct and is the
    # tell. The page was full, but full of keywords rather than evidence.
    #
    # Skills first, then. The summary only goes if shedding every keyword the
    # posting did not ask about still leaves the page over.
    page_trims: list[str] = []
    if estimated_page_lines(json_resume) > MAX_PAGE_LINES:
        dropped = _trim_skills_to_fit(
            json_resume, requirements or [], MAX_PAGE_LINES
        )
        if dropped:
            page_trims.append(
                f"{_plural(dropped, 'skill')} this posting did not ask about"
            )
            log.info("tailor.skills_trimmed_for_space", dropped=dropped)
    if estimated_page_lines(json_resume) > MAX_PAGE_LINES and _drop_summary(json_resume):
        page_trims.append("the summary")
        log.info("tailor.summary_dropped_for_space")

    page_cuts: list[str] = []
    cut_facts: list[TailorFact] = []
    if project_scores:
        for weakest in _weakest_project_first(selected_facts, project_scores):
            if estimated_page_lines(json_resume) <= MAX_PAGE_LINES:
                break
            remaining = [f for f in selected_facts if f.kind == "project"]
            if len(remaining) <= MIN_PROJECTS_ON_PAGE:
                # Below this the resume stops making a case, so a document still
                # over length here stays over length rather than being emptied.
                log.info("tailor.page_still_over_at_floor", projects=len(remaining))
                break
            # Assembled first and kept only if it is an improvement. The cut
            # used to be committed unconditionally, and a real run cut a
            # three-bullet project off a nine-bullet page and shipped six:
            # over_page traded for thin_page, one defect swapped for another,
            # and the strongest project gone to buy it.
            #
            # A page too short is not the lesser problem. Spilling is untidy,
            # and a sparse page reads as a candidate with little to show, which
            # is the impression the whole selection exists to prevent.
            trimmed_facts = [f for f in selected_facts if f.id != weakest.id]
            trimmed_bullets = [
                sb
                for sb in safe_bullets
                if bullets_by_id[sb.fact_bullet_id].fact_id != weakest.id
            ]
            trimmed_resume, trimmed_provenance = assemble(
                trimmed_facts, trimmed_bullets
            )
            if printed_bullets(trimmed_resume) < MIN_PAGE_BULLETS:
                log.info(
                    "tailor.cut_would_empty_the_page",
                    project=weakest.title,
                    bullets_after=printed_bullets(trimmed_resume),
                    floor=MIN_PAGE_BULLETS,
                )
                break
            selected_facts = trimmed_facts
            safe_bullets = trimmed_bullets
            json_resume, provenance = trimmed_resume, trimmed_provenance
            page_cuts.append(weakest.title)
            cut_facts.append(weakest)
            log.info("tailor.project_cut_for_space", project=weakest.title)

    # The summary was written before the cut, so it can still point at a project
    # the reader will not find. Dropping it costs the page its lede, which is a
    # real loss, and it is the smaller one: a missing opening line is untidy, an
    # opening line citing work the page does not show is untrue. The rejection
    # is reported the same way every other refused summary is, so the pass is
    # charged for it and the next one is told what to avoid, which is how this
    # gets a correct lede rather than none.
    if cut_facts:
        orphaned = _summary_names_absent_project(
            summary_objective, cut=cut_facts, json_resume=json_resume
        )
        if orphaned:
            log.warning("tailor.summary_named_a_cut_project", project=orphaned)
            summary_rejection = (
                f"summary_rejected(names {orphaned}, which was cut to fit the "
                "page; write the summary about the work that is on the page)"
            )
            summary_objective = None
            json_resume, provenance = assemble(selected_facts, safe_bullets)

    return (
        json_resume,
        provenance,
        summary_rejection,
        substitutions,
        page_cuts,
        page_trims,
    )


# What one flagged writing problem costs against keyword coverage. Three points
# is deliberately more than one keyword is usually worth on a JD with a dozen of
# them, because a reader notices a duplicated bullet and does not notice a
# missing keyword. The cap keeps a badly worded pass comparable to a
# well-written one that covers less, instead of driving the score negative.
QUALITY_FLAG_PENALTY = Decimal("3")
MAX_QUALITY_PENALTY = Decimal("30")


# Flags naming a defect the page inherited from the vault rather than one the
# writer introduced. They are still reported, to the user, whose facts they
# describe and who is the only one who can decide which clause of his own claim
# to drop. They are not charged to the pass, because the only move that clears
# them is to depart from the verified wording, and a scoring rule that rewards
# that is pointed against the thing this whole service exists to guarantee.
INHERITED_FLAG_SUFFIX = "_verbatim"


def _quality_penalty(quality: dict[str, list[str]]) -> Decimal:
    flagged = sum(
        1
        for flags in quality.values()
        for flag in flags
        if INHERITED_FLAG_SUFFIX not in flag.split("(", 1)[0]
    )
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
        # Lists are UNIONED across variants, not overwritten.
        #
        # Last-writer-wins per key silently discarded edits. Measured on the
        # real vault: three BedRocked duplicates carried 6, 6 and 12 keywords,
        # the twelve being the AI terms the candidate had just added to fix its
        # ranking. The six-keyword variant won the key and the edit vanished.
        # BedRocked then scored 2 against the Amex JD, tied with three unrelated
        # MSD projects, and the page-fit cut removed it on a title tie-break.
        # Unioned it scores 4 and leaves the bottom tie.
        #
        # Duplicates are the same fact by construction here, so a keyword on any
        # of them is a keyword on all of them. Scalars still take the highest
        # ranked variant's value, because two different dates or summaries are a
        # genuine conflict and picking the canonical one is the existing answer.
        payload: dict[str, Any] = {}
        for variant in reversed(ranked):
            for key, value in (variant.payload or {}).items():
                if value in (None, "", [], {}):
                    continue
                if isinstance(value, list):
                    existing = payload.get(key)
                    combined = list(existing) if isinstance(existing, list) else []
                    for item in value:
                        if item not in combined:
                            combined.append(item)
                    payload[key] = combined
                else:
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
            if flag.startswith(
                ("jd_padding", "inflated_rewrite", "first_person", "banned_wording")
            )
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


def _split_over_length(selected: list[SelectedBullet]) -> list[SelectedBullet]:
    """Cut any chosen bullet that is over the cap into the statements it holds.

    Every half keeps its `fact_bullet_id`, so both halves stay traceable to the
    one verified bullet they came from and provenance still says where the words
    came from. Nothing is written: see `split_long_bullet`, which only cuts at
    punctuation the author already used and returns the bullet untouched when
    there is none.

    Why here rather than in the prompt: the prompt has always said 30 words, and
    the safest thing a writer can do with a 36-word verified bullet is print it
    exactly as saved, which is what it does. Both rules are right and they
    disagree, so the page settles it in the one way that neither invents nor
    overrides -- by using the author's own sentence break.
    """
    out: list[SelectedBullet] = []
    for bullet in selected:
        pieces = split_long_bullet(bullet.rewritten_text)
        if len(pieces) < 2:
            out.append(bullet)
            continue
        log.info(
            "tailor.long_bullet_split",
            bullet_id=str(bullet.fact_bullet_id),
            words=len(bullet.rewritten_text.split()),
            pieces=len(pieces),
        )
        out.extend(
            SelectedBullet(
                fact_bullet_id=bullet.fact_bullet_id,
                rewritten_text=piece,
                target_section=bullet.target_section,
            )
            for piece in pieces
        )
    return out


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
    # The one line every reader sees first is also the one place a banned word
    # or vague-gesture phrase costs the most: bullet_flags catches the same
    # brochure vocabulary and JD padding here that it catches in a bullet.
    writing_flags = [
        flag
        for flag in bullet_flags(summary)
        if flag.startswith(("banned_wording", "jd_padding"))
    ]
    if writing_flags:
        log.warning("tailor.unsafe_summary_reverted", writing_flags=writing_flags)
        return None, f"summary_rejected({','.join(writing_flags)})"
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


# What the facts feed is allowed to cost, in characters of JSON.
#
# The old limit was 12,000 applied as `json.dumps(payload, indent=2)[:12000]`,
# and that is three separate mistakes in one line. His vault serialises to
# 39,848 characters that way, so the model saw 30% of it; the projects are
# written last, so EVERY project was cut off, which is why a run kept reporting
# that fact data "was truncated in the profile feed" and leaving his flagship
# off the page. And a mid-string cut of a JSON blob is not shortened JSON, it is
# broken JSON.
#
# 60,000 characters is roughly 15k tokens against a call that already writes
# 24k, so the whole vault fits with room to grow.
FACTS_PAYLOAD_BUDGET = 60_000


def _lean(value: Any) -> Any:
    """The same facts without the empty fields, which are most of the bytes."""
    if isinstance(value, dict):
        return {k: _lean(v) for k, v in value.items() if v not in (None, "", [], {})}
    if isinstance(value, list):
        return [_lean(v) for v in value]
    return value


def _facts_feed(facts_payload: list[dict[str, Any]]) -> str:
    """The vault as JSON the model can actually parse, inside the budget.

    Compact and without empty fields, which takes his 39,848 down to 28,768 on
    its own. If that is still over budget, whole facts are dropped rather than
    the string being cut mid-token, skills first because they are a name and a
    category where a project is evidence, and what went is stated in the feed
    so the model knows it is looking at part of a vault rather than all of one.
    """
    lean = [_lean(f) for f in facts_payload]
    blob = json.dumps(lean, separators=(",", ":"))
    if len(blob) <= FACTS_PAYLOAD_BUDGET:
        return blob
    # Least evidential first, and within a kind the order it arrived in.
    order = {"skill": 0, "certification": 1, "award": 2, "publication": 3}
    droppable = sorted(
        range(len(lean)), key=lambda i: order.get(str(lean[i].get("kind")), 9)
    )
    # Sized once each, then dropped by index. Rebuilding and re-serialising the
    # whole list per drop is quadratic, and on a vault big enough to need
    # dropping that is the one time it must not be.
    sizes = [len(json.dumps(f, separators=(",", ":"))) + 1 for f in lean]
    total = sum(sizes) + 2
    doomed: set[int] = set()
    for index in droppable:
        if total <= FACTS_PAYLOAD_BUDGET:
            break
        doomed.add(index)
        total -= sizes[index]
    keep = [f for i, f in enumerate(lean) if i not in doomed]
    dropped = len(doomed)
    log.warning("tailor.facts_feed_truncated", dropped=dropped, kept=len(keep))
    note = {"_note": f"{dropped} lower-value facts omitted to fit; projects and roles are complete"}
    return json.dumps([*keep, note], separators=(",", ":"))


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
        f"<jd>\n{(jd_clean or '')[:JD_CLEAN_PROMPT_CHARS]}\n</jd>\n\n"
        "CANDIDATE MASTER RESUME (JSON Resume):\n"
        f"{json.dumps(master_json_resume, indent=2)[:6000]}\n\n"
        "CANDIDATE VERIFIED FACTS + BULLETS:\n"
        f"{_facts_feed(facts_payload)}\n\n"
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


# A fact's own title is stored data, not a rendered field: this one reads
# "Junior Software Test Automation Engineer · Client: leading global rideshare
# platform (Fares team)" because whatever created or edited it wrote client
# context straight into the title, and nothing downstream ever separated the
# two. Rendered verbatim, that title is the resume entry's bold heading, wraps
# across two lines, and pushes the role itself off the first one -- the exact
# "title stays on one line" failure a resume gets marked down for. The client
# context is not lost by trimming it here: the compose pass already sees the
# untouched fact title in facts_payload and, in practice, folds context like
# this into a bullet on its own ("...the Fares pricing engine..."). Splitting
# on punctuation rather than a hard character cutoff means a title with no
# such clause is never truncated mid-word.
_TITLE_CONTEXT_SPLIT_RE = re.compile(
    r"\s*[,·|–-]\s*(?:client|customer|team|for)\s*:?\s+", re.IGNORECASE
)


def _entry_title(title: str) -> str:
    return _TITLE_CONTEXT_SPLIT_RE.split(title, maxsplit=1)[0].strip() or title.strip()


# Payload keys an importer or a hand-edited fact puts a project's link under.
# `ProfileFact` has exactly one URL column, `source_url`, so anything a resume
# or a README supplied as a second link -- the repository next to the demo, the
# bot next to the repository -- landed in the payload and was never read again.
# A real vault entry for a working Telegram claims bot rendered with `url: null`
# for precisely that reason: the link was there, in `payload`, and assembly only
# looked at the column.
#
# Ordered, because only one link fits the heading: the repository is what a
# reviewer opens to check the work, so it leads, and a live demo comes next.
_PROJECT_URL_KEYS = ("url", "github", "repo", "repository", "demo", "link", "website")


def _project_url(fact: TailorFact) -> str | None:
    """The one link that goes on this project's heading, from verified fields only."""
    payload = fact.payload or {}
    candidates = [fact.source_url, *(payload.get(key) for key in _PROJECT_URL_KEYS)]
    for candidate in candidates:
        text = str(candidate or "").strip()
        # Nothing is repaired or completed here. A fact holding "github.com/x/y"
        # without a scheme is a fact to fix on Profile, and guessing https:// in
        # front of it is guessing a URL.
        if text.startswith(("http://", "https://")):
            return text
    return None


def _jd_skill_rank(keyword: str, jd_skill_order: list[str]) -> int:
    """Where this vault skill first appears in the posting, or past the end.

    Matched with `_mentions`, the same word-boundary test the ATS score uses, so
    "Python" claims the posting's "Python" and "Async Python" claims it too,
    while "Go" never claims "Django". The posting's own order is the ranking:
    a JD that opens on C++ and mentions Python fourth is telling the reader
    which one it cares about, and a skills row that leads with Python because
    the vault happens to list it first is answering a different posting.
    """
    # `_mentions` casefolds the term and expects an already-folded haystack,
    # because every other caller hands it one built by `_ats_source_text`.
    folded = keyword.casefold()
    for index, term in enumerate(jd_skill_order):
        if _mentions(folded, term):
            return index
    return len(jd_skill_order)


def _order_skills_by_jd(
    groups: list[dict[str, Any]], jd_skill_order: list[str] | None
) -> list[dict[str, Any]]:
    """Put the posting's own languages and tools first, inside each row and across rows.

    Only ever a REORDER. Nothing is added, so a language the posting asks for
    and the vault does not hold cannot appear here, and nothing is removed, so a
    skill the posting never mentions keeps its place at the back of its row.
    That is the whole safety argument: the worst a bad match can do is stand in
    the wrong position.

    Slash-joined asks ("C/C++/Java/Go/Python") are handled upstream, where
    `_keyword_alternatives` already splits an enumeration into the individual
    skills it accepts any one of. By the time the order reaches here it is a
    flat list of terms in the order the posting states them.
    """
    if not jd_skill_order:
        return groups
    ranked: list[tuple[tuple[bool, int, int], dict[str, Any]]] = []
    for position, group in enumerate(groups):
        keywords = list(group.get("keywords") or [])
        order = sorted(
            range(len(keywords)),
            key=lambda i: (_jd_skill_rank(keywords[i], jd_skill_order), i),
        )
        reordered = {**group, "keywords": [keywords[i] for i in order]}
        best = min(
            (_jd_skill_rank(keyword, jd_skill_order) for keyword in keywords),
            default=len(jd_skill_order),
        )
        # `Additional` stays last whatever it matched: it is the row that names
        # no category, and a reader who reaches it has already read the ones
        # that told them something. Original position is the final tiebreak, so
        # two rows the posting never mentions keep the order they arrived in.
        key = (reordered.get("name") == _ADDITIONAL_SKILL_LABEL, best, position)
        ranked.append((key, reordered))
    return [group for _key, group in sorted(ranked, key=lambda item: item[0])]


def _assemble_json_resume(
    *,
    master_json_resume: dict[str, Any],
    all_facts: list[TailorFact],
    selected_facts: list[TailorFact],
    selected_bullets: list[SelectedBullet],
    bullets_by_fact: dict[str, list[TailorBullet]],
    summary_objective: str | None,
    skills_dedup_drop: list[str] | None = None,
    jd_skill_order: list[str] | None = None,
    availability: Availability | None = None,
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
    # The one line a recruiter is told to look for when the posting asked for it.
    # Assembled from verified dates in `availability`, never from anything here,
    # and rendered on the contact row so it is answered above the fold rather
    # than inferred from the education block. See services/availability.py.
    if availability and availability.line:
        basics["availability"] = availability.line
    else:
        # A stale line from a master resume must not survive onto a page for a
        # posting that never asked, and must not survive a profile that no
        # longer supports it.
        basics.pop("availability", None)

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
        # The fallback path prints the vault's own wording, so it gets the same
        # split the selected path got in `_split_over_length`: a 36-word saved
        # bullet reads no better for having skipped the writer.
        untailored = [
            piece for b in all_b for piece in split_long_bullet(b.text)
        ]
        return dedupe_bullets(untailored)[:limit], []

    work: list[dict[str, Any]] = []
    for f in _facts_of("experience"):
        bullets, _picked = _bullets_for(f, limit=MAX_WORK_BULLETS)
        payload = f.payload or {}
        work.append(
            {
                "name": f.org or "",
                "position": _entry_title(f.title),
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
                "url": _project_url(f),
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
                "position": _entry_title(f.title),
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
        "skills": _order_skills_by_jd(
            _consolidate_skills(skills_by_category, drop=skills_dedup_drop),
            jd_skill_order,
        ),
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


# Words that carry no claim of their own, so their absence should not stop a
# drop that is otherwise plainly redundant. "OpenAI / Anthropic SDKs" is already
# said by "LLM integration (OpenAI, Anthropic, Qwen)"; the bare "SDKs" is not the
# part that matters.
_SKILL_FILLER_TOKENS = frozenset(
    {"sdk", "sdks", "api", "apis", "and", "or", "the", "with", "using", "integration"}
)

# Below this the skills block stops being a skills block. A floor rather than a
# judgement: whatever the reasoning, a resume that lists three technologies for a
# candidate who has verified forty is describing somebody else.
MIN_PRINTED_SKILLS = 8


def _skill_is_redundant(keyword: str, kept: list[str]) -> bool:
    """Whether another kept skill genuinely already says this one.

    `skills_dedup_drop` exists for the case the alias matching below cannot see:
    a name nested inside a longer phrase, where "OpenAI / Anthropic SDKs" is
    already carried by "LLM integration (OpenAI, Anthropic, Qwen)". That is a
    real gap and the model is the right thing to spot it.

    What was missing is that nothing checked the claim. The drop list was applied
    on trust, so a pass that decided a dozen skills were surplus simply removed
    them, and a real run printed "Backend & Data: REST APIs, Spatial Joins" for a
    candidate whose vault holds FastAPI, Docker, PostgreSQL, Async Python and
    Pytest. The remedy text on `unevidenced_skill` pushes in exactly this
    direction, telling the writer to drop a skill no bullet demonstrates, and a
    one-page resume cannot demonstrate forty.

    So the claim is now verified instead of believed: a skill is dropped only
    when its own distinctive words all appear inside a skill that is staying.
    """
    tokens = {t for t in _identity_text(keyword).split() if t not in _SKILL_FILLER_TOKENS}
    if not tokens:
        return False
    # A one-word skill is never covered by a longer phrase containing it.
    #
    # This reverses the decision recorded in
    # `test_a_broader_skill_goes_when_a_narrower_one_already_names_it`, whose
    # argument was that "the page still says the word". For a keyword scan that
    # is true. For the human reading the row it is not: subset containment
    # folded "Python" into "Async Python", drops apply by identity across every
    # group, and a real render of an AI-engineering resume read
    # "Languages: Go, Bash, Java, HTML, CSS" for a candidate whose first
    # required skill on the posting was Python.
    #
    # Hemnaath's call, made on that render: Python stays in Languages.
    #
    # The case the drop list exists for is untouched, because it is a whole name
    # nested inside a longer one rather than a word wearing a qualifier:
    # "OpenAI / Anthropic SDKs" really is carried by
    # "LLM integration (OpenAI, Anthropic, Qwen)".
    # Counted BEFORE filler removal. "Anthropic SDKs" is two words wearing one
    # filler, and it is genuinely carried by "Anthropic Claude models"; "Python"
    # is one word and is not carried by anything.
    if len(_identity_text(keyword).split()) == 1:
        return False
    for other in kept:
        other_tokens = set(_identity_text(other).split())
        if tokens <= other_tokens and _identity_text(other) != _identity_text(keyword):
            return True
    return False


def _consolidate_skills(
    skills_by_category: dict[str, list[str]],
    *,
    drop: list[str] | None = None,
) -> list[dict[str, Any]]:
    """One row per real category, each keyword appearing once on the page.

    A profile that has been imported more than once carries the same category
    spelled several ways ("AI / ML" and "AI & ML") and the same tools listed
    under several of them, which rendered eight skill rows where five belonged
    and spent a fifth of a one-page resume repeating itself.

    `drop` is the compose agent's `skills_dedup_drop`: keyword strings it
    judged redundant because the same vendor or tool already appears in
    another row -- something the automatic alias matching below cannot see,
    since it matches one whole keyword spelling against another, not a name
    nested inside a longer phrase like "LLM integration (OpenAI, Anthropic,
    Qwen)". Matched by `_identity_text`, the same fold used everywhere else in
    this function, so a string that does not land on a real keyword drops
    nothing rather than guessing.
    """
    requested_drop = {_identity_text(item) for item in (drop or [])}
    merged: dict[str, dict[str, Any]] = {}
    for label, titles in skills_by_category.items():
        # Same words, different punctuation, is the same category.
        key = " ".join(sorted(_identity_text(label).split()))
        bucket = merged.setdefault(key, {"name": label.strip(), "keywords": []})
        bucket["keywords"].extend(titles)

    # Which requested drops are honoured, decided before anything is removed so
    # each is judged against the whole printable set rather than against whatever
    # happens to survive earlier in the loop.
    printable = [
        keyword
        for bucket in merged.values()
        for keyword in bucket["keywords"]
        if _identity_text(keyword) and _identity_text(keyword) not in UNPRINTABLE_SKILLS
    ]
    folded_drop: set[str] = set()
    for keyword in printable:
        folded = _identity_text(keyword)
        if folded not in requested_drop or folded in folded_drop:
            continue
        others = [k for k in printable if _identity_text(k) != folded]
        if _skill_is_redundant(keyword, others):
            folded_drop.add(folded)
        else:
            # The writer asked, the page does not already say it, so it stays.
            log.info("tailor.skill_drop_refused", skill=keyword)

    distinct_printable = len({_identity_text(k) for k in printable})
    # Only where there is something to protect. A candidate who has verified
    # four skills prints four, and the floor has no business overriding a
    # defensible drop to reach a number they never had.
    if (
        distinct_printable >= MIN_PRINTED_SKILLS
        and distinct_printable - len(folded_drop) < MIN_PRINTED_SKILLS
    ):
        # Individually defensible drops can still add up to a skills block that
        # describes a different candidate. The floor is the backstop.
        log.warning(
            "tailor.skill_drops_refused_by_floor",
            requested=len(folded_drop),
            printable=distinct_printable,
            floor=MIN_PRINTED_SKILLS,
        )
        folded_drop = set()

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
            if folded in folded_drop:
                log.info("tailor.skill_dedup_dropped", skill=keyword)
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
    # The same class of term, arriving as its own short `required_skills` entry
    # rather than inside a sentence. A real ByteDance posting supplied
    # "problem-solving" on its own, and it was scored as a must-have no resume
    # answers with a skill: nobody writes "problem-solving" in a bullet, and
    # counting it missing deflated a genuine match by a whole requirement.
    # "collaborative" is deliberately absent -- collaborative filtering is a
    # real technique, and excluding the word would delete it.
    r"problem[- ]solving|problem solvers?|critical thinking|analytical skills?|"
    r"interpersonal|time management|attention to detail|teamwork|"
    r"written communication|verbal communication|"
    # Enthusiasm/attitude phrasing. A real posting supplied "excited to learn"
    # and "open to feedback" as required_skills entries, each scored as a
    # missing skill no bullet could ever contain.
    r"excited to learn|excited about|open to feedback|eager to learn|"
    r"growth mindset|"
    # Career stage and spoken languages, which a skills match cannot speak to.
    # A real posting supplied "New grad or early-career engineer" and "English
    # required, French a plus", and both were scored as missing skills.
    r"new grad|early[- ]career|entry[- ]level|years? of experience|"
    r"english|french|german|spanish|mandarin|fluent|native speaker|"
    r"a plus|nice to have|preferred|coursework|thesis|law degree|"
    # An eligibility statement phrased as who the role suits, not what it
    # asks of a skill. Crowe's "Ideal for students" scored as a missing
    # skill no resume text could ever satisfy, capping the ATS score
    # regardless of true fit.
    r"ideal for (?:current )?students?|"
    # The role type itself. `internships?` above catches the noun but not the
    # bare word a keywords list actually carries: an AMD co-op posting listed
    # "Intern" and "Co-op" as keywords and both scored as must-haves the
    # candidate had failed to match, on a posting that IS an internship.
    r"interns?|co[- ]?ops?|"
    # Parse debris. Splitting "Familiarity with cloud (e.g., AWS, GCP, Azure)"
    # on its punctuation leaves "e.g" behind as its own requirement. It is not
    # a skill, it is the sentence's own shrapnel.
    r"e\.g\.?|i\.e\.?|etc\.?"
    r")\b",
    re.I,
)

# Culture, values and personality copy, which almost every posting carries and
# no resume can answer with a skill.
#
# Kept apart from `_NON_SKILL_RE` because it is a different claim about the
# term: that one says "this is an eligibility rule or a soft skill", this one
# says "this is the employer describing itself or the person it hopes to like".
# Counting either as a must-have deflates the number the same way, and this
# half is the larger one: a values paragraph runs to a dozen terms, so a
# posting with one can push a genuine match below half on wording alone.
#
# Two rules govern what goes in here, and both exist because a false positive
# deletes a real skill from the score rather than merely leaving noise in it:
#
#  * A word with a technical homonym is matched only in its culture phrasing.
#    "data-driven" and "event-driven architecture" are why bare "driven" is
#    absent; "data integrity" is why "integrity" is; "resilience engineering"
#    is why "resilient" is; "reliability" (SRE) is why "reliable" is.
#  * "collaborative" stays out for the reason `_NON_SKILL_RE` already gives:
#    collaborative filtering is a real technique. The noun and the verb do not
#    share that problem, so "collaboration" and "collaborate" are matched and
#    "collaborative" is not.
#
# Concrete qualifications are untouched. A named technology, a domain, a tool
# and a measurable responsibility all still score, including ones that sound
# soft in isolation: "accessibility" is a frontend skill, "mentoring" is a
# senior engineer's job, "stakeholder management" is a PM's, and none of them
# appear below.
_CULTURE_FLUFF_RE = re.compile(
    r"(?:"
    # The employer describing its own culture, or introducing a values list.
    # "We value curiosity" and "Our values" arrive as their own entries.
    r"\bwe value\b|\bwe are looking for\b|\bour values\b|\bteam values\b|"
    r"\bculture (?:fit|add)\b|\bcompany culture\b|\bcultural fit\b|"
    r"\bwork[- ]life balance\b|\bdynamic environment\b|\bstartup environment\b|"
    r"\bhigh[- ]growth environment\b|\bfast[- ]moving\b|"
    # Diversity, equity and inclusion statements. Every posting that carries
    # one carries several sentences of it, and a parser drops them straight
    # into `required_skills` alongside the technologies.
    r"\bdiversity\b|\binclusion\b|\bbelonging\b|\bunderrepresented\b|"
    r"\bequal opportunity\b|\ball backgrounds\b|\bencouraged to apply\b|"
    r"\binclusive (?:environment|workplace|culture|team|hiring)\b|"
    # Personality and attitude. The half a reader would call "nice to have
    # personality" rather than a qualification.
    r"\bpassion\b|\bpassionate\b|\benthusias(?:m|tic)\b|\bcuriosity\b|"
    r"\bcurious\b|\bhumility\b|\bhumble\b|\bempath(?:y|etic)\b|"
    r"\badaptab(?:le|ility)\b|\bflexib(?:le|ility)\b|\bproactive(?:ly)?\b|"
    r"\bmotivated\b|\bgrit\b|\btenacity\b|\bperseverance\b|\bhustle\b|"
    r"\bscrappy\b|\bentrepreneurial\b|\bopen[- ]minded\b|\bambiguity\b|"
    r"\bmultitask(?:ing)?\b|\bmulti[- ]task\b|\bwear many hats\b|"
    r"\bcan[- ]do\b|\bpositive attitude\b|\bsense of humou?r\b|"
    r"\bbias for action\b|\bcustomer obsess(?:ion|ed)\b|"
    r"\bself[- ](?:driven|motivated|directed|sufficient)\b|"
    r"\b(?:results|mission|purpose|value)[- ]driven\b|"
    r"\bresults[- ]oriented\b|\bteam[- ]oriented\b|"
    r"\btake[sn]? ownership\b|\b(?:sense of|ownership) (?:ownership|mentality|mindset)\b|"
    r"\bwilling(?:ness)? to learn\b|\bdesire to learn\b|\blove of learning\b|"
    r"\bcontinuous learning\b|\blearning mindset\b|\bthrives?\b|"
    # Working-together copy. The noun and the verb only; see the note above on
    # why "collaborative" is deliberately absent.
    r"\bcollaborat(?:ion|ions|e|es|ed|ing)\b|\bworks? well with others\b|"
    r"\bwork(?:s|ing)? independently\b|\bability to work independently\b|"
    r"\b(?:strong|excellent|effective|clear) communicator\b|"
    r"\bcommunicate effectively\b"
    r")",
    re.I,
)


def _is_culture_fluff(term: str) -> bool:
    """True when a JD term is culture or values copy rather than a requirement.

    Exposed for the same reason `_is_candidate_skill` is: the rule is worth
    testing directly, and a caller that wants to explain WHY a term was set
    aside needs to tell this apart from an eligibility rule.
    """
    return bool(_CULTURE_FLUFF_RE.search(term))


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
    r"ability to start|able to start|start date|new grad|early[- ]career|"
    r"ideal for (?:current )?students?)\b",
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
    return (
        not _NON_SKILL_RE.search(term)
        and not _is_culture_fluff(term)
        and not _is_date_term(term)
    )


# What a JD hangs in FRONT of a skill without adding to it. A short entry never
# reached `_skills_inside_prose`, so none of the prose path's normalisation ever
# touched it, and "Experience with C/C++/Java/Go/Python" is 3 words and 36
# characters -- inside both `_is_ats_keyword` limits. It was therefore scored
# verbatim, as one requirement whose only wording no resume can contain, and the
# five languages inside it (one of which the candidate writes) were never
# recovered. On the real ByteDance AI Platform posting it was the single largest
# miss behind a Keyword Match of 27 sitting next to a review of 98.
_LEAD_IN_RE = re.compile(
    r"^(?:(?:demonstrated|proven|strong|solid|excellent|good|deep|basic|working|"
    r"prior|practical|extensive|significant|hands[- ]on)\s+)*"
    r"(?:experience|experienced|familiarity|familiar|proficiency|proficient|"
    r"knowledge|understanding|background|exposure|expertise|competency|comfort|"
    r"comfortable|skilled|fluency)"
    r"\s+(?:with|in|of|using|across|on)\s+",
    re.I,
)

# Nouns a JD hangs off the END of a skill, naming a second wording the same
# requirement is satisfied by. Distinct from `_FILLER_TAIL_RE`, which REPLACES a
# fragment recovered from prose: this one only ever ADDS an alternative, so the
# label the user reads stays the posting's own phrasing and the denominator does
# not move. The ByteDance posting asked for "optimization techniques", "machine
# learning systems" and "profiling tools" and scored all three missing against a
# profile that says "optimization", "machine learning" and "profiling".
_TRAILING_NOUN_RE = re.compile(
    r"\s+(?:techniques?|tools?|tooling|technologies|practices?|principles?|"
    r"methods?|methodolog(?:y|ies)|libraries|frameworks?|stacks?|patterns?|"
    r"concepts?|fundamentals|systems? design|systems?|pipelines?|workflows?)$",
    re.I,
)
# How long a ONE-WORD trimmed form has to be before it is offered as a wording.
# Trimming is only safe while what is left still names something; "build systems"
# leaves "build" and "design patterns" leaves "design", and either would match
# almost any resume ever written, which is flattery rather than a match. A length
# floor rules those out without a hand-kept blocklist: "optimization",
# "profiling" and "concurrent" clear it, "build", "design", "test", "code",
# "data", "web" and "cloud" do not. A multi-word remainder ("machine learning")
# is specific by construction and is not length-checked.
_TRIMMED_FORM_MIN_CHARS = 8

# A slash-joined run of at least this many segments is an enumeration, not a
# compound. Deliberately three rather than two: `_PROSE_SPLIT_RE` documents why
# "/" is not a separator, and every compound that argument is about -- CI/CD,
# Pub/Sub, TCP/IP, I/O -- has exactly two segments. "C/C++/Java/Go/Python" has
# five and is a list of alternatives any one of which satisfies the posting.
_SLASH_LIST_MIN_SEGMENTS = 3
# A one-character segment is dropped rather than offered. `_mentions` treats "#"
# as a word boundary, so a bare "C" alternative would credit a resume that only
# ever says "C#", and the enumeration's other segments already carry the ask.
_SLASH_SEGMENT_MIN_CHARS = 2


def _slash_alternatives(term: str) -> list[str]:
    """The individual skills inside a slash-joined enumeration, if it is one."""
    segments = [segment.strip() for segment in term.split("/")]
    if len(segments) < _SLASH_LIST_MIN_SEGMENTS or not all(segments):
        return []
    return [
        segment
        for segment in segments
        if len(segment) >= _SLASH_SEGMENT_MIN_CHARS and _is_candidate_skill(segment)
    ]


def _keyword_alternatives(entry: str) -> tuple[list[str], bool]:
    """Every wording a short JD entry is satisfied by, and whether they are an any-of set.

    Returns the alternatives (the entry itself always first) and `any_of`: True
    when they are DIFFERENT skills the posting accepts any one of, rather than
    different phrasings of one skill. Only the second kind is safe to merge with
    another requirement -- see `_jd_requirements`.

    Extra alternatives that match nothing are free, because `covered_by` is an
    OR; only an alternative that matches something it should not costs anything.
    That is the whole reason this derives wordings instead of rewriting the entry.
    """
    alternatives = [entry]
    any_of = False

    def offer(candidate: str) -> None:
        cleaned = candidate.strip(" .,;:")
        if not cleaned or not _is_candidate_skill(cleaned):
            return
        if any(cleaned.casefold() == alt.casefold() for alt in alternatives):
            return
        alternatives.append(cleaned)

    def offer_trimmed_tail(form: str) -> None:
        shortened = _TRAILING_NOUN_RE.sub("", form).strip()
        if shortened == form:
            return
        if " " not in shortened and len(shortened) < _TRIMMED_FORM_MIN_CHARS:
            return
        offer(shortened)

    trimmed = _LEAD_IN_RE.sub("", entry).strip()
    offer(trimmed)
    for form in dict.fromkeys((entry, trimmed)):
        offer_trimmed_tail(form)
        segments = _slash_alternatives(form)
        # A non-empty return proves the source was a `_SLASH_LIST_MIN_SEGMENTS`
        # enumeration, which is what makes this an any-of set, however many of
        # its segments survived the filter.
        any_of = any_of or bool(segments)
        for segment in segments:
            offer(segment)
    return alternatives, any_of


# Where the posting's own skill vocabulary is read from, in the order a reader
# meets it. `required_skills` leads because that is the section an employer puts
# the things it will not compromise on; `technologies` and `keywords` follow
# because they are the same asks restated; preferred trails, since a nice-to-have
# should not push a must-have down the printed row.
_SKILL_ORDER_FIELDS = (
    "required_skills",
    "qualifications",
    "technologies",
    "keywords",
    "preferred_skills",
)


def jd_skill_order(jd_parsed: dict[str, Any] | None) -> list[str]:
    """Every skill this posting names, in its own order, deduplicated.

    Slash-joined enumerations are expanded through `_keyword_alternatives`, the
    same function the score already uses, so "C/C++/Java/Go/Python" contributes
    five orderable terms rather than one unmatchable string. Which of them the
    candidate actually writes is not decided here: this list only says what the
    posting asked for and in what order, and `_order_skills_by_jd` intersects it
    with the vault.
    """
    out: list[str] = []
    seen: set[str] = set()

    def offer(term: str) -> None:
        cleaned = term.strip(" .,;:")
        folded = cleaned.casefold()
        if not cleaned or folded in seen or not _is_candidate_skill(cleaned):
            return
        seen.add(folded)
        out.append(cleaned)

    for field_name in _SKILL_ORDER_FIELDS:
        for entry in (jd_parsed or {}).get(field_name) or []:
            text = str(entry or "").strip()
            if not text:
                continue
            if _is_ats_keyword(text):
                alternatives, _any_of = _keyword_alternatives(text)
                for alternative in alternatives:
                    offer(alternative)
                continue
            # A prose requirement carries its skills inside a sentence. The same
            # recovery the scorer uses gets them out; anything it cannot recover
            # contributes no ordering, which is right, because a skill nobody
            # can name cannot be put first.
            for fragment in _skills_inside_prose(text):
                offer(fragment)
    return out


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
    # True when the alternatives are DIFFERENT skills the posting accepts any one
    # of, rather than different phrasings of one skill. It decides one thing: an
    # any-of requirement is never merged with another. Folding a standalone,
    # specific must-have for C++ into "one or more of C++, Python or TypeScript"
    # would let a resume that only writes Python satisfy the C++ ask, which is
    # exactly the flattery test_a_genuine_mismatch_is_not_flattered forbids.
    any_of: bool = False

    def covered_by(self, resume_text: str) -> bool:
        return any(
            _mentions(resume_text, variant)
            for alt in self.alternatives
            for variant in alias_variants(alt)
        )


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

    def add(
        label: str, alternatives: list[str], *, preferred: bool, any_of: bool = False
    ) -> None:
        unique = list(dict.fromkeys(alt for alt in alternatives if alt.strip()))
        if not unique:
            return
        key = tuple(sorted(alt.casefold() for alt in unique))
        if key in seen:
            return
        seen.add(key)
        # Two entries that name the same skill are ONE thing the posting asks
        # for, not two. The ByteDance posting listed "optimization techniques"
        # under required_skills and "optimization" under technologies, and both
        # were scored: one ask took two slots in the denominator, and the missing
        # list read "optimization" twice in two spellings, which is what made the
        # panel look broken rather than informative. Merging on a shared wording
        # rather than an identical label is safe because `covered_by` is already
        # an OR over the wordings. Any-of requirements are left alone -- see
        # `_Requirement.any_of` for why that distinction has to exist.
        folded = {alt.casefold() for alt in unique}
        if not any_of:
            for index, existing in enumerate(requirements):
                if existing.any_of:
                    continue
                if not folded & {alt.casefold() for alt in existing.alternatives}:
                    continue
                requirements[index] = _Requirement(
                    label=existing.label,
                    alternatives=tuple(dict.fromkeys([*existing.alternatives, *unique])),
                    # A skill the employer named as required stays required even
                    # when a preferred section spells it differently.
                    preferred=existing.preferred and preferred,
                )
                return
        requirements.append(
            _Requirement(
                label=label,
                alternatives=tuple(unique),
                preferred=preferred,
                any_of=any_of,
            )
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
                # Short enough to score verbatim, which used to mean scored ONLY
                # verbatim. `_keyword_alternatives` gives this path the same
                # normalisation the prose path has always had, without changing
                # the label the user reads back.
                alternatives, any_of = _keyword_alternatives(entry)
                add(
                    entry,
                    alternatives,
                    preferred=is_bonus(entry, section_is_preferred=section_is_preferred),
                    any_of=any_of,
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
            # Deliberately NOT reported as culture copy, even when the sentence
            # reads like it. "Collaborate with product managers to ship React
            # features" matches the culture vocabulary and is a responsibility
            # that names a technology, and calling it a values line would be a
            # claim nobody checked. It is already unscored either way -- a whole
            # sentence never appears verbatim in a resume -- so there is nothing
            # to gain from labelling it and a wrong label to lose.
            continue
        if _ALTERNATIVES_RE.search(entry) and len(recovered) > 1:
            # "X, Y or Z" is satisfied by any one of them, so it is one unit.
            # `recovered` is passed through untouched: these are already the
            # distinct skills, and deriving more wordings here would only blur a
            # set whose exact contents test_ats_scoring pins as the contract.
            add(
                entry,
                recovered,
                preferred=is_bonus(entry, section_is_preferred=section_is_preferred),
                any_of=True,
            )
        else:
            for term in recovered:
                alternatives, any_of = _keyword_alternatives(term)
                add(
                    term,
                    alternatives,
                    preferred=is_bonus(term, section_is_preferred=section_is_preferred),
                    any_of=any_of,
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
    # P0 instrumentation. Three buckets, because "the resume did not cover this"
    # has two very different causes and the difference decides what to build
    # next: evidence the vault genuinely lacks (only the user can close that,
    # and a gap question is the honest answer) versus evidence the vault holds
    # under a name this pass did not recognise (our bug, and a silent one --
    # it lowers `_achievable_ats_score`, which lowers the run's own target, so a
    # matching miss makes the run stop EARLIER and then blames the candidate).
    literal_only = 0
    alias_rescued = 0
    genuinely_absent = 0
    for requirement in requirements:
        free: list[str] = []
        selectable: list[str] = []
        matched_literally = False
        for item in evidence:
            haystack = item.text.casefold()
            literal = any(_mentions(haystack, alt) for alt in requirement.alternatives)
            if not (literal or _mentions_with_aliases(haystack, requirement.alternatives)):
                continue
            matched_literally = matched_literally or literal
            bucket = free if item.always_on_page else selectable
            bucket.append(item.where)
        found = bool(free or selectable)
        if found and matched_literally:
            literal_only += 1
        elif found:
            alias_rescued += 1
        else:
            genuinely_absent += 1
        coverage[requirement.label] = _Coverage(
            free=tuple(free[:_MAX_COVERAGE_CITATIONS]),
            selectable=tuple(selectable[:_MAX_COVERAGE_CITATIONS]),
        )
    log.info(
        "tailor.requirement_coverage",
        requirements=len(requirements),
        matched_literally=literal_only,
        matched_via_alias=alias_rescued,
        absent_from_vault=genuinely_absent,
    )
    return coverage


def _mentions_with_aliases(haystack: str, alternatives: tuple[str, ...]) -> bool:
    """Whether the text names any alternative, under any name the table knows.

    This pass used to search for the POSTING's wording only, so a vault saying
    "k8s" against a posting saying "Kubernetes" read as no evidence -- while the
    scorer, which compares canonical keys, counted the very same pair as a
    match. Two components disagreeing about whether the candidate has a skill is
    the bug; `skill_match` is the shared answer.
    """
    return any(
        _mentions(haystack, variant)
        for alt in alternatives
        for variant in alias_variants(alt)
    )


@dataclass(frozen=True)
class _ProjectScore:
    """One project fact's overlap with this JD, in the same units the ATS score uses.

    Before this existed, `shortlist_fact_ids` (the analyst) and `selected_fact_ids`
    (the writer) were the only two decisions in the whole pipeline with no Python
    signal behind them at all: both prompts said "strongest first" and left the
    model to read the raw facts list and judge it fresh every run. That is how a
    hackathon 3D-map demo and a health-audio side project kept beating an agentic
    LLM tailoring pipeline and an LLM claims-review tool onto an AI-engineer resume:
    nothing ever measured which one actually named the technologies and domains this
    JD asked for, so a run that read the weaker project's bullets first, or judged
    "polish" over fit, had nothing to correct it. Reusing `_jd_requirements`'s output
    rather than inventing a second keyword list means "relevant" here means the same
    thing it means in the Job Match number the user already sees.
    """

    fact_id: str
    title: str
    score: int
    matched: tuple[str, ...]
    # True when the fact carried no technologies of its own to match against, as
    # opposed to carrying them and matching none of this JD's.
    unscoreable: bool = False
    # Evidence a keyword count cannot see, used ONLY to order projects the
    # lexical score cannot separate. See `_evidence_rank`.
    live_url: bool = False
    ongoing: bool = False
    started_at: int = 0
    # True when this project is the same KIND of engineering the posting is
    # hiring for: a backend service against a platform role, a model against a
    # vision role. See services/role_lane.py for why a requirement count alone
    # gets this wrong, and `_evidence_rank` for how little it is allowed to do.
    lane_match: bool = False


def _project_relevance(
    facts: list[TailorFact],
    bullets_by_fact: dict[str, list[TailorBullet]],
    requirements: list[_Requirement],
    *,
    lanes: Sequence[str] = (),
) -> list[_ProjectScore]:
    """Rank every project fact by how many JD requirements its own text names.

    Scored from the project's title, its whole payload (description, keywords,
    roles, entity, type -- whatever the fact carries) and every one of its
    bullets, so a project whose relevance lives in its bullets rather than its
    one-line title still scores. Ties keep the fact's title alphabetically, so
    the ranking is stable across runs of the same profile against the same JD
    rather than depending on incidental dict/list order.
    """
    wanted = frozenset(lanes)
    scored: list[_ProjectScore] = []
    for f in facts:
        if f.kind != "project":
            continue
        payload = f.payload or {}
        haystack = " ".join(
            [
                f.title or "",
                json.dumps(payload, ensure_ascii=False),
                *(b.text for b in bullets_by_fact.get(f.id, [])),
            ]
        ).casefold()
        matched = tuple(
            sorted(
                {
                    req.label
                    for req in requirements
                    if any(_mentions(haystack, alt) for alt in req.alternatives)
                }
            )
        )
        # Anything the fact declares about itself: keywords, description, type,
        # whatever the payload carries. A project with none of that, and bullets
        # naming none either, was never scoreable rather than scored zero.
        declared = any(str(v).strip() for v in payload.values() if v not in (None, [], {}))
        scored.append(
            _ProjectScore(
                fact_id=f.id,
                title=f.title,
                score=len(matched),
                matched=matched,
                unscoreable=not matched and not declared,
                # Reads the payload fallbacks too, so a project whose link was
                # imported into `payload` rather than the column is no longer
                # ranked as if it had none. Same source the heading prints from.
                live_url=bool(_project_url(f)),
                ongoing=f.end_date is None,
                started_at=_as_sortable_date(f.start_date),
                # Judged from the project's own words rather than from the
                # requirement labels it matched, because the point is to see the
                # kind of work a keyword count is blind to. Set overlap rather
                # than equality: a full-stack posting is hiring for two lanes,
                # and a project in either one is the same kind of work as the
                # job. Comparing a single winner against a single winner is
                # what put the product/UI project last on those postings.
                lane_match=bool(wanted & text_lanes(haystack)),
            )
        )
    # Strongest first, and within a tier the keyword count cannot separate,
    # the evidence a keyword count cannot see. Reversed, because this list is
    # best-first while `_evidence_rank` reads worst-first.
    scored.sort(
        key=lambda p: (-p.score, *(-x for x in _evidence_rank(p)), p.title.casefold())
    )
    return scored


def _achievable_ats_score(
    requirements: list[_Requirement],
    coverage: dict[str, Any],
) -> Decimal:
    """The best score these facts could reach against this posting.

    A JD asks for what it asks for; a candidate has what they have. When a
    posting names threat modelling, BERT and enterprise governance and the vault
    holds none of them, no amount of rewriting reaches 80, and a resume that
    honestly covers everything it can is not a failure.

    Same coverage the briefing is built from, so "reachable" here means exactly
    what it means everywhere else in this file rather than a second opinion.
    """
    if not requirements:
        return TARGET_ATS_SCORE
    reachable = sum(1 for req in requirements if coverage[req.label].found)
    return Decimal(100 * reachable) / Decimal(len(requirements))


def _effective_target(achievable: Decimal) -> Decimal:
    """What this run should be measured against.

    Never above the fixed target, because a candidate who can cover everything
    is not asked for more than 80. Never above what is reachable either, which
    is the half that was missing.
    """
    return min(TARGET_ATS_SCORE, (achievable * ACHIEVABLE_TARGET_SHARE).quantize(Decimal("0.1")))


# A page that spills has to lose something, and there has to be a floor on how
# much. Below this the resume stops making a case at all, so a document still
# over length here is left over length rather than emptied to fit.
#
# Raised from 2 to 3 on Hemnaath's instruction, after a render came back with
# two projects and his flagship missing: "at least 3 projects with job.os among
# them", and explicitly, do not cut a project to force one page. He is buying
# substance with a slightly full page and he was told that is the trade.
MIN_PROJECTS_ON_PAGE = 3


# What the page sheds before it sheds evidence, in order.
#
# A project is the candidate's work. A summary is a sentence about that work,
# and a skills keyword he did not match against this posting is a word. Cutting
# a project to keep them is backwards, and it is what the page-fit loop did:
# #45 went straight to removing a project, so a run six lines over its budget
# spent a whole project to save a summary and thirty unmatched keywords.


def _drop_summary(json_resume: dict[str, Any]) -> bool:
    """Remove the lede. True if there was one to remove."""
    basics = json_resume.get("basics")
    if not isinstance(basics, dict) or not str(basics.get("summary") or "").strip():
        return False
    basics.pop("summary", None)
    return True


# How many skills a trimmed block still prints.
#
# MIN_PRINTED_SKILLS (8) is #44's floor against a block being GUTTED by drops.
# Shedding for space is a different question and 8 is too few for it: a real
# render came back "Languages: Go, Bash" and "Infrastructure: Autodesk Platform
# Services", one item, which reads as a stripped resume rather than a focused
# one. His instruction after seeing it was that the skills sections should not
# visibly change.
#
# So the page sheds the tail and keeps the block looking like a skills block.
MIN_KEPT_SKILLS_ON_PAGE = 20


def _trim_skills_to_fit(
    json_resume: dict[str, Any], requirements: list[_Requirement], budget: int
) -> int:
    """Shed the least relevant skill keywords until the page fits. Returns how many.

    Sheds only as much as the page needs, least relevant first, rather than
    deleting everything the posting did not literally name. The difference
    matters: an exact-phrase filter against this Amex posting kept "Go" and
    "Bash" and dropped "LLM Integration", "RAG" and "FastAPI", because the JD
    says "LLM APIs" and "retrieval patterns" and a literal test cannot see that
    those are the same subject. That is the lexical weakness this whole area
    keeps running into, and an all-or-nothing filter walks straight back into
    it.

    Relevance here is word overlap, which is coarse but only decides ORDER. The
    keywords that survive are the ones the page has room for, so a keyword the
    matcher misjudges costs its position rather than its place on the resume.
    """
    groups = json_resume.get("skills")
    if not isinstance(groups, list) or not groups:
        return 0
    wanted = {
        word
        for req in requirements
        for word in re.findall(r"[a-z0-9+#.]+", req.label.casefold())
        if len(word) > 2
    }

    def relevance(keyword: str) -> int:
        words = set(re.findall(r"[a-z0-9+#.]+", keyword.casefold()))
        return len(words & wanted)

    # Every keyword, worst first, so shedding walks up from the least relevant.
    ordered: list[tuple[int, int, int, str]] = []
    for gi, group in enumerate(groups):
        if not isinstance(group, dict):
            continue
        for ki, keyword in enumerate(group.get("keywords") or []):
            text = str(keyword).strip()
            if text:
                ordered.append((relevance(text), -gi, -ki, text))
    ordered.sort()
    total = len(ordered)
    doomed: set[tuple[int, str]] = set()
    dropped = 0
    for _score, neg_gi, _neg_ki, keyword in ordered:
        if estimated_page_lines(json_resume) <= budget:
            break
        if total - dropped <= MIN_KEPT_SKILLS_ON_PAGE:
            # The floor #44 established: a skills block gutted below this stops
            # being a skills block. A page still over here stays over.
            break
        doomed.add((-neg_gi, keyword))
        dropped += 1
        json_resume["skills"] = [
            {
                **g,
                "keywords": [
                    k
                    for ki, k in enumerate(g.get("keywords") or [])
                    if (gi, str(k).strip()) not in doomed
                ],
            }
            for gi, g in enumerate(groups)
            if isinstance(g, dict)
        ]
        json_resume["skills"] = [
            g for g in json_resume["skills"] if g.get("keywords")
        ]
    return dropped


def _as_sortable_date(value: object) -> int:
    """A date as one comparable number, 0 when there is none.

    Zero rather than a sentinel because "no date recorded" is genuinely the
    weakest recency claim, and it must not accidentally beat a real one.
    """
    digits = re.sub(r"\D", "", str(value or ""))[:8]
    return int(digits) if digits else 0


def _evidence_rank(score: _ProjectScore) -> tuple[int, int, int, int]:
    """How to order two projects the JD keyword count cannot separate.

    Role lane leads, ahead of the URL and the dates, and it is still only a
    tiebreak: it can reorder two projects the requirement count called equal and
    it can never lift one over a project that matched more. That boundary is the
    point. A platform posting naming computer vision once produces one vision
    requirement, and a side project matching only that must not displace the
    candidate's strongest backend work off a backend job; equally, when the
    counts genuinely tie, the project that is the same kind of engineering as
    the job belongs on the page. See services/role_lane.py.

    Against the real Amex posting his three strongest projects all scored 3 out
    of 66, so which one the page-fit cut removed was decided by the title
    tie-break: alphabetically. BedRocked and job.os are a deployed 2026 platform
    and a live job-search product; Infant Cry is a 2024 class project with no
    URL and a closed end date. The scorer could not tell them apart because it
    counts words, and none of that difference is a word.

    A reachable URL first, then still being worked on, then how recently it
    started. Deliberately in that order: job.os has NO start_date at all, so a
    recency-first rule would have sunk the very project this exists to rescue.

    This only ever breaks ties. The lexical score still decides the tiers, which
    is what keeps an unrelated project from climbing: a cross-encoder tried on
    this same data floated a 2019 internship model suite above ClaimFarm, and
    the reason this rule does not is that it never gets to reorder across tiers.
    """
    return (
        int(score.lane_match),
        int(score.live_url),
        int(score.ongoing),
        score.started_at,
    )


def _weakest_project_first(
    selected: list[TailorFact],
    scored: list[_ProjectScore],
) -> list[TailorFact]:
    """Selected project facts, weakest match for this posting first.

    Anything the ranker never scored sorts below anything it did: a project with
    no measured overlap earned its slot least.

    Within a tier the keyword count cannot separate, `_evidence_rank` decides,
    so the weakest is the one with no live URL and the oldest closed dates
    rather than whichever name comes last in the alphabet. Title remains the
    final tiebreak, so the same profile against the same posting still cuts the
    same thing every run.
    """
    rank = {p.fact_id: p.score for p in scored}
    evidence = {p.fact_id: _evidence_rank(p) for p in scored}
    projects = [f for f in selected if f.kind == "project"]
    # The default has to have `_evidence_rank`'s own shape and element types. A
    # short tuple padded with a string only compared cleanly while the leading
    # elements happened to differ, and would raise the moment they did not.
    unranked = tuple(0 for _ in _evidence_rank(_ProjectScore("", "", 0, ())))
    return sorted(
        projects,
        key=lambda f: (
            rank.get(f.id, -1),
            evidence.get(f.id, unranked),
            f.title.casefold(),
        ),
    )


# Where a project title stops naming the project and starts describing it.
# "BedRocked - Civic Sewer-Sequencing Platform" is called BedRocked; the rest is
# the subtitle, and a summary that mentions the project will use the name.
_PROJECT_NAME_SPLIT_RE = re.compile(r"\s+[\u2014\u2013-]\s+|:\s+|\s+\(")


def _project_short_name(title: str) -> str:
    return _PROJECT_NAME_SPLIT_RE.split(title.strip(), maxsplit=1)[0].strip()


def _summary_names_absent_project(
    summary: str | None,
    *,
    cut: list[TailorFact],
    json_resume: dict[str, Any],
) -> str | None:
    """A cut project the summary still points the reader at, if there is one.

    #45 made the page fit by removing the weakest project and reassembling, but
    the summary was written against the selection as it stood BEFORE the cut. A
    real run opened by positioning him "via BedRocked's LLM and classification
    work" on a page BedRocked had just been cut from, so the one line every
    reader reads first cited evidence the page does not show.

    That is worse than the spilling page it came from. A resume that runs long
    is untidy; a resume that names a project it does not contain reads as
    describing someone else's work, and it is the lede that does it.

    Checked against the assembled page rather than against the cut list alone,
    so a name the page still carries for another reason is not a false positive:
    the question is whether the reader can find what the summary points at, not
    which fact it came from.
    """
    if not summary:
        return None
    page = _ats_source_text(json_resume)
    for fact in cut:
        name = _project_short_name(fact.title)
        # A one-word name is not enough to be sure the summary means the project
        # rather than the word. Longer names are also why this can miss: a
        # summary saying only "Infant Cry" about "Infant Cry Sound Detection
        # System" goes unnoticed, which is the safe direction to fail.
        if not name or len(name) < 3:
            continue
        if mentions_word(summary, name) and not mentions_word(page, name):
            return name
    return None


def _run_is_done(
    *,
    score: Decimal,
    pass_target: Decimal,
    reachable: list[str],
    chargeable: dict[str, list[str]],
    passes: int,
    improved: bool,
    nothing_left: bool,
) -> bool:
    """Whether the compose/repair loop should stop after this pass.

    See the comment at the call site for the measurement behind the first
    clause. In short: `pass_target` moves with what the vault can reach, so
    scraping over a lowered floor is a weak reason to stop, and a run that did
    WORSE on its first pass was ending up with the better resume because it fell
    short and earned a repair.
    """
    # Something a repair could actually act on: a requirement the vault can
    # still cover, or a writing flag the writer introduced.
    worth_another_pass = bool(reachable) or bool(chargeable)
    cleared = score >= pass_target and not (
        worth_another_pass and score < TARGET_ATS_SCORE
    )
    return cleared or passes >= MAX_COMPOSE_PASSES or not improved or nothing_left


def _analyst_effort_label(effort: str | None) -> str:
    """Which arm produced a run, named so its absence is not a null.

    Unset means the gateway's own default applies, which is a real condition
    and has to be distinguishable from a run that recorded nothing. `None` in a
    report column reads as "not measured", and the two are not the same claim.
    """
    return effort or "gateway_default"


def _page_trim_note(trims: list[str]) -> str:
    """Say what came off the page before any project did.

    Named for the same reason a cut is: a summary that silently disappears
    reads as the tailor forgetting it, when it was dropped on purpose to keep a
    project the reader would rather see.
    """
    if not trims:
        return ""
    return f"\n(Trimmed to fit the page: {', '.join(trims)}.)"


def _page_cut_note(cut: list[str]) -> str:
    """Say what came off the page, for the same reason a corrected selection does.

    A project that silently disappears reads as the tailor having ignored it. It
    was measured, it was the weakest against this posting, and it did not fit.
    """
    if not cut:
        return ""
    return (
        f"\n(Cut for space, weakest match for this posting first: {', '.join(cut)}.)"
    )


# Sentence boundaries that survive a project literally named "job.os". Split on
# a terminator FOLLOWED BY SPACE, so the dot inside a name never ends a sentence.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")

# The writer explaining why something is not on the page.
_EXCLUSION_RE = re.compile(
    r"\b(?:exclud|omit|left out|leaving out|drop|not includ|without|no |lack)",
    re.I,
)
# ...and blaming it on missing evidence, which is the claim Python can check.
#
# The negation has to attach to the bullets, not merely share a sentence with
# them. A real run said "JD had no concrete requirements, so kept EPAM's
# strongest bullets plus the two projects with verified evidence", which is
# true, useful, and was deleted: it contains "no" and it contains "bullets",
# and the first version of this check asked for nothing more than that. The
# words in between are what distinguish "no bullets" from "no requirements, so
# I used the bullets".
_BULLET_CLAIM_RE = re.compile(
    r"\b(?:no|without|lack(?:s|ing|ed)?|missing|absent|zero)\b"
    r"(?:\W+\w+){0,3}\W+(?:bullets?|verified facts?)\b",
    re.I,
)


def _false_bullet_excuses(
    note: str,
    *,
    facts: list[TailorFact],
    bullets_by_fact: dict[str, list[TailorBullet]],
) -> tuple[str, list[str]]:
    """Drop sentences blaming an exclusion on bullets the fact demonstrably has.

    A real run left job.os off the page and said "ClaimFarm and job.os excluded,
    no verified fact bullets provided for them". job.os is verified and has
    three bullets. The rule it describes does not exist either: `metric_verified`
    is never read in this module and no code path drops a fact for unverified
    bullets. It is an invented justification for a real decision, which is the
    pattern #39 and #40 were built to end, surviving in the one place they did
    not look.

    #40 appends a correction, and that is right for a note that has gone STALE:
    the selection changed after the writer described it, so saying "this was
    written before that check" is true and enough. This is a different failure.
    The claim was never true, and a lie followed by a correction still reads as a
    lie first, because prose is read top to bottom. So it does not get printed.

    Whole sentences are dropped, never clauses. Excising a claim from the middle
    of a sentence somebody else wrote is the unreliable surgery the #40 docstring
    declines to attempt, and it still is; removing an entire assertion is not.
    """
    if not note.strip():
        return note, []
    bulleted = {
        fact.id: fact
        for fact in facts
        if fact.kind == "project" and bullets_by_fact.get(fact.id)
    }
    if not bulleted:
        return note, []
    # Whether ANY project could honestly be described this way. When every
    # project in the vault has bullets, a sentence blaming an exclusion on
    # missing ones is false whoever it means, so it does not have to name a
    # project to be caught: "It was excluded for having no verified bullets" is
    # exactly as untrue and would otherwise walk straight past a check that
    # requires a name.
    every_project_has_bullets = not [
        fact
        for fact in facts
        if fact.kind == "project" and not bullets_by_fact.get(fact.id)
    ]
    kept: list[str] = []
    accused: list[str] = []
    for sentence in _SENTENCE_SPLIT_RE.split(note):
        named = [
            fact
            for fact in bulleted.values()
            if mentions_word(sentence, _project_short_name(fact.title))
        ]
        if (
            (named or every_project_has_bullets)
            and _EXCLUSION_RE.search(sentence)
            and _BULLET_CLAIM_RE.search(sentence)
        ):
            accused.extend(_project_short_name(fact.title) for fact in named)
            log.warning(
                "tailor.note_invented_a_bullet_reason",
                projects=[f.title for f in named],
                sentence=sentence[:160],
            )
            continue
        kept.append(sentence)
    return " ".join(kept).strip(), sorted(set(accused))


def _honest_exclusion_note(
    accused: list[str],
    *,
    on_page: set[str],
    scored: list[_ProjectScore],
) -> str:
    """Say why the project is really absent, having removed the invented reason.

    Removing a false sentence and leaving nothing would be its own small
    dishonesty: the reader saw a project they expected, then saw it vanish with
    no account at all. The ranking is the account, and Python has it.
    """
    if not accused:
        return ""
    absent = [name for name in accused if name not in on_page]
    if not absent:
        # Named as excluded and on the page anyway: #40's case, already covered
        # by the correction note, so nothing more to say here.
        return ""
    ranked = {
        _project_short_name(score.title): score.score for score in scored
    }
    parts = []
    for name in absent:
        place = ranked.get(name)
        if place is None:
            parts.append(name)
        else:
            parts.append(f"{name} (matched {place} of this posting's requirements)")
    return (
        f"\n(Not on the page: {', '.join(parts)}. It ranked below the projects "
        "that are, which is the reason; it has verified bullets and any note "
        "above saying otherwise was wrong.)"
    )


def _selection_correction_note(substitutions: list[tuple[str, str]]) -> str:
    """Set the record straight when the ranking overruled the writer.

    `agent_note` is written before `_enforce_project_ranking` runs, so it
    describes the selection the writer wanted rather than the one that shipped.
    On the first real run after the check landed, the note said "Dropped
    ClaimFarm and job.os this pass" while both were on the finished page: the
    correction working, reported as the correction failing.

    Appended rather than edited in. The note is the model's own prose and there
    is no reliable way to excise a claim from a sentence somebody else wrote, so
    this states what shipped and says plainly that the text above it is stale.
    """
    if not substitutions:
        return ""
    restored = ", ".join(sorted({restored for _passed_over, restored in substitutions}))
    return (
        f"\n(Kept on the page after review: {restored}. This note was written "
        "before that check ran, so anything above about leaving them out is out "
        "of date.)"
    )


def _ranking_key(score: _ProjectScore) -> tuple[int, int, int, int, int]:
    """How two projects compare on MERIT, worst first.

    Deliberately excludes the title. The title is in the sort orders as a final
    tiebreak, so a run repeats itself, and that is all it is for: it says
    nothing about which project is better.

    Overriding the writer needs a reason, and "comes later in the alphabet" is
    not one. An earlier draft of this included the title and turned alphabetical
    order into an enforceable violation, which is the exact thing #66 exists to
    stop. Two projects alike on score and evidence are genuinely tied, and a tie
    is the writer's to call.
    """
    return (score.score, *_evidence_rank(score))


def _enforce_project_ranking(
    selected_fact_ids: set[str],
    scored: list[_ProjectScore],
    bullets_by_fact: dict[str, list[TailorBullet]],
) -> tuple[set[str], list[tuple[str, str]]]:
    """Hold the writer to the ranking unless it can show a reason.

    `_project_relevance` measures which project answers this JD, and until now
    that measurement was advice. `selected_fact_ids` was taken as given, filtered
    only for facts that exist, so the model could drop the top-ranked project for
    the bottom one and nothing noticed.

    It did. On a real run against an AI-engineer posting, the writer dropped
    ClaimFarm (the top project), job.os and RoleReveal, and explained itself:
    "lack verified bullets so were left out despite JD relevance". No such rule
    exists anywhere in this file. Nothing here reads `metric_verified`, and
    `_sanitize_selected_bullets` falls back to the candidate's own source text
    rather than dropping a bullet. The writer invented a constraint and applied
    it to the three projects the JD actually asked for.

    The prompts do allow one honest deviation, and it is worth keeping: a
    higher-scoring project whose own evidence is genuinely too thin to write
    from. The defect is that the claim was unfalsifiable. So the deviation
    survives and the reason is checked: a higher-scoring project may be passed
    over only if it truly has no bullets to write from. If it has them, the
    swap is undone.

    Returns the corrected selection and the substitutions made, so the caller
    can log what the writer tried to do rather than silently disagreeing with it.
    """
    projects = [p for p in scored if p.score > 0]
    if not projects:
        return selected_fact_ids, []

    def writable(fact_id: str) -> bool:
        return bool(bullets_by_fact.get(fact_id))

    kept = [p for p in projects if p.fact_id in selected_fact_ids]
    dropped = [p for p in projects if p.fact_id not in selected_fact_ids]

    corrected = set(selected_fact_ids)
    substitutions: list[tuple[str, str]] = []

    # Lowest-scoring kept first: that is the one a better project displaces.
    for candidate in dropped:
        if not writable(candidate.fact_id):
            # The one legitimate reason, and now the only one.
            continue
        # Compared on the FULL ranking key, not the score alone.
        #
        # #66 taught the ranking to break a tie on evidence, a reachable URL and
        # still being worked on, and changed the order everywhere the order is
        # read. It did not change this, the one place that ACTS on the order at
        # selection time, and a strict `p.score < candidate.score` means a tie
        # is never a violation. So on the first run after it deployed, job.os
        # and Infant Cry both scored 3, the writer picked Infant Cry, and
        # nothing corrected it: a live 2026 platform lost its slot to a 2024
        # class project because the enforcement could not see the difference the
        # ranking had just learned.
        rank = _ranking_key(candidate)
        weakest = min(
            (p for p in kept if p.fact_id in corrected and _ranking_key(p) < rank),
            key=_ranking_key,
            default=None,
        )
        if weakest is None:
            continue
        corrected.discard(weakest.fact_id)
        corrected.add(candidate.fact_id)
        substitutions.append((weakest.title, candidate.title))

    return corrected, substitutions


# How a lane is named to a reader. The keys are the engine's vocabulary; these
# are the words a person would use for the same thing.
_LANE_LABELS = {
    "backend": "backend and platform",
    "ml": "machine learning and models",
    "data": "data engineering",
    "test": "test and quality engineering",
    "frontend": "frontend",
}


def _lane_phrase(lanes: Sequence[str]) -> str:
    """The posting's kind of work, named the way a person would say it.

    Two lanes get "and" rather than a slash, because a full-stack posting is
    hiring for both and the sentence this feeds tells the writer that a project
    in either counts.
    """
    named = [_LANE_LABELS.get(lane, lane) for lane in lanes]
    if len(named) <= 1:
        return "".join(named)
    return f"{', '.join(named[:-1])} and {named[-1]}"


def _project_relevance_briefing(
    scored: list[_ProjectScore], *, lanes: Sequence[str] = ()
) -> str:
    """The project ranking Python already computed, so "strongest first" in the
    prompts means a measured ranking rather than whatever the model notices first.

    Silent below two projects: ranking a field of one settles nothing and reads as
    noise the model has to parse for free.
    """
    if len(scored) < 2:
        return ""
    lines = [
        "PROJECT RELEVANCE TO THIS JD, SCORED BY OVERLAP WITH THE SAME "
        "REQUIREMENTS THE JOB MATCH NUMBER ABOVE IS BUILT FROM. Python measured "
        "this against every project fact's title, payload and bullets; it is not "
        "the model's impression of which project is more polished or more recent:",
    ]
    if lanes:
        both = (
            " It asks for both, so a project in either one counts equally here."
            if len(lanes) > 1
            else ""
        )
        lines += [
            "",
            f"This posting is a {_lane_phrase(lanes)} role.{both} Where two "
            "projects match the same number of requirements, the one that is "
            "that same kind of work is marked below and goes on the page first. "
            "This never outranks a higher requirement match: a posting that "
            "names one term from another discipline does not make a side "
            "project in that discipline the better evidence.",
        ]
    for rank, item in enumerate(scored, start=1):
        if item.matched:
            detail = f": {', '.join(item.matched)}"
        elif item.unscoreable:
            # Not the same statement as "no overlap", and the difference decided
            # a real resume. A project whose fact declares no technologies and
            # whose bullets name none cannot match a JD written in technology
            # nouns, however relevant it is. Reporting that as no overlap told
            # the writer the project was irrelevant, which is a judgement
            # nobody made, and it dropped the candidate's flagship work.
            detail = " (nothing declared to score against, not judged irrelevant)"
        else:
            detail = " (no overlap found)"
        matches = _plural(item.score, "requirement match")
        same_lane = "  [same kind of work as this role]" if item.lane_match else ""
        lines.append(f"  {rank}. {item.title} -- {matches}{detail}{same_lane}")
    lines += [
        "",
        "Prefer the highest-scoring projects for shortlist_fact_ids and "
        "selected_fact_ids. The page holds 3 to 4 projects: when this many score "
        "above zero, use that many rather than settling for fewer. A more recent "
        "date, a fuller bullet list, or a more finished feel is not a reason to "
        "feature a lower-scoring project ahead of a higher-scoring one; only a "
        "real weakness in the higher-scoring project's own verified evidence is.",
    ]
    return "\n".join(lines)


def _availability_briefing(availability: Availability, asked: bool) -> str:
    """What the page will already say about when the candidate is free.

    Told to the writer only so it does not spend a sentence of a 45-word summary
    repeating a line the header already carries, and so it does not try to close
    the gap with prose when the profile holds no dates. The writer never sets
    this field: `_assemble_json_resume` does, from verified facts.
    """
    if not asked:
        return ""
    if availability.line:
        return (
            "AVAILABILITY. This posting asks the candidate to state when they "
            f"are free, and the page answers it in the contact row: "
            f'"{availability.line}". It is assembled from verified dates, it is '
            "already on the page, and it is not yours to write. Do not repeat it "
            "in the summary or in a bullet."
        )
    return (
        "AVAILABILITY. This posting asks the candidate to state when they are "
        "free, and their profile records no availability dates, no work "
        "authorization window and no graduation month. The page therefore says "
        "nothing about it and the user is told to add the dates on Profile. Do "
        "not answer it in prose: a start date you infer is a start date you "
        "invented, and it is the one claim on the page nobody can check."
    )


def _requirement_briefing(
    requirements: list[_Requirement],
    coverage: dict[str, _Coverage],
    *,
    status: str = "",
    achievable: Decimal | None = None,
) -> str:
    """The scoring rubric, handed to the model before it writes anything.

    The writer used to guess which terms mattered and find out afterwards. It now
    reads the same list the score is computed from, which is the single change
    that makes a first pass worth shipping.

    `achievable` names the number that list adds up to. The rubric told the
    writer how it would be scored but never what score to reach, so a pass could
    satisfy every instruction here and still stop short of what the evidence
    supported -- and the repair pass existed to notice. Stating the ceiling costs
    no extra model call: it is already computed before the prompt is built.
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
    if achievable is not None and must:
        reachable = len(must) - len(absent)
        lines += [
            "",
            f"THE NUMBER TO REACH, ON THIS PASS. The evidence can honestly cover "
            f"{reachable} of the {len(must)} must-haves, which scores "
            f"{achievable.quantize(Decimal('0.1'))}. That is this posting's ceiling "
            "and it is this pass's aim. Write as though there is no second pass, "
            "because there usually is not: reaching it means every requirement "
            "listed above as already met, or as met if you select the bullet "
            "carrying it, is on the finished page and still worded that way after "
            "your rewrite.",
            "The distance from that number to 100 is evidence the candidate does "
            "not have. It is not yours to close, and closing it by padding is the "
            "failure named above. A run that reaches the ceiling honestly has "
            "done the whole job, whatever the ceiling happens to be.",
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
    project_briefing: str = "",
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
        f"<jd>\n{(jd_clean or '')[:JD_CLEAN_PROMPT_CHARS]}\n</jd>\n\n"
        "CANDIDATE VERIFIED FACTS + BULLETS:\n"
        f"{_facts_feed(facts_payload)}\n\n"
        f"{project_briefing}\n\n"
        "REQUIREMENTS WHOSE OWN WORDS APPEAR NOWHERE IN THAT PROFILE:\n"
        f"{json.dumps([req.label for req in unresolved], indent=2)}\n\n"
        "Respond with a single JSON object matching this schema (no prose, no "
        f"fences):\n{json.dumps(TailorAnalysis.model_json_schema())}"
    )
    started = time.perf_counter()
    try:
        # No COMPOSE_EFFORT here, deliberately: left at the gateway's default.
        # See the comment on COMPOSE_EFFORT for why this specific step is the
        # one exception, not an oversight.
        #
        # `analyst_effort` is unset in production, so this stays the gateway
        # default and behaves exactly as it did before the setting existed. It
        # is here so the effort can be varied for a measured A/B without
        # shipping the change the measurement exists to justify.
        effort = get_settings().analyst_effort
        msg = await create_message(
            client,
            model=model,
            max_tokens=ANALYSIS_MAX_TOKENS,
            system=f"{CAREER_OPS_RULES}\n\n{ANALYST_SYSTEM_PROMPT}",
            messages=[{"role": "user", "content": prompt}],
            extra_headers={"x-manifest-tier": tier},
            **({"output_config": {"effort": effort}} if effort else {}),
        )
        _log_prompt_cache("analyst", 1, msg)
        log.info(
            "tailor.call_timing",
            # Recorded so a run identifies its own condition. Reading a
            # timing out of the execution log is useless for an A/B if the
            # log does not say which arm produced it.
            effort=_analyst_effort_label(effort),
            step="analyst",
            iteration=1,
            seconds=round(time.perf_counter() - started, 1),
        )
        analysis = parse_model_json(TailorAnalysis, response_text(msg))
    except (anthropic.APIError, httpx.HTTPError, TimeoutError, ValidationError) as exc:
        # An empty analysis is a planned degradation: `analysis_settled` downstream
        # knows the gaps were never checked. A dropped stream has to land here too,
        # or the one step that is allowed to fail softly takes the run down with it.
        # Same for `TimeoutError`: `create_message`'s wall-clock deadline (see
        # llm_json.py) raises it bare, past every anthropic/httpx class, for a
        # stream that stays technically alive on gateway keep-alives without the
        # per-read timeout ever tripping -- a real run sat here past 12 minutes
        # with no error before this existed.
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
