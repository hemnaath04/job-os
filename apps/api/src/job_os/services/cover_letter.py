"""Cover-letter agent.

Same contract as the tailoring pipeline, applied to prose: every specific claim
in the finished letter traces to a verified `fact_bullets` row, and Python
enforces that at assembly time rather than asking the model to behave. The model
returns sentences with attributions; Python decides which of them may print.

The rule that does the work is small enough to state in one line: a sentence may
say something specific only if it names the bullet that proves it. So there are
exactly two kinds of sentence, and both are checked.

  1. An ATTRIBUTED sentence carries a `fact_bullet_id`. It is checked against
     that bullet the way `tailor._sanitize_selected_bullets` checks a rewritten
     bullet: no metric, technology, status upgrade or sole-credit claim the
     evidence does not already carry.
  2. An UNATTRIBUTED sentence carries none, so it must make no claim at all. No
     number, no technology name, no past-tense claim verb. This is the half a
     resume pipeline never needs, because a resume is nothing but attributed
     bullets, while a letter is mostly connective prose and that prose is exactly
     where an invented sentence would hide.

Anything that fails is deleted and reported as a `RefusedSentence`, never
rewritten into something weaker and printed anyway. A requirement the job asks
for that the vault cannot support becomes a `GapQuestion`, and those are derived
by Python from the same word-matching the tailor scores with, so a gap is proved
rather than volunteered.

The flow is compose, measure, repair at most once:
  1. Python derives the requirement rubric from the JD and word-matches it
     against the whole vault. Free, and it is what the letter is briefed on.
  2. One writing pass composes the letter with that rubric in hand.
  3. Python assembles, refuses what it cannot prove, and measures the result.
  4. A repair pass runs only when there is something a repair could fix: a
     refused sentence, or a measured writing problem. The better of the two
     attempts is kept, by a deterministic comparison.

Deliberately no LangGraph here, unlike `tailor.run_tailor`. That loop branches
and carries state across up to two scored passes with a frozen keyword set; this
one is a two-iteration loop with no branch, and a StateGraph around it would add
a dependency and a layer without buying a decision.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any
from uuid import UUID

import anthropic
import httpx
import structlog
from pydantic import ValidationError

from job_os.schemas.cover_letters import (
    CoverLetterAgentOutput,
    CoverLetterDocument,
    CoverLetterProvenanceEntry,
    CoverLetterResult,
    CoverLetterSender,
    CoverLetterTone,
    LetterSentence,
    RefusedSentence,
)
from job_os.schemas.resumes import GapQuestion
from job_os.services.career_ops_rules import CAREER_OPS_RULES
from job_os.services.llm_json import (
    EMPTY_REPLY_RETRY,
    JSON_ONLY_RETRY,
    create_message,
    parse_model_json,
    response_diagnostics,
    response_text,
)
from job_os.services.resume_writing import drops_team_credit, normalize_dashes, upgrades_status

# Imported from the tailoring module rather than duplicated. Several of these are
# private names, which is deliberate and worth explaining: they are the exact
# functions that derive and score the requirement rubric, and a letter that
# briefed itself off a second, slightly different implementation of the same idea
# would report gaps the resume pipeline does not. `tailor.py` is owned by another
# branch right now, so making them public there is not this change's to make.
from job_os.services.tailor import (
    NUMBER_RE,
    TailorBullet,
    TailorFact,
    TailorStage,
    _build_facts_payload,
    _evidence_items,
    _jd_requirements,
    _mentions,
    _merge_duplicate_facts,
    _requirement_coverage,
    _technology_terms,
)
from job_os.settings import get_settings

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from job_os.db.models import Job, ResumeVersion, User

log = structlog.get_logger(__name__)

# Words the finished letter should land between. Under the floor it reads as a
# form letter and says nothing an employer could not read off the resume; over
# the ceiling it stops being a page. Both numbers are the conventional band for
# the format rather than anything measured here.
MIN_WORDS = 250
TARGET_MAX_WORDS = 350
# The cap Python guarantees, as opposed to the one it asks for. A letter over
# this loses its last body paragraph, because a cover letter that runs to a
# second page is not a longer letter, it is one nobody finishes. Set above the
# soft ceiling so an ordinary overshoot is repaired rather than truncated.
HARD_MAX_WORDS = 400
# A sentence past this stops being a sentence. Flagged, not deleted: length is a
# writing problem and deleting the claim would be a worse outcome than a long
# sentence.
MAX_SENTENCE_WORDS = 35
# Below this the letter is not making a case, it is making noise. Two evidenced
# claims is the floor for something worth sending.
MIN_EVIDENCED_CLAIMS = 2
# Opening plus three body paragraphs plus a closing. More than this on one page
# means paragraphs of one sentence each, which reads as a list.
MAX_PARAGRAPHS = 5

# One compose pass and at most one repair, the same budget `tailor` settled on
# after measuring that a third pass bought nothing.
MAX_COMPOSE_PASSES = 2

# The letter is a page of prose and the schema around it is small, so the output
# is nowhere near the resume agent's. The budget still has to hold an extended
# thinking block in front of the answer, which is why it is not a few thousand.
DRAFT_MAX_TOKENS = 16000
RETRY_MAX_TOKENS = 24000

# Gap questions shown at once. Past this it stops being a list of things to fix
# and becomes a verdict on the application.
MAX_GAP_QUESTIONS = 6

# Wording that makes a letter read as machine-written, which is the failure mode
# users name when they call a generated letter cheap. Three groups: the resume
# ban list, the enthusiasm tells, and the self-description clichés. Stems rather
# than exact words, so "utilizing" and "excitement" cannot slip through the way
# they would past a fixed list.
_BANNED_LETTER_WORDING = re.compile(
    r"(?<!\w)(?:"
    # From the career-ops ban list, which applies to every document.
    r"leverag\w*|utiliz\w*|spearhead\w*|synergiz\w*|synergy|revolutioniz\w*|"
    r"facilitat\w*|enabl\w*|streamlin\w*|orchestrat\w*|empower\w*|foster\w*|"
    r"cutting[- ]edge|state[- ]of[- ]the[- ]art|innovat\w*|robust|holistic|"
    r"seamless\w*|end[- ]to[- ]end|comprehensive|sophisticated|meticulous|"
    r"pivotal|delve\w*|showcas\w*|underscor\w*|"
    # The enthusiasm tells. None of them is a fact, and a letter that opens on
    # one has spent its first sentence saying nothing.
    r"thrill\w*|excit\w*|passionat\w*|passion|delighted|honou?red|eager|"
    r"esteemed|prestigious|world[- ]class|renowned|"
    # Self-description with no evidence behind it.
    r"results[- ]driven|team player|self[- ]starter|go[- ]getter|"
    r"detail[- ]oriented|hit the ground running|perfect (?:fit|candidate)|"
    r"dream job|ideal candidate|fast[- ]paced|dynamic environment|"
    r"proven track record|wealth of experience"
    r")(?!\w)",
    re.I,
)

# First person plural in a letter about one person's own work. Either it is
# taking a team's credit or it is guessing at the employer's internals, and the
# honest form for shared work is "with a team", which `drops_team_credit`
# already recognises. "us" is excluded on purpose: it is a market ("US markets")
# far more often than it is a pronoun here.
_FIRST_PERSON_PLURAL = re.compile(r"(?<!\w)(?:we|we'\w+|our|ours)(?!\w)", re.I)

# Past-tense verbs that assert the candidate did something. An unattributed
# sentence containing one is making a claim with nothing behind it, whatever else
# it says. Present and future tense are absent on purpose: "I want to build
# systems like yours" is a statement of intent, not of history, and a letter is
# allowed to say it.
_CLAIM_VERB = re.compile(
    r"(?<!\w)(?:"
    r"built|wrote|written|designed|shipped|launched|released|delivered|"
    r"led|owned|developed|implemented|automated|migrated|tested|trained|"
    r"deployed|maintained|refactored|hardened|debugged|scored|measured|"
    r"used|scaled|integrated|reduced|improved|cut|"
    r"work(?:ed|ing) on|"
    r"experience (?:with|in|building|writing)|"
    r"years? of"
    r")(?!\w)",
    re.I,
)

# An exclamation mark in a cover letter is never the right punctuation and is
# never worth refusing a whole sentence over, so it is quietly replaced the same
# way an em dash is.
_EXCLAMATION = re.compile(r"!+")

_SENTENCE_SPLIT = re.compile(r"(?<=[.?])\s+")


TONE_GUIDANCE: dict[str, str] = {
    "plain": (
        "TONE: plain. Flat, factual, first person. State what was built and what "
        "it cost. No warmth claims, no adjectives about the company, no scene "
        "setting. This is the default because it is the hardest tone to fake and "
        "the easiest to read."
    ),
    "warm": (
        "TONE: warm. The same facts as plain, one degree less clipped, so a "
        "sentence may say what drew this person to the work. It may not say how "
        "they feel about the company, and it earns no adjectives: warmth here is "
        "sentence rhythm, not vocabulary."
    ),
    "direct": (
        "TONE: direct. Shortest letter that still makes the case. Open on the "
        "strongest verified claim rather than on the role, cut every connective "
        "sentence that is not load bearing, and close in one line. Aim at the "
        "lower end of the word range."
    ),
}


SYSTEM_PROMPT = """\
You write one cover letter. You receive (a) a parsed job description, (b) the
candidate's verified facts and bullets, (c) the requirement rubric Python derived
from that job description, and (d) the tone to write in.

You do NOT write the contact block, the date, the greeting, the subject line or
the sign-off. Python writes those from the candidate's own resume, so leave them
out entirely. You write body prose and nothing else.

HOW THIS OUTPUT IS READ, because it decides how you should write. You return
sentences, and every sentence that says something specific about what this
candidate has done must carry the `fact_bullet_id` of the verified bullet that
proves it. Python checks each sentence against that bullet and DELETES any
sentence it cannot prove. A deleted sentence does not come back as a weaker
sentence, it comes back as nothing, so a claim that reaches slightly past the
evidence costs the letter the whole paragraph it was in.

HARD CONSTRAINTS, these are non-negotiable:
1. Every `fact_bullet_id` MUST appear in the candidate's bullets list. Never
   invent an id, and never attribute a sentence to a bullet that does not
   describe the work the sentence claims.
2. An attributed sentence may reword its bullet freely. It MUST NOT introduce a
   metric, a number, a technology, a client, a scale or an outcome that is not
   already in that bullet or its parent fact. If the job asks for something the
   evidence does not hold, it belongs in `gap_questions`, not in a sentence.
3. A sentence with `fact_bullet_id: null` may make NO claim about the candidate's
   work: no numbers, no technology names, no past-tense claim verbs (built,
   wrote, shipped, led, designed, worked on, used, years of). Python refuses
   those outright. Use null for the sentence that names the role, for a
   connective sentence, and for the closing.
4. Never upgrade a fact's status. Work the evidence records as demoed, pending
   approval, a prototype, a hackathon build, a trial or a mock is never
   described as shipped, launched, released, delivered or in production. Carry
   the qualifier into the sentence: "demoed end to end and pending approval when
   I left" is a stronger line than a claim an interviewer punctures in one
   question.
5. Where the evidence credits a TEAM, the sentence keeps the team. "I worked with
   a team on an agent that drafts test cases" is honest. "I built an agent that
   drafts test cases" is a bigger claim than the evidence supports, and Python
   deletes it.
6. First person singular throughout: I, my, me. Never "we" or "our". In a letter
   about one person's work, "we built" either takes a team's credit or guesses at
   the employer's internals.
7. Do not name a person. You are not told who reads this and you must not guess
   at a hiring manager, a team lead or a recruiter.

WHAT MAKES A COVER LETTER READ AS CHEAP. This is the specific failure to avoid,
and every item is a habit rather than an accident:
- Opening on enthusiasm instead of information: "I am writing to express my
  strong interest in this opportunity at your company."
- Restating the job description back at the employer, who wrote it.
- Claiming a feeling no fact supports: passion for the mission, admiration for
  the culture, excitement about the industry.
- Adjectives where a decision or a number belongs.
- Three-item lists of qualities: "collaborative, detail-oriented and
  results-driven".
- Explaining what a technology is, or why the employer's own problem is hard.
Write what was built, what constrained it, and what the constraint cost. One
concrete decision is worth a paragraph of adjectives, and it is the only thing in
the letter an interviewer can ask a real question about.

STRUCTURE, four paragraphs at the outside:
- `opening`: one or two sentences. Which role, and the single strongest reason
  this candidate is a real match, stated as a fact. Attribute the reason sentence
  if it names work.
- `body`: two or three paragraphs. Each one takes ONE requirement the posting
  actually named and answers it with ONE piece of verified evidence, led by the
  evidence rather than by the requirement. A paragraph is two or three sentences:
  the claim, then what made it hard or what it measured.
- `closing`: one or two sentences. What the candidate is asking for, plainly. No
  gratitude for the reader's time, no "at your earliest convenience", no
  restating the opening.

LENGTH: 250 to 350 words across every sentence you return. Python counts them.
Under 250 reads as a form letter; over 400 Python deletes your last body
paragraph to keep the letter on one page. Keep sentences under 35 words.

WORDING:
- Banned words, no exceptions: leveraged, utilized, spearheaded, cutting-edge,
  state-of-the-art, innovative, robust, seamlessly, synergized, revolutionized,
  facilitated, enabled, end-to-end, comprehensive, thrilled, excited, passionate,
  esteemed, results-driven, team player, detail-oriented, fast-paced. Python
  deletes any sentence containing one, so a banned word costs the whole claim.
  Say the plain verb: built, wrote, tested, migrated, measured, fixed.
- No em dashes, en dashes or double hyphens anywhere. Commas, colons and periods
  only. This is a hard rule of this product, not a preference.
- No exclamation marks. No rhetorical questions.
- Name the company at most twice, and never in a compliment.
- Do not mention salary, visa status, notice period or availability unless the
  job description asks for it.

GAP QUESTIONS. Python has already word-matched every requirement in the posting
against the whole vault and will add the ones it proved absent, so you do not
need to list those. Add a `gap_question` only for something a word match would
miss, with `why_no_match` as a short phrase and not a sentence. At most three.

`agent_note`: one sentence, under 25 words, orienting the user. Not a recap of
your reasoning.

Output: a single JSON object matching the provided schema. No prose, no fences.
"""


# ---------------------------------------------------------------------------
# Sentence-level enforcement
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _KeptSentence:
    """A sentence that survived every check, and what backs it."""

    text: str
    fact_bullet_id: str | None
    fact_id: str | None
    words: int


def _clean(text: str) -> str:
    """Punctuation the rules forbid, fixed rather than refused.

    A dash and an exclamation mark are both cosmetic: the claim underneath is
    unaffected, so deleting the sentence over one would throw away evidence to
    enforce a style rule. Normalising happens before every other check, so a
    refusal reason can never be about a character this would have removed.
    """
    cleaned = normalize_dashes(text, separator=", ") or ""
    cleaned = _EXCLAMATION.sub(".", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def _unattributed_claim(text: str, *, allowed: str) -> str | None:
    """Why an unattributed sentence is making a claim, or None if it is not.

    `allowed` is the role title and company name from the posting. A number or a
    technology inside those is the employer's own wording, not a claim about the
    candidate: an opening line naming a "Python Backend Engineer, Tier 2" role
    must not be refused for the word Python or the digit 2.
    """
    allowed_numbers = set(NUMBER_RE.findall(allowed))
    numbers = sorted(set(NUMBER_RE.findall(text)) - allowed_numbers)
    if numbers:
        return f"unattributed_number({','.join(numbers)})"
    technologies = sorted(_technology_terms(text) - _technology_terms(allowed))
    if technologies:
        return f"unattributed_technology({','.join(technologies)})"
    verb = _CLAIM_VERB.search(text)
    if verb:
        return f"unattributed_claim({verb.group(0).casefold()})"
    return None


def _sentence_verdict(
    sentence: LetterSentence,
    *,
    bullets_by_id: dict[str, TailorBullet],
    facts_by_id: dict[str, TailorFact],
    allowed: str,
) -> tuple[_KeptSentence | None, RefusedSentence | None]:
    """Whether one sentence may print, and if not, exactly why.

    The two branches are the whole contract. An attributed sentence is measured
    against its bullet; an unattributed one is measured against the rule that it
    may claim nothing. Everything a letter can say falls into one of those, which
    is what closes the hole a prose generator would otherwise leave open.
    """
    text = _clean(sentence.text)
    if not text:
        return None, None

    banned = _BANNED_LETTER_WORDING.search(text)
    if banned:
        return None, RefusedSentence(
            text=text,
            reason=f"banned_wording({banned.group(0).casefold()})",
            fact_bullet_id=sentence.fact_bullet_id,
        )
    plural = _FIRST_PERSON_PLURAL.search(text)
    if plural:
        return None, RefusedSentence(
            text=text,
            reason=f"first_person_plural({plural.group(0).casefold()})",
            fact_bullet_id=sentence.fact_bullet_id,
        )

    if sentence.fact_bullet_id is None:
        reason = _unattributed_claim(text, allowed=allowed)
        if reason:
            return None, RefusedSentence(text=text, reason=reason)
        kept = _KeptSentence(
            text=text, fact_bullet_id=None, fact_id=None, words=len(text.split())
        )
        return kept, None

    source = bullets_by_id.get(sentence.fact_bullet_id)
    fact = facts_by_id.get(source.fact_id) if source else None
    if source is None or fact is None:
        # Either the model invented the id, or the bullet's parent fact is gone or
        # unverified. Both mean the same thing: nothing here proves the sentence.
        return None, RefusedSentence(
            text=text,
            reason="unknown_fact_bullet_id",
            fact_bullet_id=sentence.fact_bullet_id,
        )

    # The parent fact's payload counts as evidence alongside the bullet, exactly
    # as it does for a tailored bullet: a project's technology list is verified
    # content and a sentence naming one of those technologies invents nothing.
    context = source.text + "\n" + json.dumps(fact.payload or {}, ensure_ascii=False)
    added_numbers = sorted(set(NUMBER_RE.findall(text)) - set(NUMBER_RE.findall(context)))
    if added_numbers:
        return None, RefusedSentence(
            text=text,
            reason=f"unverified_number({','.join(added_numbers)})",
            fact_bullet_id=source.id,
        )
    added_technologies = sorted(_technology_terms(text) - _technology_terms(context))
    if added_technologies:
        return None, RefusedSentence(
            text=text,
            reason=f"unverified_technology({','.join(added_technologies)})",
            fact_bullet_id=source.id,
        )
    if upgrades_status(text, context):
        return None, RefusedSentence(
            text=text, reason="upgraded_status", fact_bullet_id=source.id
        )
    if drops_team_credit(text, source.text):
        return None, RefusedSentence(
            text=text, reason="dropped_team_credit", fact_bullet_id=source.id
        )
    return (
        _KeptSentence(
            text=text,
            fact_bullet_id=source.id,
            fact_id=source.fact_id,
            words=len(text.split()),
        ),
        None,
    )


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def _sender(master_json_resume: dict[str, Any]) -> CoverLetterSender:
    """The contact block, read only from the master resume's basics."""
    basics = (master_json_resume or {}).get("basics") or {}
    location = basics.get("location") or {}
    where = ", ".join(
        str(part).strip()
        for part in (location.get("city"), location.get("region"))
        if str(part or "").strip()
    )
    links = [
        str(profile.get("url")).strip()
        for profile in (basics.get("profiles") or [])
        if str(profile.get("url") or "").strip()
    ]
    if str(basics.get("url") or "").strip():
        links.insert(0, str(basics["url"]).strip())
    return CoverLetterSender(
        name=str(basics.get("name") or "").strip(),
        email=str(basics.get("email") or "").strip(),
        phone=str(basics.get("phone") or "").strip(),
        location=where,
        links=links[:3],
    )


def _greeting(recipient_name: str | None) -> str:
    """Who the letter is addressed to, decided by Python and never by the model.

    A hiring manager's name is the same class of fact as a phone number: the
    letter may use one the user supplied and must never produce one otherwise.
    "Dear Hiring Team" is the honest default and reads better than the alternative
    convention, which is a guess with a comma after it.
    """
    name = (recipient_name or "").strip()
    return f"Dear {name}," if name else "Dear Hiring Team,"


def assemble_letter(
    agent: CoverLetterAgentOutput,
    *,
    facts: list[TailorFact],
    bullets_by_fact: dict[str, list[TailorBullet]],
    master_json_resume: dict[str, Any],
    company: str,
    role: str,
    tone: CoverLetterTone = "plain",
    recipient_name: str | None = None,
    today: date | None = None,
    gap_questions: list[GapQuestion] | None = None,
    agent_note: str = "",
) -> CoverLetterResult:
    """Turn one agent pass into the letter it would actually send.

    Every safety check lives here, so the letter measured mid-run is the letter
    the user gets. `tailor._build_document` makes the same argument for the same
    reason: scoring a friendlier object than the one that ships is how a loop
    comes to optimise a number nobody outside it can see.
    """
    bullets_by_id = {b.id: b for bs in bullets_by_fact.values() for b in bs}
    facts_by_id = {f.id: f for f in facts}
    allowed = f"{role} {company}"

    refused: list[RefusedSentence] = []
    # Tagged by section so the word cap can only ever trim the body. Deleting a
    # closing to fit would leave the letter ending mid-argument.
    sections: list[tuple[str, list[_KeptSentence]]] = []
    for label, paragraph in (
        ("opening", agent.opening),
        *(("body", paragraph) for paragraph in agent.body),
        ("closing", agent.closing),
    ):
        kept: list[_KeptSentence] = []
        for sentence in paragraph.sentences:
            verdict, refusal = _sentence_verdict(
                sentence,
                bullets_by_id=bullets_by_id,
                facts_by_id=facts_by_id,
                allowed=allowed,
            )
            if refusal is not None:
                refused.append(refusal)
            if verdict is not None:
                kept.append(verdict)
        if kept:
            sections.append((label, kept))

    trimmed = 0
    while _word_count(sections) > HARD_MAX_WORDS:
        # Last body paragraph first, and never the opening or the closing. The
        # last paragraph is the weakest by construction: the prompt asks for the
        # strongest requirement first.
        body_indexes = [i for i, (label, _) in enumerate(sections) if label == "body"]
        if not body_indexes:
            break
        sections.pop(body_indexes[-1])
        trimmed += 1

    paragraphs, provenance = _print(sections)
    words = _word_count(sections)
    document = CoverLetterDocument(
        sender=_sender(master_json_resume),
        date=(today or date.today()).strftime("%d %B %Y"),
        company=company,
        role=role,
        recipient_name=(recipient_name or "").strip(),
        greeting=_greeting(recipient_name),
        subject=_subject(role, company),
        paragraphs=paragraphs,
        signoff="Sincerely,",
        word_count=words,
    )
    return CoverLetterResult(
        document=document,
        provenance=provenance,
        gap_questions=(gap_questions or [])[:MAX_GAP_QUESTIONS],
        refused=refused,
        quality_flags=letter_quality_flags(
            document, provenance=provenance, sections=sections, trimmed=trimmed
        ),
        tone=tone,
        agent_note=_clean(agent_note or agent.agent_note),
    )


def _subject(role: str, company: str) -> str:
    """The Re: line. Plain, and it names the role rather than selling it."""
    role = (role or "").strip()
    company = (company or "").strip()
    if role and company:
        return f"Application for {role} at {company}"
    return f"Application for {role or company}".strip()


def _word_count(sections: list[tuple[str, list[_KeptSentence]]]) -> int:
    return sum(sentence.words for _label, kept in sections for sentence in kept)


def _print(
    sections: list[tuple[str, list[_KeptSentence]]],
) -> tuple[list[str], list[CoverLetterProvenanceEntry]]:
    """Join the surviving sentences into paragraphs and index them.

    The one place a provenance row is minted, which is the point: the row's
    `paragraph` and `sentence` are the position of that sentence in the printed
    letter, so they can only be right if they are counted while the letter is
    being printed. Both callers, generation and a hand edit, go through here, so
    neither can produce a document whose rows point somewhere else.

    A sentence with no `fact_bullet_id` gets no row and needs none: it was already
    proved to claim nothing, so there is nothing for a row to attest.
    """
    paragraphs: list[str] = []
    provenance: list[CoverLetterProvenanceEntry] = []
    for paragraph_index, (_label, kept) in enumerate(sections):
        paragraphs.append(" ".join(kept_sentence.text for kept_sentence in kept))
        for sentence_index, kept_sentence in enumerate(kept):
            if kept_sentence.fact_bullet_id is None or kept_sentence.fact_id is None:
                continue
            provenance.append(
                CoverLetterProvenanceEntry(
                    paragraph=paragraph_index,
                    sentence=sentence_index,
                    text=kept_sentence.text,
                    fact_bullet_id=kept_sentence.fact_bullet_id,
                    fact_id=kept_sentence.fact_id,
                )
            )
    return paragraphs, provenance


def letter_quality_flags(
    document: CoverLetterDocument,
    *,
    provenance: list[CoverLetterProvenanceEntry],
    sections: list[tuple[str, list[_KeptSentence]]] | None = None,
    trimmed: int = 0,
) -> dict[str, list[str]]:
    """Writing problems in an assembled letter, named so a model can fix them.

    Keyed by where the problem lives, the same shape
    `resume_writing.document_quality_flags` returns, so one UI renders both.
    Nothing here is an honesty failure: those are already gone by this point, and
    they are reported as refusals rather than as flags.
    """
    flags: dict[str, list[str]] = {}
    words = document.word_count
    if words < MIN_WORDS:
        flags.setdefault("length", []).append(f"thin_letter({words}w)")
    elif words > TARGET_MAX_WORDS:
        flags.setdefault("length", []).append(f"long_letter({words}w)")
    if trimmed:
        flags.setdefault("length", []).append(f"trimmed_paragraphs({trimmed})")
    if len(provenance) < MIN_EVIDENCED_CLAIMS:
        flags.setdefault("evidence", []).append(f"too_few_claims({len(provenance)})")
    if len(document.paragraphs) > MAX_PARAGRAPHS:
        flags.setdefault("structure", []).append(
            f"too_many_paragraphs({len(document.paragraphs)})"
        )
    if sections is not None and not any(label == "opening" for label, _ in sections):
        flags.setdefault("structure", []).append("no_opening")
    if sections is not None and not any(label == "closing" for label, _ in sections):
        flags.setdefault("structure", []).append("no_closing")
    for index, paragraph in enumerate(document.paragraphs):
        long_sentences = [
            part
            for part in _SENTENCE_SPLIT.split(paragraph)
            if len(part.split()) > MAX_SENTENCE_WORDS
        ]
        if long_sentences:
            flags.setdefault(f"paragraph {index + 1}", []).append(
                f"long_sentence({len(long_sentences[0].split())}w)"
            )
    return flags


# ---------------------------------------------------------------------------
# Gap questions
# ---------------------------------------------------------------------------


def derive_gap_questions(
    *,
    jd_parsed: dict[str, Any],
    facts: list[TailorFact],
    bullets_by_fact: dict[str, list[TailorBullet]],
    model_gaps: list[GapQuestion],
) -> list[GapQuestion]:
    """Requirements this posting names that the vault cannot support.

    Python-derived, from the same word matching the resume pipeline scores with,
    so a gap here is proved rather than volunteered. The model's own gaps are
    kept only where the vault genuinely does not answer them: left unchecked, a
    model that skimmed the evidence reports a gap the candidate does not have and
    the user is asked to fill something already filled.
    """
    requirements, _prose, _excluded = _jd_requirements(jd_parsed)
    evidence = _evidence_items(facts, bullets_by_fact)
    coverage = _requirement_coverage(requirements, evidence)
    haystack = " ".join(item.text for item in evidence).casefold()

    model_by_label = {
        (gap.requirement or "").strip().casefold(): gap for gap in model_gaps
    }
    gaps: list[GapQuestion] = []
    seen: set[str] = set()
    for requirement in requirements:
        if requirement.preferred or coverage[requirement.label].found:
            continue
        label = requirement.label
        key = label.casefold()
        if key in seen:
            continue
        seen.add(key)
        volunteered = model_by_label.get(key)
        gaps.append(
            GapQuestion(
                requirement=label,
                why_no_match=(
                    volunteered.why_no_match
                    if volunteered and volunteered.why_no_match.strip()
                    else "not worded anywhere in your verified profile"
                ),
            )
        )
    for gap in model_gaps:
        key = (gap.requirement or "").strip().casefold()
        if not key or key in seen:
            continue
        if _mentions(haystack, gap.requirement):
            log.info("cover_letter.model_gap_already_covered", requirement=gap.requirement)
            continue
        seen.add(key)
        gaps.append(gap)
    return gaps[:MAX_GAP_QUESTIONS]


# ---------------------------------------------------------------------------
# Prompting
# ---------------------------------------------------------------------------


def _briefing(
    *,
    jd_parsed: dict[str, Any],
    facts: list[TailorFact],
    bullets_by_fact: dict[str, list[TailorBullet]],
) -> str:
    """What the posting asks for, split by whether the vault answers it.

    The resume pipeline hands its writer the same list for the same reason: a
    model that has to guess which requirements the evidence covers spends its
    pass rediscovering something Python settled in under a millisecond.
    """
    requirements, _prose, _excluded = _jd_requirements(jd_parsed)
    coverage = _requirement_coverage(
        requirements, _evidence_items(facts, bullets_by_fact)
    )
    must = [req for req in requirements if not req.preferred]
    backed = [req.label for req in must if coverage[req.label].found]
    absent = [req.label for req in must if not coverage[req.label].found]
    lines = [
        "WHAT THIS POSTING ASKS FOR. Python word-matched every requirement "
        "against the whole verified vault before you were called, so this is "
        "measured rather than guessed.",
        "",
        f"REQUIREMENTS YOUR EVIDENCE ANSWERS ({len(backed)}). These are what the "
        "letter is for. Pick the two or three that matter most to this posting "
        f"and answer each with one verified bullet: {', '.join(backed) or 'none'}",
    ]
    if absent:
        lines += [
            "",
            "REQUIREMENTS THE VAULT DOES NOT HOLD. Python confirmed the words "
            "appear nowhere in any verified fact or bullet. Do not write a "
            "sentence about any of these, do not gesture at them, and do not "
            "list them in gap_questions because Python already has: "
            f"{', '.join(absent)}",
        ]
    lines += [
        "",
        "A requirement you cannot answer from a real bullet is not a sentence to "
        "soften. It is a gap, and the user would rather read a shorter letter "
        "than a claim they have to defend in an interview.",
    ]
    return "\n".join(lines)


def _build_user_prompt(
    *,
    jd_parsed: dict[str, Any],
    jd_clean: str,
    facts_payload: list[dict[str, Any]],
    briefing: str,
    tone: CoverLetterTone,
    company: str,
    role: str,
) -> str:
    return (
        f"ROLE: {role or 'unknown'}\nCOMPANY: {company or 'unknown'}\n\n"
        "JOB DESCRIPTION (parsed):\n"
        f"{json.dumps(jd_parsed or {}, indent=2)}\n\n"
        "JOB DESCRIPTION (clean text, truncated):\n"
        f"<jd>\n{(jd_clean or '')[:6000]}\n</jd>\n\n"
        "CANDIDATE VERIFIED FACTS + BULLETS. Every fact_bullet_id you may cite "
        "is in here and nowhere else:\n"
        f"{json.dumps(facts_payload, indent=2)[:12000]}\n\n"
        f"{briefing}\n\n"
        f"{TONE_GUIDANCE.get(tone, TONE_GUIDANCE['plain'])}\n\n"
        "Respond with a single JSON object matching this schema (no prose, no "
        "fences):\n"
        f"{json.dumps(CoverLetterAgentOutput.model_json_schema())}"
    )


def _repair_prompt(result: CoverLetterResult) -> str:
    """Feedback turn after a pass Python had to cut into.

    Two kinds of feedback, because there are two ways for a pass to fall short.
    A refusal is a claim that was deleted, which is the expensive one. A flag is a
    writing problem a reader would hold against the letter. Both are measured
    from the assembled letter rather than from the model's account of it.
    """
    lines: list[str] = []
    if result.refused:
        lines += [
            f"Python deleted {len(result.refused)} of your sentences. Each one "
            "claimed something the verified evidence does not carry, so the "
            "letter went out without it. Rewrite each claim so it stays inside "
            "its bullet, or drop it and use a different bullet:",
        ]
        for refusal in result.refused[:8]:
            lines.append(f'  - {refusal.reason}: "{refusal.text[:180]}"')
        lines += [
            "",
            "How to read those reasons. unverified_number and "
            "unverified_technology mean the sentence named something its bullet "
            "does not, so cite a bullet that does or cut the detail. "
            "unattributed_number, unattributed_technology and unattributed_claim "
            "mean a sentence with a null fact_bullet_id made a claim anyway, so "
            "either attribute it to the bullet that proves it or make the "
            "sentence claim nothing. upgraded_status means you said work shipped "
            "that the evidence records as provisional. dropped_team_credit means "
            "the evidence says a team did the work and your sentence took sole "
            "credit, so keep the team. banned_wording and first_person_plural are "
            "wording rules and the sentence is deleted whole, so fix the word. "
            "unknown_fact_bullet_id means the id is not in the list you were "
            "given.",
        ]
    if result.quality_flags:
        lines += ["", "Writing problems Python measured on the assembled letter:"]
        for where, flags in result.quality_flags.items():
            lines.append(f"  - {where}: {', '.join(flags)}")
        lines += [
            "",
            f"thin_letter means the letter is under {MIN_WORDS} words and reads "
            f"as a form letter. long_letter means it is over {TARGET_MAX_WORDS}. "
            "trimmed_paragraphs means it ran past the hard cap and Python deleted "
            "your last body paragraph to keep it on one page. too_few_claims "
            "means fewer than two sentences are backed by a verified bullet, "
            "which is a letter that says nothing the resume does not. "
            "long_sentence means cut it under 35 words.",
        ]
    lines += [
        "",
        "Return the FULL letter JSON again, not a diff. Same schema as before.",
    ]
    return "\n".join(lines)


def _attempt_rank(result: CoverLetterResult) -> tuple[int, int, int]:
    """Comparison key for two passes, lowest wins.

    Refusals first, because a refused sentence is evidence the letter lost.
    Then measured writing problems. Then distance from the middle of the word
    band, which breaks a tie in favour of the letter closest to the length the
    format wants. Deterministic on purpose: the model does not get to tell us
    which of its own attempts was better.
    """
    midpoint = (MIN_WORDS + TARGET_MAX_WORDS) // 2
    return (
        len(result.refused),
        sum(len(flags) for flags in result.quality_flags.values()),
        abs(result.document.word_count - midpoint),
    )


# ---------------------------------------------------------------------------
# The agent
# ---------------------------------------------------------------------------


async def run_cover_letter(
    *,
    facts: list[TailorFact],
    bullets_by_fact: dict[str, list[TailorBullet]],
    master_json_resume: dict[str, Any],
    jd_parsed: dict[str, Any],
    jd_clean: str,
    company: str = "",
    role: str = "",
    tone: CoverLetterTone = "plain",
    recipient_name: str | None = None,
    today: date | None = None,
    on_progress: Callable[[TailorStage], None] | None = None,
) -> CoverLetterResult:
    """Backend-agnostic cover-letter agent.

    No DB access, for the same reason `tailor.run_tailor` has none: the FastAPI
    Postgres backend and the Appwrite Function can then share one implementation
    of the contract rather than each holding its own.

    `facts` must already be filtered to verified facts. An unverified fact is an
    agent-proposed draft the user has not confirmed, and this function has no way
    to tell one from the other, so the loaders do it. See `_load_verified_facts`
    in the adapter below.
    """
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set; cannot run the cover-letter agent.")

    def report(step: str, label: str, detail: str | None, pct: float) -> None:
        if on_progress:
            on_progress(TailorStage(step=step, label=label, detail=detail, pct=pct))

    facts, bullets_by_fact = _merge_duplicate_facts(facts, bullets_by_fact)
    facts_payload = _build_facts_payload(facts, bullets_by_fact)
    bullet_count = sum(len(bs) for bs in bullets_by_fact.values())
    report(
        "read_role",
        "Reading the role",
        f"Matching against {bullet_count} verified bullets",
        0.10,
    )

    briefing = _briefing(
        jd_parsed=jd_parsed, facts=facts, bullets_by_fact=bullets_by_fact
    )
    client = anthropic.AsyncAnthropic(
        auth_token=settings.anthropic_api_key,
        base_url=settings.anthropic_base_url or None,
    )
    messages: list[anthropic.types.MessageParam] = [
        {
            "role": "user",
            "content": _build_user_prompt(
                jd_parsed=jd_parsed,
                jd_clean=jd_clean,
                facts_payload=facts_payload,
                briefing=briefing,
                tone=tone,
                company=company,
                role=role,
            ),
        }
    ]

    best: CoverLetterResult | None = None
    for iteration in range(1, MAX_COMPOSE_PASSES + 1):
        report(
            "compose" if iteration == 1 else "repair",
            "Writing your letter" if iteration == 1 else "Fixing what could not be proved",
            None
            if iteration == 1
            else _repair_detail(best),
            0.35 if iteration == 1 else 0.70,
        )
        try:
            agent, raw = await _compose(client, messages=messages, settings=settings)
        except (anthropic.APIError, httpx.HTTPError) as exc:
            # A repair is an improvement on something that already works, so a
            # transient gateway failure on one must never throw away the pass
            # that succeeded. Same reasoning as `tailor.compose_and_score`.
            log.warning(
                "cover_letter.pass_failed_keeping_best",
                iteration=iteration,
                error=repr(exc)[:200],
                have_best=best is not None,
            )
            if best is not None:
                break
            raise

        result = assemble_letter(
            agent,
            facts=facts,
            bullets_by_fact=bullets_by_fact,
            master_json_resume=master_json_resume,
            company=company,
            role=role,
            tone=tone,
            recipient_name=recipient_name,
            today=today,
            gap_questions=derive_gap_questions(
                jd_parsed=jd_parsed,
                facts=facts,
                bullets_by_fact=bullets_by_fact,
                model_gaps=agent.gap_questions,
            ),
        )
        result.passes = iteration
        log.info(
            "cover_letter.iteration",
            iteration=iteration,
            words=result.document.word_count,
            claims=len(result.provenance),
            refused=[refusal.reason for refusal in result.refused],
            quality_flags=sorted(
                {flag for flags in result.quality_flags.values() for flag in flags}
            ),
        )
        report(
            "check_claims" if iteration == 1 else "check_repair",
            "Checking every claim is backed",
            f"{len(result.provenance)} claims backed, "
            f"{len(result.refused)} sentences refused",
            0.55 if iteration == 1 else 0.86,
        )
        if best is None or _attempt_rank(result) < _attempt_rank(best):
            best = result

        # Nothing measurable left to fix, so another pass would be a minute of the
        # user's time spent on a coin flip.
        if not result.refused and not result.quality_flags:
            break
        if iteration >= MAX_COMPOSE_PASSES:
            break
        messages = [
            *messages,
            {"role": "assistant", "content": raw[:6000]},
            {"role": "user", "content": _repair_prompt(result)},
        ]

    if best is None:  # pragma: no cover - the loop either sets it or raises
        raise RuntimeError("The cover-letter agent produced no letter.")
    report(
        "done",
        "Letter ready",
        f"{best.document.word_count} words, {len(best.provenance)} backed claims",
        1.0,
    )
    return best


def _repair_detail(best: CoverLetterResult | None) -> str | None:
    """What the repair pass is going after, in the words the user will read."""
    if best is None:
        return None
    parts: list[str] = []
    if best.refused:
        parts.append(f"{len(best.refused)} unproved sentences")
    flags = sum(len(flags) for flags in best.quality_flags.values())
    if flags:
        parts.append(f"{flags} writing problems")
    return "Fixing " + " and ".join(parts) if parts else None


async def _compose(
    client: Any,
    *,
    messages: list[anthropic.types.MessageParam],
    settings: Any,
) -> tuple[CoverLetterAgentOutput, str]:
    """One writing pass, with the one retry a chatty or truncated reply earns."""
    system = f"{CAREER_OPS_RULES}\n\n{SYSTEM_PROMPT}"
    tier = {"x-manifest-tier": settings.manifest_tier_sonnet}
    message = await create_message(
        client,
        model=settings.anthropic_model_tailor,
        max_tokens=DRAFT_MAX_TOKENS,
        system=system,
        messages=messages,
        extra_headers=tier,
    )
    raw = response_text(message)
    try:
        return parse_model_json(CoverLetterAgentOutput, raw), raw
    except ValidationError as error:
        log.warning(
            "cover_letter.invalid_json",
            error=str(error),
            preview=raw[:400],
            **response_diagnostics(message),
        )
        # An empty reply is a different problem from a chatty one: it means the
        # answer ran past the output ceiling, and telling a model that produced
        # nothing "that was not valid JSON" helps nobody.
        empty = not raw.strip()
        retry_messages: list[anthropic.types.MessageParam] = (
            [*messages, {"role": "user", "content": EMPTY_REPLY_RETRY}]
            if empty
            else [
                *messages,
                {"role": "assistant", "content": raw[:4000]},
                {"role": "user", "content": JSON_ONLY_RETRY},
            ]
        )
        retry = await create_message(
            client,
            model=settings.anthropic_model_tailor,
            max_tokens=RETRY_MAX_TOKENS if empty else DRAFT_MAX_TOKENS,
            system=system,
            messages=retry_messages,
            extra_headers=tier,
        )
        retry_raw = response_text(retry)
        try:
            return parse_model_json(CoverLetterAgentOutput, retry_raw), retry_raw
        except ValidationError as retry_error:
            log.warning(
                "cover_letter.invalid_json_after_retry",
                preview=retry_raw[:400],
                **response_diagnostics(retry),
            )
            raise RuntimeError(
                "Cover-letter agent returned an invalid response."
            ) from retry_error


# ---------------------------------------------------------------------------
# Editing an existing letter
# ---------------------------------------------------------------------------


def revalidate_edited_letter(
    document: CoverLetterDocument,
    *,
    paragraphs: list[str],
    provenance: list[CoverLetterProvenanceEntry],
    facts: list[TailorFact],
    bullets_by_fact: dict[str, list[TailorBullet]],
) -> CoverLetterResult:
    """Re-check a letter the user edited by hand, keeping provenance honest.

    A hand edit is the one path that can put text on the page without going
    through the agent, so it is the one path that could otherwise carry an
    unbacked claim under a row that says otherwise. Two rules, and they pull in
    opposite directions on purpose.

    The text is never rejected. It is the user's letter, they may write what they
    like in it, and a tool that deleted a sentence a person typed themselves would
    be wrong about whose document this is.

    A provenance row is rejected the moment it stops being true. A row survives
    only when all three still hold: the sentence text is unchanged, the bullet it
    cited is still in the vault, and that bullet's parent fact is still verified.
    Un-verifying a fact therefore withdraws every row that rested on it, which is
    the behaviour the whole feature is for. Where the text changed, this service
    is in no position to say which bullet the new wording rests on, so it asserts
    nothing rather than asserting the old row about new text.

    Sentences that now claim something nothing backs are reported in `refused`,
    which here means "printed, but do not believe the letter proves this" rather
    than "deleted". Same list, different force, and the difference is spelled out
    in the note the caller stores.
    """
    by_text = {entry.text: entry for entry in provenance}
    sections: list[tuple[str, list[_KeptSentence]]] = []
    refused: list[RefusedSentence] = []
    bullets_by_id = {b.id: b for bs in bullets_by_fact.values() for b in bs}
    # The vault's own facts, so a row cannot outlive the fact under it. `facts`
    # is already filtered to verified rows by the loader, so absence here means
    # exactly one thing: the user withdrew the evidence.
    verified_fact_ids = {fact.id for fact in facts}
    for index, paragraph in enumerate(paragraphs):
        cleaned = _clean(paragraph)
        if not cleaned:
            continue
        kept: list[_KeptSentence] = []
        for part in _SENTENCE_SPLIT.split(cleaned):
            text = part.strip()
            if not text:
                continue
            entry = by_text.get(text)
            bullet = bullets_by_id.get(entry.fact_bullet_id) if entry else None
            if bullet is not None and bullet.fact_id not in verified_fact_ids:
                bullet = None
            if bullet is None:
                # Nothing backs this sentence any more. Say why, once, and only
                # when the sentence is actually claiming something: a connective
                # line the user rewrote never had a row and does not need one.
                reason = (
                    "edited_claim_unverified"
                    if entry is not None
                    else _unattributed_claim(text, allowed="")
                )
                if reason:
                    refused.append(RefusedSentence(text=text, reason=reason))
            kept.append(
                _KeptSentence(
                    text=text,
                    fact_bullet_id=bullet.id if bullet else None,
                    fact_id=bullet.fact_id if bullet else None,
                    words=len(text.split()),
                )
            )
        if not kept:
            continue
        label = "opening" if index == 0 else "closing" if index == len(paragraphs) - 1 else "body"
        sections.append((label, kept))

    new_paragraphs, new_provenance = _print(sections)
    edited = document.model_copy(
        update={
            "paragraphs": new_paragraphs,
            "word_count": _word_count(sections),
        }
    )
    return CoverLetterResult(
        document=edited,
        provenance=new_provenance,
        refused=refused,
        quality_flags=letter_quality_flags(
            edited, provenance=new_provenance, sections=sections
        ),
        agent_note=(
            "Edited by hand. Provenance kept only for unchanged sentences that "
            "still have verified evidence behind them. Anything listed as refused "
            "is still in your letter, it just has nothing backing it."
        ),
    )


# ---------------------------------------------------------------------------
# Postgres adapter
# ---------------------------------------------------------------------------


async def load_verified_vault(
    session: AsyncSession, user_id: UUID
) -> tuple[list[TailorFact], dict[str, list[TailorBullet]]]:
    """Every verified fact and its bullets, as the backend-agnostic dataclasses.

    `verified.is_(True)` is the whole no-fabrication perimeter on this side: an
    unverified fact is a draft the agent proposed through a gap question and the
    user has not confirmed, and it must never reach generated output.
    """
    from sqlalchemy import select

    from job_os.db.models import FactBullet, ProfileFact

    fact_rows = list(
        (
            await session.execute(
                select(ProfileFact)
                .where(ProfileFact.user_id == user_id, ProfileFact.verified.is_(True))
                .order_by(ProfileFact.kind, ProfileFact.start_date.desc().nullslast())
            )
        )
        .scalars()
        .all()
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
            updated_at=row.updated_at.isoformat() if row.updated_at else None,
        )
        for row in fact_rows
    ]
    if not facts:
        return [], {}
    bullet_rows = list(
        (
            await session.execute(
                select(FactBullet)
                .where(FactBullet.fact_id.in_([row.id for row in fact_rows]))
                .order_by(FactBullet.created_at)
            )
        )
        .scalars()
        .all()
    )
    bullets_by_fact: dict[str, list[TailorBullet]] = {}
    for row in bullet_rows:
        bullets_by_fact.setdefault(str(row.fact_id), []).append(
            TailorBullet(
                id=str(row.id),
                fact_id=str(row.fact_id),
                text=row.text,
                target_role=row.target_role,
            )
        )
    return facts, bullets_by_fact


async def generate_cover_letter(
    session: AsyncSession,
    *,
    user: User,
    job: Job,
    master_version: ResumeVersion,
    tone: CoverLetterTone = "plain",
    recipient_name: str | None = None,
) -> CoverLetterResult:
    """Postgres-backed entry point, mirroring `tailor.tailor_resume`.

    Loads the verified vault, adapts it, and delegates to `run_cover_letter`.
    Keeping the agent in that function is what lets the Appwrite Function reuse
    this exact flow with no database.
    """
    facts, bullets_by_fact = await load_verified_vault(session, user.id)
    if not facts:
        raise ValueError(
            "Nothing on your profile is verified yet. A cover letter only says "
            "things you have confirmed you did, so open Profile, tick off the "
            "facts that are true, then write the letter."
        )
    parsed = job.jd_parsed or {}
    company = str(parsed.get("company") or "").strip()
    if not company and job.company is not None:
        company = str(job.company.name or "").strip()
    return await run_cover_letter(
        facts=facts,
        bullets_by_fact=bullets_by_fact,
        master_json_resume=master_version.json_resume,
        jd_parsed=parsed,
        jd_clean=job.jd_clean or "",
        company=company,
        role=str(parsed.get("title") or job.title or "").strip(),
        tone=tone,
        recipient_name=recipient_name,
    )
