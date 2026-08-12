"""Interview prep generated from the job description and the candidate's own vault.

There is no question bank here, and there deliberately never will be. A bank of
questions scraped per company is somebody else's content and it is also the
weaker product: it cannot know that THIS posting asks for Go and Kubernetes, and
it cannot know that the candidate has a verified Go bullet and nothing at all
about Kubernetes. Both of those are things this app already holds, so the pack is
derived from them.

Four things are produced for one application:

  technical      questions grounded in the requirements the JD actually names
  behavioral     questions aimed at the competencies the JD actually names
  resume_probe   what a sharp interviewer would ask about the candidate's OWN
                 bullets, which is the highest-value and most neglected category
  candidate_ask  what the candidate should ask them, about this role

Plus a readiness number, and answer scaffolds for the behavioural half.

THE INVARIANT. A scaffold may only be built from `verified=True` ProfileFact and
FactBullet rows, cited by id, and where the vault has nothing the pack says so.
A competitor's agent was caught writing "recognized at a national conference"
into a real user's material, an accolade that user never received; the guards in
`_ground_answer` exist so that class of sentence cannot survive here. Where the
guards cannot prove a sentence, they delete it and record what they deleted,
because a visibly missing claim is recoverable and an invisible invented one is
not.

The readiness number is computed by Python from the JD's requirements measured
against the vault. The model's own estimate is carried as `model_estimate` and is
never the grade, which is the same stance `resume_engine.review_resume` takes and
for the same measured reason: a model's free-form 0-100 is not reproducible run
to run, and a score that moves while the evidence stands still is a score nobody
can act on.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import anthropic
import httpx
import structlog
from pydantic import ValidationError

from job_os.schemas.interviews import (
    AnswerScaffold,
    DefenceRisk,
    EvidenceCitation,
    GeneratedQuestion,
    InterviewPrepOutput,
    ReadinessBand,
    ReadinessReport,
    TopicReadiness,
    VaultFact,
)
from job_os.services.career_ops_rules import CAREER_OPS_RULES
from job_os.services.llm_json import (
    create_message,
    parse_model_json,
    response_diagnostics,
    response_text,
)
from job_os.services.resume_writing import (
    normalize_dashes,
    records_provisional_status,
    upgrades_status,
)

# Read-only reuse of the tailor's JD reader. The tailor already turns a parsed JD
# into scored REQUIREMENTS rather than keywords ("one or more of C++, Python or
# TypeScript" is one requirement Python satisfies outright, not three), already
# recovers the skills buried inside a prose requirement, already excludes the
# eligibility lines no evidence can match, and already word-matches every
# requirement against the whole vault with hand-rolled boundaries that survive
# C++, CI/CD and .NET. Every one of those behaviours is what makes a readiness
# number defensible, all of them were paid for with measured production failures,
# and a second copy here would drift from the first within a release. Nothing in
# tailor.py is modified; this module only reads it.
from job_os.services.tailor import (  # noqa: E402
    NUMBER_RE,
    TailorBullet,
    TailorFact,
    _build_facts_payload,
    _evidence_items,
    _jd_requirements,
    _requirement_coverage,
)
from job_os.settings import get_settings

log = structlog.get_logger(__name__)

# How many of each category to keep. Caps rather than targets: they are enforced
# after the model answers, so a model that returns thirty technical questions
# cannot turn one generate click into a wall nobody reads, or a hundred rows in
# the database. The numbers are what fits on one screen per section.
MAX_TECHNICAL = 8
MAX_BEHAVIORAL = 6
MAX_RESUME_PROBES = 8
MAX_CANDIDATE_ASKS = 5

# Same ceiling the tailor uses, and for the same reason: the gateway routes to a
# model with extended thinking and `max_tokens` covers the thinking block as well
# as the answer, so a budget sized for the JSON alone returns zero text blocks
# with stop_reason max_tokens. The retry gets more again, since the reason it is
# happening is that the answer did not fit.
PREP_MAX_TOKENS = 32000
PREP_RETRY_MAX_TOKENS = 48000

# Bands over the readiness number. Thresholds, not adjectives applied by feel:
# they are reported in the readiness report so the label is as checkable as the
# score. 80 matches the tailor's TARGET_ATS_SCORE, so "strong" means the same
# thing on the interview page as a met target does on the resume page.
READY_STRONG = 80
READY_MIXED = 50

# Sentence splitter for the guards. Deliberately simple: the guards work on
# generated prose, one or two sentences per STAR field, and a full sentence
# tokeniser would buy nothing over splitting on terminal punctuation.
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")

# Claims that need proof, because they are the ones a candidate cannot walk back
# in the room. The list is deliberately short and specific: a term that is not
# here can never trip the guard, which is what keeps false positives bounded, and
# every term here is a thing an interviewer can check or ask a follow-up about.
#
# The named failure this exists for is an agent that wrote "recognized at a
# national conference" about a real user. Recognition, awards, publications,
# patents and promotions are all in the same class: they are either on the record
# or they are fabrications, and the vault is the record.
_ACCOLADE_TERMS = (
    "award",
    "awards",
    "awarded",
    "prize",
    "won",
    "winner",
    "winning",
    "first place",
    "runner-up",
    "recognised",
    "recognized",
    "recognition",
    "honoured",
    "honored",
    "keynote",
    "invited talk",
    "best paper",
    "published",
    "publication",
    "patent",
    "patented",
    "promoted",
    "promotion",
    "featured",
    "press",
    "dean's list",
    "scholarship",
    "fellowship",
    "nationally",
    "national conference",
    "industry award",
)

# Ownership and title claims, held to the same rule. Found while testing the
# guards: a scaffold ended "I was given the team lead role for it" over evidence
# that says "worked on". It invents no number, names no award and upgrades no
# status, so every other guard passed it, and it is the single most checkable lie
# on this list, because the interviewer can ask who else was on the team.
#
# This is the career-ops rule enforced one layer further out: prefer "worked on"
# to "owned", "led" or "drove" unless a verified fact says otherwise. Accuracy
# beats a stronger-sounding verb, and it beats it hardest out loud.
_OWNERSHIP_TERMS = (
    "led",
    "leading",
    "team lead",
    "tech lead",
    "owned",
    "managed",
    "supervised",
    "directed",
    "headed",
    "in charge",
    "promoted",
    "drove",
    "solely",
    "single-handedly",
    "by myself",
    "on my own",
)


def _mentions_term(haystack: str, term: str) -> bool:
    """Word-boundary containment, tolerant of the punctuation in these terms.

    `re.escape` plus explicit lookarounds rather than `\\b`, because the terms
    include "dean's list", "runner-up" and "first place", where a word boundary
    lands in the wrong place or matches the wrong half.
    """
    return bool(re.search(rf"(?<!\w){re.escape(term)}(?!\w)", haystack))


@dataclass(frozen=True)
class ResumeBullet:
    """One bullet on the tailored resume, with the provenance it was built from.

    This is what makes the resume-probe category possible: the tailored resume
    already records, per bullet, which verified fact bullet it came from, so a
    probe can be written against the exact sentence a reader will see AND checked
    against the exact evidence behind it.
    """

    section: str
    text: str
    fact_bullet_id: str | None = None
    fact_id: str | None = None


@dataclass(frozen=True)
class PreparedQuestion:
    """One grounded question, ready to persist.

    `evidence` is non-empty whenever `scaffold` is not None. That is enforced in
    `_ground_answer`, asserted in the tests, and is the whole no-fabrication
    contract in one sentence.
    """

    category: str
    position: int
    question: str
    topic: str | None
    difficulty: str
    why_asked: str
    scaffold: AnswerScaffold | None
    evidence: list[EvidenceCitation]
    gap: bool
    gap_note: str | None
    removed_claims: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PrepResult:
    readiness: ReadinessReport
    questions: list[PreparedQuestion]
    note: str


SYSTEM_PROMPT = """\
You prepare a candidate for one specific interview. You are given the parsed job
description, the candidate's VERIFIED fact vault, and, when it exists, the
bullets of the resume they are sending for this role.

WHAT YOU MAY USE AS EXPERIENCE. The verified vault, and nothing else. Every
answer scaffold you write must cite the fact ids and fact bullet ids it is built
from, and the server drops any id that is not in the vault it gave you, so an
invented id costs you the whole scaffold. If the vault cannot answer a question,
say so: leave `scaffold` null and the server marks it as a gap for the candidate
to prepare. A gap honestly named is useful. A story they never lived is the one
failure this tool exists to prevent, and it is the failure that gets a candidate
caught in the room, where they cannot take it back.

Specifically: never write that they were recognised, awarded, promoted,
published, patented, featured or placed in a competition unless the verified
evidence says so in those terms. Never claim a title or sole credit the evidence
does not give them: where it says "worked on", the answer says worked on, not
led, owned, drove or ran. An interviewer can ask who else was on that team, so
the stronger verb is the one that costs them the room. Never upgrade a status.
Where the evidence says
demoed, pending approval, prototype, hackathon build, trial or mock, the answer
carries that qualifier; "demoed end to end, pending approval" is a stronger
answer than a claim the interviewer punctures with one follow-up.

FOUR CATEGORIES, all in one reply.

1. `technical`. Grounded in the requirements this posting actually names. Set
   `topic` to the requirement, and `difficulty` to warmup, core or stretch. Ask
   what an engineer on that team would ask: a decision, a trade-off, a failure
   mode. Not trivia, and not a definition a search engine answers. A question
   about a requirement the vault cannot back is still worth asking, because they
   will ask it. Leave the scaffold null and it becomes a gap.

2. `behavioral`. Aimed at the competencies the posting names, in the employer's
   own framing (ownership, ambiguity, cross-team work, mentoring, conflict).
   These are the ones to scaffold. A scaffold is a SKELETON in the candidate's
   own voice, four short fields, first person is correct here: situation, task,
   action, result. Use only what the cited evidence supports. Where the evidence
   has no number, the result field says what changed without inventing one.

3. `resume_probes`. One per resume bullet worth probing, and the most valuable
   category, because nobody prepares for it. For each bullet ask what a sharp
   interviewer would push on: the number in it, the scope claim, the ownership
   verb, what you would do differently, why that approach and not the obvious
   one. Set `topic` to a short handle for the bullet. Scaffold these from the
   bullet's own evidence.

4. `candidate_asks`. What the candidate should ask THEM, specific to this role
   and this company as the posting describes them. No scaffold, no evidence. A
   question they could ask any employer is a wasted turn; a question that shows
   they read the posting is not.

HOW THE READINESS NUMBER IS COMPUTED. Not by you. The server derives it from the
posting's must-have requirements measured against the verified vault, and it
already knows which are backed, so the table below is the answer key rather than
your estimate of it. `readiness_estimate` is advisory context only. Do not hold a
gap back because you already lowered the number, and do not describe the
candidate as ready or unready in `note`.

WRITING. Plain, short, spoken English. No em dashes, en dashes or double
hyphens. No inflated wording. Ask one thing per question.
"""

# What to say when the reply came back unusable. Deliberately not the tailor's
# EMPTY_REPLY_RETRY: that one names gap_questions and agent_note, fields this
# schema does not have, and handing a model instructions about fields it was
# never asked for is how a retry produces a second unusable reply.
COMPACT_RETRY = (
    "That reply could not be used. Send the same content again as one raw JSON "
    "object matching the schema you were given, and nothing else: no prose "
    "before or after it, no markdown fences. Keep it compact, since length is "
    "the likely reason the first attempt failed. Fewer questions, complete and "
    "correctly grounded, beats more questions that do not arrive: four technical, "
    "four behavioral, four resume_probes, three candidate_asks is enough. Keep "
    "each scaffold field to one sentence."
)


# ---- Readiness ---------------------------------------------------------------


def readiness(
    *,
    jd_parsed: dict[str, Any],
    facts: list[TailorFact],
    bullets_by_fact: dict[str, list[TailorBullet]],
    resume_bullets: list[ResumeBullet] | None = None,
    unverified_metric_bullet_ids: frozenset[str] = frozenset(),
    model_estimate: int | None = None,
) -> ReadinessReport:
    """How much of what this posting asks about the candidate can evidence.

    Deterministic: a pure function of the parsed JD and the verified vault, with
    no model in the path, so two runs against the same inputs return the same
    number and every point of it names a topic. That is the precedent
    `resume_engine._score_from_issues` set, and it was set because the
    alternative was measured: a reviewing model's own 0-100 moved nine points
    across three identical reviews of one document while the findings barely
    changed.

    The number is must-have coverage only. Nice-to-haves are reported and not
    scored, because a posting's bonus stack is not the bar for the interview: on
    a real posting, averaging twenty-five "nice to have" terms in took a genuine
    match down to 35. Missing one of those is worth knowing and is not worth a
    third of the grade.

    A JD with nothing scoreable in it returns `score=None` and band
    `not_scored`, not zero. Zero would tell a candidate they are unprepared for a
    posting the parser could not read, which is a statement about our parser
    dressed up as a statement about them.
    """
    requirements, prose, _excluded = _jd_requirements(jd_parsed or {})
    coverage = _requirement_coverage(requirements, _evidence_items(facts, bullets_by_fact))

    topics: list[TopicReadiness] = []
    for requirement in requirements:
        found = coverage[requirement.label]
        topics.append(
            TopicReadiness(
                topic=requirement.label,
                preferred=requirement.preferred,
                status="evidenced" if found.found else "gap",
                citations=[*found.free, *found.selectable],
                alternatives=list(requirement.alternatives),
            )
        )

    scored = [topic for topic in topics if not topic.preferred]
    evidenced = [topic for topic in scored if topic.status == "evidenced"]
    score: Decimal | None
    band: ReadinessBand
    if scored:
        # Bound to its own name before the comparisons. `score` is declared as
        # `Decimal | None` for the not-scored branch below, and comparing that
        # union against a threshold is not a thing a type checker can allow.
        share = (Decimal(len(evidenced)) / Decimal(len(scored)) * Decimal("100")).quantize(
            Decimal("0.1")
        )
        score = share
        band = (
            "strong"
            if share >= READY_STRONG
            else "mixed"
            if share >= READY_MIXED
            else "thin"
        )
    else:
        score = None
        band = "not_scored"

    return ReadinessReport(
        score=score,
        band=band,
        scored_topics=len(scored),
        evidenced_topics=len(evidenced),
        topics=topics,
        defence_risks=_defence_risks(
            resume_bullets or [],
            verified_bullet_ids=frozenset(
                bullet.id for bullets in bullets_by_fact.values() for bullet in bullets
            ),
            unverified_metric_bullet_ids=unverified_metric_bullet_ids,
        ),
        # Reported rather than dropped. A requirement sentence no resume and no
        # fact will ever word-match is still something the interview will cover,
        # and a report that silently omitted it would look like the tool had not
        # read the posting.
        unscored_requirements=prose,
        formula=(
            "readiness = must-have requirements with verified evidence / "
            "must-have requirements total. Nice-to-haves are listed and not "
            "scored. Requirement wording is matched against the whole verified "
            "vault, not only against the resume."
        ),
        thresholds={"strong": READY_STRONG, "mixed": READY_MIXED},
        model_estimate=model_estimate,
    )


def _defence_risks(
    resume_bullets: list[ResumeBullet],
    *,
    verified_bullet_ids: frozenset[str],
    unverified_metric_bullet_ids: frozenset[str],
) -> list[DefenceRisk]:
    """Bullets already on the resume that the vault cannot currently back.

    Two rules, both checkable. A bullet whose provenance points at a fact bullet
    that is no longer in the verified set is a claim the candidate has since
    un-verified or deleted, and it is still on the page an interviewer is
    holding. A bullet carrying a number whose source fact bullet is marked
    `metric_verified=False` is a number the candidate themselves flagged as
    unconfirmed, and it is the first thing a numerate interviewer asks about.

    Reported next to the readiness score, never folded into it. The score answers
    "can you speak to what they asked for"; this answers "what on your page will
    you be asked to defend". Averaging two different questions into one number is
    how a number stops meaning anything.
    """
    risks: list[DefenceRisk] = []
    for bullet in resume_bullets:
        where = f"{bullet.section}: {bullet.text[:80]}"
        if bullet.fact_bullet_id and bullet.fact_bullet_id not in verified_bullet_ids:
            risks.append(
                DefenceRisk(
                    text=bullet.text,
                    where=where,
                    reason=(
                        "This bullet's source fact is no longer in your verified "
                        "profile, so there is nothing on file to back it. Either "
                        "re-verify the fact or take the bullet off the resume "
                        "before you send it."
                    ),
                )
            )
            continue
        if (
            bullet.fact_bullet_id
            and bullet.fact_bullet_id in unverified_metric_bullet_ids
            and NUMBER_RE.search(bullet.text)
        ):
            risks.append(
                DefenceRisk(
                    text=bullet.text,
                    where=where,
                    reason=(
                        "You marked the number in the source fact as unconfirmed. "
                        "Expect to be asked how it was measured, and have the "
                        "measurement ready or drop the number."
                    ),
                )
            )
    return risks


def topic_briefing(report: ReadinessReport) -> str:
    """The answer key, handed to the model before it writes a question.

    The same move the tailor makes with `_requirement_briefing`, for the same
    reason: Python can already say which requirements the vault words and where,
    so a model spending a pass working that out is a pass spent rediscovering
    something free. Here it also decides which questions get a scaffold at all,
    which is the difference between a pack that invents a story and one that
    names a gap.
    """
    scored = [topic for topic in report.topics if not topic.preferred]
    evidenced = [topic for topic in scored if topic.status == "evidenced"]
    gaps = [topic for topic in scored if topic.status == "gap"]
    bonus = [topic for topic in report.topics if topic.preferred]

    lines = [
        "WHAT THIS POSTING ASKS FOR, ALREADY CHECKED AGAINST THE VERIFIED VAULT. "
        "Use this rather than working it out again.",
        "",
        f"MUST-HAVES ({len(scored)}).",
    ]
    if evidenced:
        lines.append(
            "  BACKED BY VERIFIED EVIDENCE. Ask about these AND scaffold the "
            "answers from the cited rows:"
        )
        for topic in evidenced:
            where = "; ".join(topic.citations) or "vault"
            lines.append(f"    - {topic.topic}  ->  {where}")
    if gaps:
        lines.append(
            "  WORDED NOWHERE IN THE VAULT. They will still ask, so still ask it, "
            "and leave the scaffold null. Do not reach for an unrelated fact to "
            "cover one of these: a scaffold about the wrong work is worse than a "
            "named gap, because the candidate walks into the room believing they "
            "have an answer:"
        )
        for topic in gaps:
            lines.append(f"    - {topic.topic}")
    if bonus:
        lines += [
            "",
            "NICE TO HAVE, not part of the readiness number: "
            f"{', '.join(topic.topic for topic in bonus)}",
        ]
    if report.unscored_requirements:
        lines += [
            "",
            "REQUIREMENT SENTENCES TOO LONG TO MATCH AGAINST ANY ONE FACT. Read "
            "them for what the interview will cover:",
            *(f"    - {sentence}" for sentence in report.unscored_requirements[:12]),
        ]
    if report.defence_risks:
        lines += [
            "",
            "CLAIMS ALREADY ON THE RESUME THAT THE VAULT CANNOT BACK. These are "
            "the highest-value probes in the pack, so ask about every one of "
            "them, and say plainly in why_asked what the interviewer would push "
            "on:",
            *(f"    - {risk.text}  ({risk.reason})" for risk in report.defence_risks),
        ]
    return "\n".join(lines)


# ---- Grounding ---------------------------------------------------------------


def _evidence_index(
    facts: list[TailorFact], bullets_by_fact: dict[str, list[TailorBullet]]
) -> dict[str, EvidenceCitation]:
    """Every citable verified row, keyed by the id a model may cite.

    Facts and bullets share one namespace because the model cites from one list
    and confusing the two is not an error worth failing a scaffold over: what
    matters is that the id exists in the verified set, and that the text stored
    alongside the citation is OURS rather than the model's.
    """
    index: dict[str, EvidenceCitation] = {}
    for fact in facts:
        label = " at ".join(part for part in (fact.title, fact.org) if part) or fact.kind
        index[str(fact.id)] = EvidenceCitation(
            fact_id=str(fact.id),
            fact_bullet_id=None,
            label=f"{fact.kind}: {label}",
            # The fact's own verified fields. Payload included because that is
            # where courses, technologies and URLs live, and a scaffold about a
            # project routinely rests on them.
            text=" ".join(
                part
                for part in (
                    fact.title,
                    fact.org,
                    json.dumps(fact.payload or {}, ensure_ascii=False)
                    if fact.payload
                    else "",
                )
                if part
            ),
        )
        for bullet in bullets_by_fact.get(fact.id, []):
            index[str(bullet.id)] = EvidenceCitation(
                fact_id=str(fact.id),
                fact_bullet_id=str(bullet.id),
                label=label,
                text=bullet.text,
            )
    return index


def _cited(
    question: GeneratedQuestion, index: dict[str, EvidenceCitation]
) -> list[EvidenceCitation]:
    """The citations that name a row that really exists, in the model's order.

    An id the vault does not contain is dropped rather than repaired. The tailor
    takes the same line with an analyst that invents a `fact_bullet_id`, and the
    reason is the same: an id nobody can resolve is not weak evidence, it is no
    evidence, and a scaffold resting on one is exactly the fabrication this
    module exists to stop.
    """
    seen: set[str] = set()
    out: list[EvidenceCitation] = []
    for raw in [*question.fact_bullet_ids, *question.fact_ids]:
        key = str(raw)
        if key in seen:
            continue
        seen.add(key)
        citation = index.get(key)
        if citation is None:
            log.warning("interview_prep.unknown_evidence_id", cited=key[:60])
            continue
        out.append(citation)
    return out


def _unsupported_sentences(
    text: str, *, source_text: str
) -> tuple[str, list[str]]:
    """Strip the sentences the cited evidence cannot support, and say which.

    Four checks, each one a claim an interviewer can test:

      numbers      a figure that appears nowhere in the cited evidence
      accolades    a recognition, award, publication, patent or promotion the
                   evidence does not record
      ownership    a title or sole-credit claim the evidence does not record
      status       completion language over evidence that records the work as
                   unfinished, using the same rule the resume writer uses

    Sentence-level rather than field-level, so one unsupported clause does not
    throw away a scaffold that is otherwise correctly grounded. Whatever is
    removed is returned, because a claim the user can see was dropped is a claim
    they can go and verify; one that vanishes silently just looks like a thin
    answer.
    """
    if not text.strip():
        return text, []
    haystack = source_text.casefold()
    source_numbers = set(NUMBER_RE.findall(source_text))
    kept: list[str] = []
    removed: list[str] = []
    for sentence in _SENTENCE_RE.split(text.strip()):
        if not sentence.strip():
            continue
        reasons: list[str] = []
        invented = set(NUMBER_RE.findall(sentence)) - source_numbers
        if invented:
            reasons.append(f"number not in the evidence ({', '.join(sorted(invented))})")
        lowered = sentence.casefold()
        accolades = [
            term
            for term in _ACCOLADE_TERMS
            if _mentions_term(lowered, term) and not _mentions_term(haystack, term)
        ]
        if accolades:
            reasons.append(f"claim the evidence does not record ({', '.join(accolades)})")
        ownership = [
            term
            for term in _OWNERSHIP_TERMS
            if _mentions_term(lowered, term) and not _mentions_term(haystack, term)
        ]
        if ownership:
            reasons.append(
                "ownership or title claim the evidence does not record "
                f"({', '.join(ownership)})"
            )
        if upgrades_status(sentence, source_text):
            reasons.append("says the work finished when the evidence says it did not")
        if reasons:
            removed.append(f"{sentence.strip()} [{'; '.join(reasons)}]")
            continue
        kept.append(sentence.strip())
    return " ".join(kept), removed


def _ground_answer(
    question: GeneratedQuestion,
    index: dict[str, EvidenceCitation],
    *,
    scaffoldable: bool,
) -> tuple[AnswerScaffold | None, list[EvidenceCitation], bool, str | None, list[str]]:
    """Keep a scaffold only if verified evidence can carry every claim in it.

    Returns the scaffold, its provenance, whether this is a gap, the gap note,
    and anything a guard removed. The three outcomes:

      grounded   real citations, and every sentence supported. Scaffold kept.
      gap        no citations resolve, or the guards emptied the scaffold. The
                 scaffold is dropped and the question is marked as a gap.
      no answer  the category does not take one at all (a question the candidate
                 asks the interviewer has no answer to scaffold).

    Nothing here tries to repair a scaffold into being true. A scaffold that
    survives is one the vault already supported.
    """
    if not scaffoldable:
        return None, [], False, None, []

    citations = _cited(question, index)
    if not citations:
        return (
            None,
            [],
            True,
            (
                "Nothing in your verified profile answers this yet. Prepare it as "
                "a real gap: either add the experience as a verified fact if you "
                "have it, or plan to say plainly what you have done that is "
                "closest and what you have not done."
            ),
            [],
        )

    if question.scaffold is None:
        # The model cited evidence and declined to write the skeleton. Reported as
        # a gap with the citations kept, so the user can see which of their own
        # work is nearest rather than being told there is nothing.
        return (
            None,
            citations,
            True,
            (
                "Your nearest verified evidence is cited below, but it does not "
                "add up to a full answer. Worth deciding in advance how much of "
                "this you claim."
            ),
            [],
        )

    source_text = "\n".join(citation.text for citation in citations)
    removed: list[str] = []
    cleaned: dict[str, str] = {}
    for name in ("situation", "task", "action", "result"):
        # First person is left alone on purpose. A resume bullet may not say "I";
        # an interview answer is spoken by the candidate and "I" is the correct
        # word. The guards here are about truth, not about voice.
        value = normalize_dashes(getattr(question.scaffold, name)) or ""
        kept, dropped = _unsupported_sentences(value, source_text=source_text)
        cleaned[name] = kept
        removed.extend(f"{name}: {item}" for item in dropped)

    scaffold = AnswerScaffold(**cleaned)
    if not scaffold.joined().strip():
        # Everything in it was unsupported. That is a gap, and it is the most
        # important gap in the pack: the model tried to answer and could not do it
        # honestly, so the user is told rather than handed the wreckage.
        return (
            None,
            citations,
            True,
            (
                "A draft answer was written and then removed, because every part "
                "of it claimed something your verified profile does not record. "
                "The removed wording is listed so you can add the evidence if it "
                "is real. Until then, treat this as a gap."
            ),
            removed,
        )
    if removed:
        log.info(
            "interview_prep.claims_removed",
            question=question.question[:80],
            removed=len(removed),
        )
    if records_provisional_status(source_text):
        # Not a removal, a reminder. The evidence says the work is unfinished, so
        # the qualifier belongs in the spoken answer even when no sentence in the
        # scaffold broke the rule.
        scaffold = AnswerScaffold(
            situation=scaffold.situation,
            task=scaffold.task,
            action=scaffold.action,
            result=(
                f"{scaffold.result} Keep the qualifier your evidence carries: this "
                "work was not finished, and saying so is stronger than a claim a "
                "follow-up question punctures."
            ).strip(),
        )
    return scaffold, citations, False, None, removed


# ---- Generation --------------------------------------------------------------


_CATEGORY_LIMITS = {
    "technical": MAX_TECHNICAL,
    "behavioral": MAX_BEHAVIORAL,
    "resume_probe": MAX_RESUME_PROBES,
    "candidate_ask": MAX_CANDIDATE_ASKS,
}
# Which categories get an answer scaffold. A question the candidate asks the
# interviewer has no answer of theirs to ground, and a technical question is
# knowledge rather than experience, so scaffolding one from a project bullet
# would be inventing a story about how they learned it. The two categories that
# are ABOUT the candidate's own history are the two that get scaffolded.
_SCAFFOLDED = frozenset({"behavioral", "resume_probe"})


def _client() -> anthropic.AsyncAnthropic:
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set.")
    # `auth_token`, not `api_key`. The Manifest gateway wants
    # `Authorization: Bearer`, and `api_key` sends `x-api-key`, which it ignores.
    return anthropic.AsyncAnthropic(
        auth_token=settings.anthropic_api_key,
        base_url=settings.anthropic_base_url or None,
    )


def _build_prompt(
    *,
    job_title: str,
    company_name: str | None,
    jd_parsed: dict[str, Any],
    jd_clean: str,
    facts_payload: list[dict[str, Any]],
    resume_bullets: list[ResumeBullet],
    briefing: str,
) -> str:
    resume_block = (
        json.dumps(
            [
                {
                    "section": bullet.section,
                    "text": bullet.text,
                    "fact_bullet_id": bullet.fact_bullet_id,
                    "fact_id": bullet.fact_id,
                }
                for bullet in resume_bullets
            ],
            ensure_ascii=False,
        )[:9000]
        if resume_bullets
        else (
            "[] (no tailored resume for this application yet, so write the "
            "resume_probes against the strongest verified bullets instead, and "
            "say in why_asked that the bullet is from the profile rather than "
            "from a tailored page)"
        )
    )
    return (
        f"ROLE: {job_title}\n"
        f"COMPANY: {company_name or 'unknown'}\n\n"
        "JOB DESCRIPTION (parsed):\n"
        f"{json.dumps(jd_parsed or {}, ensure_ascii=False, indent=2)[:9000]}\n\n"
        "JOB DESCRIPTION (clean text, truncated):\n"
        f"<jd>\n{(jd_clean or '')[:8000]}\n</jd>\n\n"
        "VERIFIED FACT VAULT. These ids are the only ones you may cite:\n"
        f"{json.dumps(facts_payload, ensure_ascii=False)[:16000]}\n\n"
        "BULLETS ON THE RESUME BEING SENT FOR THIS ROLE, with the verified "
        "evidence each was built from:\n"
        f"{resume_block}\n\n"
        f"{briefing}\n\n"
        f"CAPS. At most {MAX_TECHNICAL} technical, {MAX_BEHAVIORAL} behavioral, "
        f"{MAX_RESUME_PROBES} resume_probes, {MAX_CANDIDATE_ASKS} candidate_asks. "
        "Anything past a cap is discarded by the server, so spend the room on the "
        "questions most likely to be asked.\n\n"
        "Reply with one raw JSON object matching this schema and nothing else: no "
        "prose around it, no markdown fences.\n"
        f"{json.dumps(InterviewPrepOutput.model_json_schema())}"
    )


async def generate_prep(
    *,
    jd_parsed: dict[str, Any],
    jd_clean: str,
    job_title: str,
    company_name: str | None = None,
    facts: list[TailorFact],
    bullets_by_fact: dict[str, list[TailorBullet]],
    resume_bullets: list[ResumeBullet] | None = None,
    unverified_metric_bullet_ids: frozenset[str] = frozenset(),
) -> PrepResult:
    """Build one prep pack. Backend agnostic: no ORM, no session, no IO but the model call.

    Structured this way for the reason `run_tailor` is: the interesting logic is
    the grounding and the score, both of which are worth testing against
    fixtures without a database anywhere near them. The Postgres adapter is
    `prep_for_application`.

    One model call, with one retry when the reply is not usable. The readiness
    report needs no model at all, so a failed call still returns a pack with a
    real score and an honest note rather than an error page, which is the same
    trade `provisional_review` makes for a resume that cannot be rendered.
    """
    resume_bullets = resume_bullets or []
    report = readiness(
        jd_parsed=jd_parsed,
        facts=facts,
        bullets_by_fact=bullets_by_fact,
        resume_bullets=resume_bullets,
        unverified_metric_bullet_ids=unverified_metric_bullet_ids,
    )
    index = _evidence_index(facts, bullets_by_fact)
    settings = get_settings()
    prompt = _build_prompt(
        job_title=job_title,
        company_name=company_name,
        jd_parsed=jd_parsed,
        jd_clean=jd_clean,
        facts_payload=_build_facts_payload(facts, bullets_by_fact),
        resume_bullets=resume_bullets,
        briefing=topic_briefing(report),
    )
    messages: list[anthropic.types.MessageParam] = [{"role": "user", "content": prompt}]

    async def ask(*, max_tokens: int = PREP_MAX_TOKENS) -> tuple[str, Any]:
        response = await create_message(
            _client(),
            model=settings.anthropic_model_tailor,
            max_tokens=max_tokens,
            # The candidate's own boundaries: what they may be positioned as,
            # what they have not done, which statuses are provisional. A prep
            # pack that ignores them would coach the candidate into claims the
            # resume engine refuses to print, which is worse than useless.
            system=f"{SYSTEM_PROMPT}\n\n{CAREER_OPS_RULES}",
            messages=messages,
            extra_headers={"x-manifest-tier": settings.manifest_tier_sonnet},
        )
        return response_text(response), response

    output: InterviewPrepOutput | None = None
    try:
        raw, response = await ask()
        try:
            output = parse_model_json(InterviewPrepOutput, raw)
        except ValidationError:
            log.warning(
                "interview_prep.not_json",
                preview=raw[:300],
                **response_diagnostics(response),
            )
            messages = [*messages, {"role": "user", "content": COMPACT_RETRY}]
            retry_raw, retry_response = await ask(max_tokens=PREP_RETRY_MAX_TOKENS)
            try:
                output = parse_model_json(InterviewPrepOutput, retry_raw)
            except ValidationError:
                log.warning(
                    "interview_prep.not_json_after_retry",
                    preview=retry_raw[:300],
                    **response_diagnostics(retry_response),
                )
                raise
    except (
        ValidationError,
        json.JSONDecodeError,
        anthropic.APIError,
        httpx.HTTPError,
        RuntimeError,
    ) as exc:
        # Same catch set as the resume review, including the raw httpx error a
        # stream that dies mid-reply arrives as, past every anthropic class.
        log.warning("interview_prep.model_failed", error=repr(exc))
        return PrepResult(
            readiness=report,
            questions=[],
            note=(
                "The question generator did not answer, so this pack has the "
                "readiness report only. That part runs on rules rather than on a "
                "model, so it is complete and correct as it stands. Generate again "
                "to add the questions."
            ),
        )

    questions = _prepare(output, index=index)
    report = report.model_copy(update={"model_estimate": output.readiness_estimate})
    scaffolded = sum(1 for question in questions if question.scaffold is not None)
    gaps = sum(1 for question in questions if question.gap)
    note = normalize_dashes(output.note) or ""
    return PrepResult(
        readiness=report,
        questions=questions,
        note=(
            f"{note}\n\n{len(questions)} questions, {scaffolded} with an answer "
            f"scaffold built from verified evidence, {gaps} marked as gaps to "
            "prepare. Every scaffold cites the rows it came from."
        ).strip(),
    )


def _prepare(
    output: InterviewPrepOutput, *, index: dict[str, EvidenceCitation]
) -> list[PreparedQuestion]:
    """Turn the model's four lists into grounded, capped, ordered questions."""
    prepared: list[PreparedQuestion] = []
    for category, generated in (
        ("technical", output.technical),
        ("behavioral", output.behavioral),
        ("resume_probe", output.resume_probes),
        ("candidate_ask", output.candidate_asks),
    ):
        seen: set[str] = set()
        for question in generated:
            text = normalize_dashes(question.question) or ""
            if not text.strip():
                continue
            # A model that asks the same thing twice in one pack costs the reader
            # trust in the whole list, and the duplicate is free to drop.
            key = re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()
            if key in seen:
                continue
            seen.add(key)
            position = sum(1 for item in prepared if item.category == category)
            if position >= _CATEGORY_LIMITS[category]:
                break
            scaffold, evidence, gap, gap_note, removed = _ground_answer(
                question, index, scaffoldable=category in _SCAFFOLDED
            )
            prepared.append(
                PreparedQuestion(
                    category=category,
                    position=position,
                    question=text,
                    topic=normalize_dashes(question.topic) or None,
                    difficulty=question.difficulty,
                    why_asked=normalize_dashes(question.why_asked) or "",
                    scaffold=scaffold,
                    evidence=evidence,
                    gap=gap,
                    gap_note=gap_note,
                    removed_claims=removed,
                )
            )
    return prepared


# ---- Spaced review ----------------------------------------------------------
#
# Deliberately the smallest thing that works, and deliberately separate from
# everything above: nothing in generation reads these fields, and nothing here
# can affect a pack's grounding. The interval is a fixed ladder rather than SM-2,
# because the pack is prepared against one interview a week or two out, and a
# scheduler tuned for months of retention would put half the questions past the
# date that matters.
REVIEW_INTERVAL_DAYS = {"shaky": 1, "workable": 3, "solid": 7}


def next_review_at(confidence: str, *, now: datetime | None = None) -> datetime | None:
    """When to show a practised question again, or None when it is not tracked."""
    days = REVIEW_INTERVAL_DAYS.get(confidence)
    if days is None:
        return None
    return (now or datetime.now(UTC)) + timedelta(days=days)


# ---- Postgres adapter -------------------------------------------------------


def verified_facts_statement(user_id: UUID) -> Any:
    """The one query that decides what may become an answer.

    Named and separated so it can be asserted on directly. `verified.is_(True)`
    is the whole no-fabrication contract at the storage layer: an unverified fact
    is a draft the agent proposed and the user never confirmed, and a draft that
    reaches a scaffold is the tool telling the candidate a story about
    themselves. A test compiles this statement and checks the filter is still in
    it, because the failure mode of dropping it is silent and only shows up in an
    interview.
    """
    from sqlalchemy import select

    from job_os.db.models import ProfileFact

    return select(ProfileFact).where(
        ProfileFact.user_id == user_id, ProfileFact.verified.is_(True)
    )


def _vault_from_supplied(
    supplied: list[VaultFact],
) -> tuple[list[TailorFact], dict[str, list[TailorBullet]], set[str]]:
    """Turn a caller-supplied vault into the same shape the loader produces.

    The `verified` filter is applied HERE, not by the caller. The browser is
    trusted to know where the user's facts live and is not trusted to decide
    which of them may become an answer, because that decision is the whole
    contract and a client bug would break it silently.
    """
    facts: list[TailorFact] = []
    bullets_by_fact: dict[str, list[TailorBullet]] = {}
    unverified_metrics: set[str] = set()
    for fact in supplied:
        if not fact.verified:
            continue
        facts.append(
            TailorFact(
                id=fact.id,
                kind=fact.kind,
                title=fact.title,
                org=fact.org,
                start_date=fact.start_date,
                end_date=fact.end_date,
                location=fact.location,
                source_url=fact.source_url,
                payload=fact.payload,
            )
        )
        for bullet in fact.bullets:
            bullets_by_fact.setdefault(fact.id, []).append(
                TailorBullet(id=bullet.id, fact_id=fact.id, text=bullet.text)
            )
            if not bullet.metric_verified:
                unverified_metrics.add(bullet.id)
    return facts, bullets_by_fact, unverified_metrics


async def prep_for_application(
    session: Any,
    *,
    user_id: UUID,
    application_id: UUID,
    supplied_facts: list[VaultFact] | None = None,
) -> tuple[Any, PrepResult]:
    """Load one application's inputs, generate a pack, and persist it.

    Returns the stored `InterviewPrep` and the result it came from. Imports the
    ORM lazily for the same reason the tailor does: the pure core above stays
    importable in runtimes that have neither SQLAlchemy nor the models.

    `supplied_facts` is the vault sent by a caller whose facts do not live in
    this database, which is the Appwrite workspace deployment. Unverified rows
    are dropped here rather than trusted, so an honest client and a buggy one get
    the same guarantee.
    """
    from sqlalchemy import select
    from sqlalchemy.orm import joinedload

    from job_os.db.models import (
        Application,
        FactBullet,
        InterviewPrep,
        InterviewQuestion,
        Job,
        ResumeVersion,
    )

    application = (
        (
            await session.execute(
                select(Application)
                .options(joinedload(Application.job).joinedload(Job.company))
                .where(
                    Application.id == application_id,
                    Application.user_id == user_id,
                )
            )
        )
        .unique()
        .scalar_one_or_none()
    )
    if application is None:
        raise LookupError("application not found")

    if supplied_facts is not None:
        facts, bullets_by_fact, unverified_metrics = _vault_from_supplied(supplied_facts)
    else:
        facts_rows = list(
            (await session.execute(verified_facts_statement(user_id))).scalars().all()
        )
        fact_ids = [row.id for row in facts_rows]
        bullet_rows = (
            list(
                (
                    await session.execute(
                        select(FactBullet).where(FactBullet.fact_id.in_(fact_ids))
                    )
                )
                .scalars()
                .all()
            )
            if fact_ids
            else []
        )

        facts = [
            TailorFact(
                id=str(row.id),
                kind=row.kind,
                title=row.title,
                org=row.org,
                start_date=row.start_date,
                end_date=row.end_date,
                location=row.location,
                source_url=row.source_url,
                payload=row.payload or {},
            )
            for row in facts_rows
        ]
        bullets_by_fact = {}
        unverified_metrics = set()
        for row in bullet_rows:
            bullets_by_fact.setdefault(str(row.fact_id), []).append(
                TailorBullet(
                    id=str(row.id),
                    fact_id=str(row.fact_id),
                    text=row.text,
                    target_role=row.target_role,
                )
            )
            if not row.metric_verified:
                unverified_metrics.add(str(row.id))

    # The newest resume tailored for this application, or failing that for its
    # job. Either is the page the interviewer will be holding; neither is
    # required, and without one the probes fall back to the vault's own bullets.
    version = (
        (
            await session.execute(
                select(ResumeVersion)
                .where(
                    (ResumeVersion.spawned_from_application_id == application_id)
                    | (ResumeVersion.spawned_from_job_id == application.job_id)
                )
                .where(ResumeVersion.archived_at.is_(None))
                .order_by(ResumeVersion.created_at.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    resume_bullets = _resume_bullets(version)

    result = await generate_prep(
        jd_parsed=application.job.jd_parsed or {},
        jd_clean=application.job.jd_clean or application.job.jd_raw or "",
        job_title=application.job.title,
        company_name=application.job.company.name if application.job.company else None,
        facts=facts,
        bullets_by_fact=bullets_by_fact,
        resume_bullets=resume_bullets,
        unverified_metric_bullet_ids=frozenset(unverified_metrics),
    )

    prep = InterviewPrep(
        user_id=user_id,
        application_id=application_id,
        job_id=application.job_id,
        resume_version_id=version.id if version else None,
        readiness_score=result.readiness.score,
        readiness_report=result.readiness.model_dump(mode="json"),
        model_estimate=result.readiness.model_estimate,
        note=result.note,
    )
    session.add(prep)
    await session.flush()
    for question in result.questions:
        session.add(
            InterviewQuestion(
                prep_id=prep.id,
                category=question.category,
                position=question.position,
                question=question.question,
                topic=question.topic,
                difficulty=question.difficulty,
                why_asked=question.why_asked,
                scaffold=(
                    question.scaffold.model_dump(mode="json") if question.scaffold else None
                ),
                evidence=[citation.model_dump(mode="json") for citation in question.evidence],
                gap=question.gap,
                gap_note=question.gap_note,
                removed_claims=question.removed_claims,
            )
        )
    await session.flush()
    return prep, result


def _resume_bullets(version: Any) -> list[ResumeBullet]:
    """Read the tailored page's bullets out of a version's provenance.

    Provenance is the right source rather than the document: every tailored
    bullet is recorded there against the fact bullet it came from, which is what
    lets a probe be checked against its own evidence. A version with no
    provenance (an imported master, say) falls back to the document's own
    highlights, with no evidence attached, and the probes for those are written
    without a scaffold.
    """
    if version is None:
        return []
    bullets: list[ResumeBullet] = []
    for entry in version.provenance or []:
        if not isinstance(entry, dict):
            continue
        text = str(entry.get("text") or "").strip()
        if not text:
            continue
        bullets.append(
            ResumeBullet(
                section=str(entry.get("section") or "work"),
                text=text,
                fact_bullet_id=(
                    str(entry["fact_bullet_id"]) if entry.get("fact_bullet_id") else None
                ),
                fact_id=str(entry["fact_id"]) if entry.get("fact_id") else None,
            )
        )
    if bullets:
        return bullets
    document = version.json_resume or {}
    for section in ("work", "projects", "volunteer"):
        for entry in document.get(section) or []:
            if not isinstance(entry, dict):
                continue
            for highlight in entry.get("highlights") or []:
                if isinstance(highlight, str) and highlight.strip():
                    bullets.append(ResumeBullet(section=section, text=highlight.strip()))
    return bullets
