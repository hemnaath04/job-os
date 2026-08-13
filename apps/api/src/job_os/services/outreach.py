"""Referral and networking outreach drafting.

Given a job, one target person, and the user's verified fact vault, write a short
message that person might actually answer. Same contract as the tailoring
pipeline: the model chooses what to say from evidence it is handed, Python
assembles and grades the result, and every claim that reaches the message carries
a provenance row pointing at the verified row it came from.

The failure this module is built around is not a bad sentence. It is a fabricated
shared connection. "I saw we both worked at Stripe" to someone who can check in
one click is unrecoverable socially, and it is the exact thing a competitor was
caught doing. So the shared-context ledger is computed by Python BEFORE the model
runs, from the intersection of what the user asserts about this person and what
the user's own verified vault holds, and the finished body is then scanned for the
SHAPE of a common-ground claim whether or not the model cited anything. A claim
with no ledger entry behind it never ships, and the model is not asked nicely to
avoid it.

Four guards do the work, all of them in Python rather than in the prompt:
  1. Only verified facts enter the payload. `verified=False` rows are
     agent-proposed drafts and the loader filters them out.
  2. The shared-context ledger is an allowlist with ids. Cited ids are
     intersected with it and unknown ids are dropped.
  3. `unbacked_shared_claims` regex-scans the assembled body for common-ground
     phrasing and requires a ledger entry per hit.
  4. Numbers, technologies and completion verbs in the body must appear in the
     evidence that was actually cited.
"""
from __future__ import annotations

import enum
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any

import anthropic
import structlog
from pydantic import BaseModel, Field, ValidationError

from job_os.services.career_ops_rules import CAREER_OPS_RULES, UNPRINTABLE_SKILLS
from job_os.services.identity import identity_text
from job_os.services.llm_json import (
    JSON_ONLY_RETRY,
    create_message,
    parse_model_json,
    response_diagnostics,
    response_text,
)
from job_os.services.resume_writing import (
    BANNED_WORDING,
    has_banned_separator,
    mentions_word,
    normalize_dashes,
    records_provisional_status,
    upgrades_status,
)
from job_os.settings import get_settings

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

log = structlog.get_logger(__name__)


class OutreachVariant(str, enum.Enum):
    """The four situations that actually come up in a job search."""

    # The person who owns the hiring decision, who did not ask to hear from you.
    COLD_HIRING_MANAGER = "cold_hiring_manager"
    # An engineer at the company who cannot hire but can refer.
    REFERRAL_ASK = "referral_ask"
    # Someone who shares a school. The connection is the only reason this is not
    # a cold message, so the ledger must hold it or the variant is refused.
    ALUMNI = "alumni"
    # The application already exists. Do not re-pitch it.
    POST_APPLICATION_FOLLOWUP = "post_application_followup"


# Words a body may not reach, compared with `>=` so "under 120 words for a cold
# message" is enforced literally. Shorter is better everywhere and the follow-up
# is shortest of all: a message that arrives after an application has one job,
# which is to add one new thing and get out.
WORD_CAPS: dict[OutreachVariant, int] = {
    OutreachVariant.COLD_HIRING_MANAGER: 120,
    OutreachVariant.REFERRAL_ASK: 120,
    OutreachVariant.ALUMNI: 110,
    OutreachVariant.POST_APPLICATION_FOLLOWUP: 90,
}

# Subject lines longer than this get truncated in an inbox list, which is the one
# place the subject has to work.
SUBJECT_MAX_WORDS = 9

# Days before nudging the same person again, and how many nudges are acceptable
# at all. Two is the ceiling on purpose: a third message is not persistence, it is
# pressure, and it costs the user the contact.
MIN_DAYS_BETWEEN_MESSAGES = 5
MAX_FOLLOW_UPS = 2
FOLLOW_UP_BUSINESS_DAYS: dict[OutreachVariant, int] = {
    OutreachVariant.COLD_HIRING_MANAGER: 5,
    OutreachVariant.REFERRAL_ASK: 6,
    OutreachVariant.ALUMNI: 7,
    OutreachVariant.POST_APPLICATION_FOLLOWUP: 7,
}

# The whole output is a short message plus a handful of provenance rows, but the
# gateway routes to a model with extended thinking and `max_tokens` has to cover
# the thinking block too. A budget sized for the answer alone comes back empty
# with stop_reason max_tokens and zero text blocks, which is the failure the
# tailor loop documents at length.
DRAFT_MAX_TOKENS = 8000
RETRY_MAX_TOKENS = 12000

# One repair pass. The reviewer hands over the exact flags, so a pass that lands
# flat has hit the limit of the evidence rather than paused on its way up, and a
# person waiting on a two-sentence email will not wait through four passes.
MAX_REPAIR_PASSES = 1

# First person PLURAL only. Unlike a resume, an outreach message is a person
# speaking, so "I" and "my" are correct and required. "We" is the tell of a
# template: there is no we, there is one candidate writing to one stranger, and
# "we both" is also the opening half of every fabricated shared connection.
# "us" is lowercase-only for the reason resume_writing gives: "US" is a market.
#
# The apostrophe in the contractions is REQUIRED, and both the typewriter and the
# typographic form are accepted. Writing it as optional looks harmless and is not:
# `we'?re` matches the ordinary word "were", `we'?ll` matches "well", and `we'?d`
# matches "wed", so a message reading "most of the cases were real" was rejected
# for first person plural. A guard that fires on honest sentences gets worked
# around, which costs more than the guard was worth. The bare `[Ww]e` branch
# already catches every real contraction, since the boundary between "we" and an
# apostrophe is a word boundary.
_FIRST_PERSON_PLURAL_RE = re.compile(
    r"\b(?:[Ww]e|[Ww]e['’](?:re|ve|d|ll)|[Oo]ur|[Oo]urs|us)\b"
)

# Bare forms the career-ops ban list only covers in phrases ("robust
# architecture", "end-to-end solution"). In a two-sentence email the bare
# adjective is the whole problem.
OUTREACH_BANNED_WORDING = (
    *BANNED_WORDING,
    "robust",
    "end-to-end",
    "cutting edge",
)

# Openers and closers that tell the reader a template wrote this. Every one of
# these was in the first draft of something. They are checked as phrases rather
# than left to the model's taste, because taste is what varies between runs.
_CRINGE_RE = re.compile(
    r"(?:"
    r"hope this (?:email |message )?finds you well|"
    r"hope you(?:'re| are) doing well|"
    r"i(?:'m| am)? ?(?:just )?reaching out (?:to you )?(?:today|because i)|"
    r"i came across your profile|"
    r"i stumbled upon|"
    r"perfect fit|ideal candidate|great asset|value add|"
    r"rockstar|ninja|guru|"
    r"circle back|touch base|pick your brain|jump on a (?:quick )?call|"
    r"any guidance would be|"
    r"let me know if you have any questions|"
    r"i would love the opportunity to|"
    r"passionate about (?:your|the) mission|"
    r"i am writing to express my (?:strong )?interest"
    r")",
    re.I,
)

# The shape of a common-ground claim. Anything matching here asserts something
# about the RECIPIENT's history or a person between them, which is the class of
# claim that cannot be recovered from when it is wrong. Each pattern optionally
# captures the organisation it names, in group "org".
#
# Deliberately wide. A false positive costs one repair pass or one deleted
# sentence; a false negative costs the user a relationship.
_SHARED_CLAIM_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Named organisation attributed to the recipient or to both parties.
    re.compile(
        r"\balso (?:went to|studied at|attended|graduated from)\s+(?P<org>[^.,;!?]{2,60})",
        re.I,
    ),
    re.compile(r"\b(?:i )?also worked (?:at|for|on)\s+(?P<org>[^.,;!?]{2,60})", re.I),
    re.compile(r"\b(?:i )?(?:was|used to be) also at\s+(?P<org>[^.,;!?]{2,60})", re.I),
    re.compile(r"\byour (?:time|years|stint|days) at\s+(?P<org>[^.,;!?]{2,60})", re.I),
    re.compile(r"\bwhen you were (?:at|on)\s+(?P<org>[^.,;!?]{2,60})", re.I),
    re.compile(r"\bsince you(?:'?ve)? (?:were|worked) at\s+(?P<org>[^.,;!?]{2,60})", re.I),
    re.compile(
        r"\bsame (?:team|group|lab|program|cohort|class|batch) at\s+(?P<org>[^.,;!?]{2,60})",
        re.I,
    ),
    re.compile(r"\bfellow\s+(?P<org>[^.,;!?]{2,40})", re.I),
    re.compile(r"\balum(?:nus|na|ni)? of\s+(?P<org>[^.,;!?]{2,60})", re.I),
    # Common ground asserted with no organisation named.
    re.compile(r"\b(?:we|you and i) (?:both|also)\b", re.I),
    re.compile(r"\bwe (?:overlapped|crossed paths|worked together)\b", re.I),
    re.compile(r"\b(?:our|a) mutual (?:friend|connection|contact|colleague|acquaintance)\b", re.I),
    re.compile(r"\bmy (?:former|old|ex) (?:colleague|teammate|manager)\b", re.I),
    re.compile(r"\bi saw (?:that )?we\b", re.I),
    re.compile(r"\bas a fellow\b", re.I),
    re.compile(r"\bwe(?:'?re| are) both\b", re.I),
)

# Numbers that describe the ask rather than the candidate's work. "Would fifteen
# minutes next week work" is not a metric about anything, and requiring evidence
# for it would reject good drafts. Everything else has to be backed.
_ASK_DURATION_RE = re.compile(r"\b\d{1,3}\s*(?:-|\s)?(?:min|mins|minute|minutes)\b", re.I)
_NUMBER_RE = re.compile(
    r"(?<!\w)(?:\$?\d[\d,.]*%?|\d+\s?(?:ms|s|sec|min|hours?|days?|x))(?!\w)", re.I
)
_TECHNOLOGY_RE = re.compile(
    r"(?<!\w)(?:"
    r"aws|azure|gcp|kubernetes|docker|terraform|react|next\.?js|fastapi|django|"
    r"flask|postgres(?:ql)?|mongodb|redis|kafka|pytorch|tensorflow|scikit-learn|"
    r"langchain|langgraph|openai|claude|c\+\+|c#|java|python|golang|go|typescript|"
    r"rust|kotlin|swift|graphql|spark|airflow|snowflake|selenium|playwright|"
    r"cypress|pytest|junit|cucumber|jenkins|github actions|grpc|rabbitmq"
    r")(?!\w)",
    re.I,
)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


class OutreachDraftRejected(RuntimeError):
    """The model could not produce a message that passes the checks.

    Raised rather than returning the draft with a warning. A message that claims
    a shared employer the vault does not hold, or a metric nothing backs, is
    worse than no message, and showing it to the user is how it gets sent.
    """

    def __init__(self, flags: dict[str, list[str]]) -> None:
        self.flags = flags
        super().__init__(
            "Outreach draft rejected: "
            + "; ".join(f"{where}: {', '.join(items)}" for where, items in flags.items())
        )


@dataclass(frozen=True)
class VerifiedBullet:
    """One verified fact bullet, backend agnostic. Ids are strings because
    Postgres mints UUIDs and the Appwrite workspace mints its own shapes."""

    id: str
    fact_id: str
    text: str


@dataclass(frozen=True)
class VerifiedFact:
    """One verified profile fact, backend agnostic."""

    id: str
    kind: str
    title: str
    org: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OutreachTarget:
    """The person being written to, and what the user asserts about them.

    `shared_school`, `shared_employer` and `referred_by` are the user's
    assertions, not permission. `shared_context` intersects the first two with the
    verified vault before either can be said out loud.
    """

    full_name: str
    title: str | None = None
    company_name: str | None = None
    relationship: str = "other"
    shared_school: str | None = None
    shared_employer: str | None = None
    referred_by: str | None = None


@dataclass(frozen=True)
class SharedContext:
    """One thing the message is allowed to claim the two people have in common.

    Nothing outside this list may be asserted, and the list is built by Python
    from evidence on both sides. `org` is the folded organisation name, which is
    what the assertion scanner matches a drafted claim against.
    """

    id: str
    kind: str
    claim: str
    org: str | None = None
    fact_id: str | None = None


@dataclass(frozen=True)
class OutreachProvenance:
    """One row proving one phrase in the message came from somewhere real."""

    phrase: str
    evidence_kind: str
    evidence_id: str
    evidence_text: str


@dataclass(frozen=True)
class PriorContact:
    """One message already sent to this person, read back from the event log."""

    sent_at: datetime
    variant: str
    channel: str


@dataclass(frozen=True)
class FollowUpPlan:
    """When to nudge, or why not to."""

    suggested_at: datetime | None
    label: str
    is_final: bool = False


@dataclass(frozen=True)
class OutreachDraft:
    """A finished, checked message."""

    variant: OutreachVariant
    subject: str
    body: str
    word_count: int
    provenance: list[OutreachProvenance]
    shared_context_used: list[SharedContext]
    follow_up: FollowUpPlan
    warnings: list[str]
    note: str


class DraftedClaim(BaseModel):
    """The model's own account of where one phrase came from."""

    phrase: str
    evidence_kind: str
    evidence_id: str


class OutreachDraftOutput(BaseModel):
    """What one model pass returns."""

    subject: str = ""
    body: str = ""
    claims: list[DraftedClaim] = Field(default_factory=list)
    shared_context_ids: list[str] = Field(default_factory=list)
    note: str = ""


# ---------------------------------------------------------------------------
# The shared-context ledger. This is the fabrication guard, and it runs before
# the model does.
# ---------------------------------------------------------------------------

def _org_words(value: str | None) -> tuple[str, ...]:
    """An organisation name as whole words, punctuation and case folded away."""
    return tuple(identity_text(value).split())


def org_matches(left: str | None, right: str | None) -> bool:
    """Whether two organisation names denote the same place.

    Matched on runs of whole WORDS, so "Northeastern" matches "Northeastern
    University, Khoury College" in either direction, which is how a user types a
    school into a form against how a resume records it. Reuses `identity_text`
    so this cannot drift from how the vault decides two facts are one job.

    Plain substring containment was the obvious implementation and it was wrong
    in the one direction that costs something. "Stripe" is a substring of
    "Striped Systems Inc", so a user recording the recipient's employer as Stripe
    would have matched an unrelated verified job, earned a shared-employer ledger
    entry, and licensed the message to tell a Stripe engineer they used to be
    colleagues. Word runs cannot do that: "stripe" is not the word "striped".

    A run rather than a subset, because word ORDER carries meaning here.
    "Khoury College" matching "Northeastern University, Khoury College" is right;
    a scattered-word match would also pair "Boston University" with "Boston
    Children's Hospital University Program", which is not the same place.
    """
    a, b = _org_words(left), _org_words(right)
    if not a or not b:
        return False
    if a == b:
        return True
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    span = len(short)
    return any(long[start : start + span] == short for start in range(len(long) - span + 1))


def shared_context(
    *, facts: list[VerifiedFact], target: OutreachTarget
) -> list[SharedContext]:
    """Everything the message may claim the two people have in common.

    Each entry needs evidence on BOTH sides. The user saying the recipient went to
    Northeastern is one half; a verified education fact naming Northeastern is the
    other. One half alone produces nothing, which is the whole point: a shared
    school the vault cannot back is a fabrication however confident the user was
    when they typed it, and a verified degree says nothing about the recipient.

    A named referrer is the exception and needs no vault side, because it is the
    user reporting a first-hand conversation about their own life. The model still
    cannot invent one, since the field has to be filled in for the entry to exist.
    """
    found: list[SharedContext] = []

    if target.shared_school:
        for fact in facts:
            if fact.kind != "education":
                continue
            if not org_matches(fact.org, target.shared_school):
                continue
            found.append(
                SharedContext(
                    id=f"same_school:{fact.id}",
                    kind="same_school",
                    claim=f"Both studied at {fact.org}.",
                    org=identity_text(fact.org),
                    fact_id=fact.id,
                )
            )
            break

    if target.shared_employer:
        for fact in facts:
            if fact.kind != "experience":
                continue
            if not org_matches(fact.org, target.shared_employer):
                continue
            found.append(
                SharedContext(
                    id=f"same_employer:{fact.id}",
                    kind="same_employer",
                    claim=f"Both worked at {fact.org}.",
                    org=identity_text(fact.org),
                    fact_id=fact.id,
                )
            )
            break

    if target.referred_by:
        found.append(
            SharedContext(
                id="mutual_contact",
                kind="mutual_contact",
                claim=f"{target.referred_by} suggested getting in touch.",
                org=identity_text(target.referred_by),
            )
        )

    return found


def unbacked_shared_claims(body: str, allowed: list[SharedContext]) -> list[str]:
    """Common-ground claims in the body that the ledger does not support.

    Runs on the assembled text rather than on the model's citations, so it catches
    the failure whether or not the model bothered to cite anything. That is the
    difference between a guard and a request.

    A claim that names an organisation is checked against that organisation. A
    claim that names none ("a mutual connection", "as a fellow ...") is checked
    against the ledger being non-empty, which is the conservative reading: the
    phrase asserts common ground, so some common ground has to exist.
    """
    allowed_orgs = [entry.org for entry in allowed if entry.org]
    flagged: list[str] = []
    for pattern in _SHARED_CLAIM_PATTERNS:
        for match in pattern.finditer(body):
            named = (match.groupdict().get("org") or "").strip()
            if named:
                if any(org_matches(named, org) for org in allowed_orgs):
                    continue
                flagged.append(match.group(0).strip())
                continue
            if allowed:
                continue
            flagged.append(match.group(0).strip())
    # Stable and deduplicated, so a repair prompt does not list one phrase twice.
    return sorted(set(flagged))


def drop_unbacked_sentences(body: str, allowed: list[SharedContext]) -> str:
    """Delete the sentences carrying an unsupported common-ground claim.

    The last resort, after the repair pass. Safe in a way no other fix is: these
    claims are additive, so removing one can only make the message more honest.
    A shorter message that says nothing false beats a fuller one that does.
    """
    kept = [
        sentence
        for sentence in _SENTENCE_SPLIT_RE.split(body.strip())
        if sentence.strip() and not unbacked_shared_claims(sentence, allowed)
    ]
    return " ".join(kept).strip()


# ---------------------------------------------------------------------------
# Review. Everything the reviewer measures, it measures on the assembled body.
# ---------------------------------------------------------------------------


@dataclass
class DraftReview:
    """What Python found in one drafted message."""

    subject: str
    body: str
    word_count: int
    flags: dict[str, list[str]]
    provenance: list[OutreachProvenance]
    shared_context_used: list[SharedContext]

    @property
    def has_unbacked_shared_claim(self) -> bool:
        return any(
            flag.startswith("unbacked_shared_claim")
            for flags in self.flags.values()
            for flag in flags
        )


def _evidence_index(
    facts: list[VerifiedFact], bullets: list[VerifiedBullet]
) -> dict[str, tuple[str, str]]:
    """Every citable id mapped to (kind, the text that backs it).

    Facts contribute their own title, org and payload values as well as their
    bullets, because "wrote Go test suites at EPAM" is backed by the fact row even
    when no bullet spells it out.
    """
    index: dict[str, tuple[str, str]] = {}
    for fact in facts:
        payload_text = " ".join(
            str(value)
            for value in (fact.payload or {}).values()
            if isinstance(value, str | int | float)
        )
        payload_lists = " ".join(
            " ".join(str(item) for item in value)
            for value in (fact.payload or {}).values()
            if isinstance(value, list)
        )
        index[fact.id] = (
            "fact",
            " ".join(
                part
                for part in (fact.title, fact.org, payload_text, payload_lists)
                if part
            ),
        )
    for bullet in bullets:
        index[bullet.id] = ("bullet", bullet.text)
    return index


def review_draft(
    output: OutreachDraftOutput,
    *,
    variant: OutreachVariant,
    facts: list[VerifiedFact],
    bullets: list[VerifiedBullet],
    allowed_context: list[SharedContext],
) -> DraftReview:
    """Grade one drafted message against the evidence it was built from.

    Pure, so every guard in it is unit-testable without a model, a database or a
    network. The service calls this, hands the flags back to the model once, and
    refuses to ship anything still flagged.
    """
    # Dashes are fixed rather than flagged. The rule is global, the source facts
    # do not honour it, and replacing an em dash with a comma changes no meaning.
    subject = normalize_dashes(" ".join(output.subject.split())) or ""
    body = normalize_dashes(output.body.strip(), separator=", ") or ""
    words = len(body.split())
    flags: dict[str, list[str]] = {}

    def flag(where: str, item: str) -> None:
        flags.setdefault(where, []).append(item)

    index = _evidence_index(facts, bullets)
    allowed_by_id = {entry.id: entry for entry in allowed_context}

    # Provenance. An id the vault does not hold is dropped rather than trusted,
    # and a phrase that is not in the body is decoration: it proves nothing about
    # the message that actually ships.
    provenance: list[OutreachProvenance] = []
    lowered_body = body.casefold()
    for claim in output.claims:
        entry = index.get(claim.evidence_id)
        if entry is None:
            context = allowed_by_id.get(claim.evidence_id)
            if context is not None:
                provenance.append(
                    OutreachProvenance(
                        phrase=claim.phrase,
                        evidence_kind="shared_context",
                        evidence_id=context.id,
                        evidence_text=context.claim,
                    )
                )
                continue
            flag("provenance", f"unknown_evidence_id({claim.evidence_id})")
            continue
        kind, text = entry
        if claim.phrase.strip() and claim.phrase.strip().casefold() not in lowered_body:
            flag("provenance", f"phantom_provenance({claim.phrase[:40]})")
            continue
        provenance.append(
            OutreachProvenance(
                phrase=claim.phrase,
                evidence_kind=kind,
                evidence_id=claim.evidence_id,
                evidence_text=text,
            )
        )

    if body and not provenance:
        flag("provenance", "no_provenance")

    # Shared context. Cited ids are intersected with the ledger, then the body is
    # scanned regardless of what was cited.
    used = [allowed_by_id[cid] for cid in output.shared_context_ids if cid in allowed_by_id]
    invented = [cid for cid in output.shared_context_ids if cid not in allowed_by_id]
    for cid in invented:
        flag("shared_context", f"unknown_shared_context_id({cid})")
    for claim_text in unbacked_shared_claims(body, allowed_context):
        flag("shared_context", f"unbacked_shared_claim({claim_text})")

    # Writing rules.
    cap = WORD_CAPS[variant]
    if words >= cap:
        flag("body", f"too_long({words}w, cap {cap})")
    if not body:
        flag("body", "empty")
    if _FIRST_PERSON_PLURAL_RE.search(body):
        flag("body", "first_person_plural")
    lowered = body.casefold()
    banned = sorted(phrase for phrase in OUTREACH_BANNED_WORDING if phrase in lowered)
    if banned:
        flag("body", f"banned_wording({','.join(banned)})")
    cringe = sorted({m.group(0).casefold() for m in _CRINGE_RE.finditer(body)})
    if cringe:
        flag("body", f"template_phrasing({','.join(cringe)})")
    # normalize_dashes should have removed these. Checked anyway, because a
    # separator it cannot reach is still a rule broken.
    if has_banned_separator(body):
        flag("body", "dash")

    if subject:
        if has_banned_separator(subject):
            flag("subject", "dash")
        subject_words = len(subject.split())
        if subject_words > SUBJECT_MAX_WORDS:
            flag("subject", f"subject_too_long({subject_words}w)")
        if _FIRST_PERSON_PLURAL_RE.search(subject):
            flag("subject", "first_person_plural")
    else:
        flag("subject", "empty")

    # Claims about the candidate's work, checked against the evidence actually
    # cited plus every shared-context claim allowed.
    cited_text = " ".join(row.evidence_text for row in provenance).casefold()
    for number in _NUMBER_RE.finditer(body):
        token = number.group(0)
        window = body[max(0, number.start() - 4) : number.end() + 12]
        if _ASK_DURATION_RE.search(window):
            # The length of the meeting being asked for, not a metric.
            continue
        if token.casefold().strip("$%") in cited_text.replace(",", ""):
            continue
        if token.casefold() in cited_text:
            continue
        flag("evidence", f"unsupported_number({token})")

    for tech in {m.group(0).casefold() for m in _TECHNOLOGY_RE.finditer(body)}:
        # Deliberately checked against the CANDIDATE's evidence and not against
        # the job description. Naming a technology because the posting asks for it
        # is the exact move this pipeline exists to stop, and a message that
        # discusses the role without claiming an unheld skill is the better
        # message anyway.
        if mentions_word(cited_text, tech):
            continue
        flag("evidence", f"unsupported_technology({tech})")

    for skill in sorted(UNPRINTABLE_SKILLS):
        if mentions_word(body, skill):
            # The career-ops rules keep these off the page whatever the vault
            # says, and a message is a stronger claim than a resume line: it is
            # the candidate saying it in their own voice.
            flag("evidence", f"unprintable_skill({skill})")

    # Status. A body claiming something shipped, when the evidence behind it
    # records a demo pending approval, is the overstatement an interviewer
    # punctures in one question. `text_is_about_source=False` because the body is
    # judged against several facts at once, which is the summary case.
    if cited_text and upgrades_status(body, cited_text, text_is_about_source=False):
        flag("evidence", "upgraded_status")

    return DraftReview(
        subject=subject,
        body=body,
        word_count=words,
        flags={where: sorted(set(items)) for where, items in flags.items()},
        provenance=provenance,
        shared_context_used=used,
    )


# ---------------------------------------------------------------------------
# Follow-up timing and double-message prevention.
# ---------------------------------------------------------------------------


def add_business_days(start: datetime, days: int) -> datetime:
    """`days` working days after `start`, skipping weekends.

    A nudge that lands on a Sunday is a nudge that gets buried by Monday morning,
    so the suggestion is in working days rather than calendar days.
    """
    moved = start
    remaining = days
    while remaining > 0:
        moved += timedelta(days=1)
        if moved.weekday() < 5:
            remaining -= 1
    return moved


def follow_up_plan(
    *, variant: OutreachVariant, sent_at: datetime, prior_sends: int = 0
) -> FollowUpPlan:
    """When to nudge this person next, or why to stop.

    `prior_sends` counts messages already sent to this contact, including the one
    at `sent_at`. Past `MAX_FOLLOW_UPS` the honest answer is to stop, and saying
    so is more use than another date.
    """
    if prior_sends > MAX_FOLLOW_UPS:
        return FollowUpPlan(
            suggested_at=None,
            label=(
                f"Stop here. {MAX_FOLLOW_UPS} follow-ups is the limit, and a third "
                "message reads as pressure rather than interest."
            ),
            is_final=True,
        )
    days = FOLLOW_UP_BUSINESS_DAYS[variant]
    when = add_business_days(sent_at, days)
    which = "Follow up" if prior_sends <= 1 else "Last follow-up"
    return FollowUpPlan(
        suggested_at=when,
        label=f"{which} with {_first_name(variant)} in {days} working days",
        is_final=prior_sends == MAX_FOLLOW_UPS,
    )


def _first_name(variant: OutreachVariant) -> str:
    """Who the follow-up is with, in the label's own terms."""
    return {
        OutreachVariant.COLD_HIRING_MANAGER: "the hiring manager",
        OutreachVariant.REFERRAL_ASK: "the referral contact",
        OutreachVariant.ALUMNI: "the alumni contact",
        OutreachVariant.POST_APPLICATION_FOLLOWUP: "this application",
    }[variant]


def double_message_block(
    *,
    prior: list[PriorContact],
    now: datetime,
    min_days: int = MIN_DAYS_BETWEEN_MESSAGES,
) -> str | None:
    """Why this send should not happen yet, or None when it should.

    Two separate reasons to stop, and they are different problems. Messaging the
    same person twice inside a working week is the one the user does by accident,
    usually after switching devices. Messaging them a fourth time is a decision,
    and the answer is still no.
    """
    if not prior:
        return None
    if len(prior) > MAX_FOLLOW_UPS:
        return (
            f"{len(prior)} messages have already gone to this person. "
            f"{MAX_FOLLOW_UPS} follow-ups is the limit."
        )
    latest = max(prior, key=lambda sent: sent.sent_at)
    gap = now - latest.sent_at
    if gap < timedelta(days=min_days):
        next_ok = latest.sent_at + timedelta(days=min_days)
        return (
            f"A {latest.variant.replace('_', ' ')} message went to this person "
            f"{gap.days} day(s) ago on {latest.sent_at.date().isoformat()}. "
            f"Wait until {next_ok.date().isoformat()}."
        )
    return None


# ---------------------------------------------------------------------------
# Prompts.
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You write one short outreach message from one job seeker to one named person at a
company. You are not writing a cover letter and you are not writing a resume. You
are writing the kind of note an engineer sends another engineer, which a busy
person reads in fifteen seconds and can answer in one line.

HARD CONSTRAINTS, these are non-negotiable:
1. Every specific claim about the sender MUST come from the verified evidence you
   are given, and every one MUST appear in `claims` with the `evidence_id` it came
   from. Never cite an id that is not in the evidence list. If a phrase has no
   evidence behind it, cut the phrase.
2. NEVER assert anything the two people have in common unless it appears in the
   SHARED CONTEXT list with an id, and put that id in `shared_context_ids`. If
   that list is empty, the message has no common ground and must not imply any.
   No shared employer, no shared school, no mutual contact, no "I saw we both".
   A fabricated connection to someone who can check it in one click is the single
   worst thing this message can do, and it is worse than a plain cold note.
3. Never invent, upgrade or round a number, a technology, a title, a date or an
   outcome. Never claim work shipped when the evidence records it as demoed,
   pending approval, a prototype, a hackathon build, a trial or a mock. Carry the
   qualifier through instead.
4. Only name a technology the sender's own verified evidence names. The job
   description is context for what THEY want, never a source for what HE has done.
5. No first person plural. No "we", "our", "us". There is one person writing to
   one stranger and "we" is the tell of a template.
6. No em dashes, en dashes or double hyphens anywhere. Commas, colons, periods.
7. Banned words, no exceptions: leveraged, utilized, spearheaded, cutting-edge,
   state-of-the-art, innovative, robust, seamlessly, synergized, revolutionized,
   facilitated, enabled, end-to-end. They read as machine-written to anyone who
   has seen twenty of these this month. Say the plain verb: built, wrote, tested,
   migrated, measured, fixed.

WHAT MAKES ONE OF THESE GET ANSWERED. There is exactly one reason a stranger
replies, and it is that the message is obviously about them and obviously not a
template:
- Name the specific role or the specific team in the first sentence, in their
  words, not "your company".
- Then ONE concrete thing the sender built that bears on that team's problem, in
  plain terms with the constraint that made it interesting. Not a list. One.
- Then ONE ask that is cheap to grant and easy to refuse. A short call, a pointer
  to the right person, a yes or no. Never "any guidance would be appreciated".
- Close and stop. No signature block, no attachment, no "let me know if you have
  any questions", no restating the ask.

HOW TO WRITE IT SO A PERSON WROTE IT:
- Short sentences. Say the thing, then stop. If he would not say it out loud to a
  colleague, cut it.
- Specific beats impressive. "The big model cost too much per image, so a small
  one scored the rest" beats "applied knowledge distillation".
- No adjectives about himself. No "passionate", "driven", "strong background".
  Evidence carries that or nothing does.
- Do not open with a compliment about the company. They know.
- Never open with "I hope this finds you well", "I am reaching out", or "I came
  across your profile".
- One question mark in the whole message, at the ask.
- Subject line: under nine words, lowercase-ish, concrete, no colons stacking two
  clauses. It should read like a person typed it in a hurry, not like a campaign.

LENGTH IS A HARD CEILING, NOT A TARGET. The word cap you are given is measured by
Python the moment you answer and a message at the cap is rejected outright.
Aim well under it. Every sentence that is not the role, the evidence, or the ask
is a sentence to cut.

Output: a single JSON object matching the provided schema. No prose, no fences.
"""

_VARIANT_BRIEFS: dict[OutreachVariant, str] = {
    OutreachVariant.COLD_HIRING_MANAGER: """\
The recipient owns the hiring decision for this role and did not ask to hear from
anyone. Nothing is owed to the sender, so the message earns its reply or it does
not get one.
Shape: one sentence naming the role and why this team specifically, one or two on
the single most relevant thing built, one cheap ask. No resume pasted in the body,
no attachment mentioned, no salary, no availability calendar.
""",
    OutreachVariant.REFERRAL_ASK: """\
The recipient is an engineer at the company. They cannot hire, so do not ask them
to. What they can do is refer, or say which team is actually hiring, or say the
role is not worth applying to.
Shape: name the exact role being applied for, one or two lines of relevant
evidence, then an ask that is genuinely easy to decline: whether they would be
willing to refer, or failing that whether they can point at the right person.
Say explicitly that a no is a fine answer. Do not imply a prior relationship.
""",
    OutreachVariant.ALUMNI: """\
The recipient shares a school with the sender and that is the ONLY reason this is
not a cold message. State it once, plainly, in the first sentence, and then stop
leaning on it: the rest of the message has to be earned with evidence like any
other. An alumni opener followed by nothing specific is worse than a cold note,
because it spent the one thing they had in common on nothing.
Shape: the shared school in one short clause, the role, one piece of evidence,
one ask. Never call them a fellow anything more than once.
""",
    OutreachVariant.POST_APPLICATION_FOLLOWUP: """\
The application is already in. The recipient may not have read it and may not be
the person who will. Do not re-pitch and do not restate the resume.
Shape: name the role and when it was applied for in one clause, add exactly ONE
thing that is new since then or one specific detail from the evidence that the
application did not surface, then ask one question about timeline or next steps.
Shortest of the four. If it is over sixty words it is too long.
""",
}


def _evidence_payload(
    facts: list[VerifiedFact], bullets: list[VerifiedBullet]
) -> list[dict[str, Any]]:
    """The verified vault, compact, with the ids the model must cite."""
    by_fact: dict[str, list[VerifiedBullet]] = {}
    for bullet in bullets:
        by_fact.setdefault(bullet.fact_id, []).append(bullet)
    payload: list[dict[str, Any]] = []
    for fact in facts:
        entry: dict[str, Any] = {
            "fact_id": fact.id,
            "kind": fact.kind,
            "title": fact.title,
        }
        if fact.org:
            entry["org"] = fact.org
        if fact.start_date or fact.end_date:
            entry["dates"] = (
                f"{fact.start_date or '?'} to {fact.end_date or 'present'}"
            )
        if fact.payload:
            entry["payload"] = fact.payload
        found = by_fact.get(fact.id) or []
        if found:
            entry["bullets"] = [{"id": b.id, "text": b.text} for b in found]
        # A fact whose evidence says the work is provisional is marked, so the
        # writer knows before it composes rather than after Python refuses it.
        if any(records_provisional_status(b.text) for b in found) or (
            records_provisional_status(fact.title)
        ):
            entry["status_note"] = (
                "This work is provisional in the evidence. Do not say it shipped."
            )
        payload.append(entry)
    return payload


def build_user_prompt(
    *,
    variant: OutreachVariant,
    target: OutreachTarget,
    job: dict[str, Any],
    facts: list[VerifiedFact],
    bullets: list[VerifiedBullet],
    allowed_context: list[SharedContext],
    prior: list[PriorContact],
    user_note: str | None = None,
) -> str:
    """One turn carrying the situation, the evidence, and the ledger."""
    ledger = [
        {"id": entry.id, "kind": entry.kind, "you_may_say": entry.claim}
        for entry in allowed_context
    ]
    sections = [
        f"VARIANT: {variant.value}",
        _VARIANT_BRIEFS[variant].strip(),
        "",
        f"WORD CAP for the body, measured by Python: under {WORD_CAPS[variant]} words.",
        "",
        "THE PERSON YOU ARE WRITING TO:",
        json.dumps(
            {
                "full_name": target.full_name,
                "title": target.title,
                "company": target.company_name,
                "relationship": target.relationship,
            },
            indent=2,
        ),
        "",
        "THE ROLE:",
        json.dumps(job, indent=2)[:6000],
        "",
        "SHARED CONTEXT, the complete and only list of things you may claim the "
        "two of them have in common:",
        json.dumps(ledger, indent=2) if ledger else "[]  <- EMPTY. Claim nothing.",
        "",
        "VERIFIED EVIDENCE. Cite `fact_id` values for facts and bullet `id` "
        "values for bullets. Nothing outside this list exists:",
        json.dumps(_evidence_payload(facts, bullets), indent=2)[:20000],
    ]
    if prior:
        sections += [
            "",
            "ALREADY SENT to this person, so do not repeat an opening or an ask "
            "they have already read:",
            json.dumps(
                [
                    {
                        "sent_at": sent.sent_at.date().isoformat(),
                        "variant": sent.variant,
                    }
                    for sent in prior
                ],
                indent=2,
            ),
        ]
    if user_note:
        sections += [
            "",
            "ONE NOTE FROM THE SENDER. Use it if it helps and ignore it if it "
            "would break a constraint above. It is not evidence and it cannot be "
            f"cited: {user_note[:500]}",
        ]
    return "\n".join(sections)


def _repair_prompt(review: DraftReview, *, variant: OutreachVariant) -> str:
    """Feedback after a pass Python refused, in the terms it refused it."""
    lines = [
        "Python measured that draft against the evidence and it cannot ship. "
        "What is wrong, by location:",
    ]
    for where, items in sorted(review.flags.items()):
        lines.append(f"  - {where}: {', '.join(items)}")
    lines += [
        "",
        "How to read those flags. unbacked_shared_claim means you asserted "
        "something the two of them have in common that is NOT in the shared "
        "context list: delete that clause entirely, do not reword it, and do not "
        "replace it with a softer version of the same claim. "
        "unsupported_number and unsupported_technology mean the body names a "
        "figure or a tool that no cited evidence carries: cut it or cite the "
        "bullet that actually holds it. unprintable_skill means the message "
        "claims a skill the sender does not write. upgraded_status means you said "
        "provisional work shipped. no_provenance means you gave no citations at "
        "all. phantom_provenance means a cited phrase is not in the body you "
        "wrote. too_long means cut sentences, not words: the cap is measured "
        "strictly. first_person_plural means remove we, our and us. "
        "template_phrasing means that phrase appears in every one of these "
        "messages and it is why they get deleted unread.",
        "",
        f"Return the FULL message again as one JSON object, same schema, under "
        f"{WORD_CAPS[variant]} words. Shorter is better than fuller.",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# The agent.
# ---------------------------------------------------------------------------


async def run_outreach_draft(
    *,
    variant: OutreachVariant,
    target: OutreachTarget,
    job: dict[str, Any],
    facts: list[VerifiedFact],
    bullets: list[VerifiedBullet],
    prior: list[PriorContact] | None = None,
    user_note: str | None = None,
    now: datetime | None = None,
    client: Any | None = None,
) -> OutreachDraft:
    """Draft one checked outreach message. No database access.

    `facts` and `bullets` must already be verified rows. The loaders filter on
    `verified=True`, and this function filters again below, because a caller that
    forgets is a fabricated resume claim in an email rather than a test failure.
    """
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set; cannot draft outreach.")

    when = now or _utcnow()
    prior_sends = list(prior or [])

    allowed_context = shared_context(facts=facts, target=target)

    # An alumni message with no shared school is not a variant, it is a lie with a
    # friendly opening. Refused here rather than left to the prompt, because the
    # prompt cannot refuse anything.
    if variant is OutreachVariant.ALUMNI and not any(
        entry.kind == "same_school" for entry in allowed_context
    ):
        raise OutreachDraftRejected(
            {
                "shared_context": [
                    "alumni_variant_without_verified_shared_school: record the school "
                    "this person attended, and it has to match a school in your "
                    "verified profile"
                ]
            }
        )

    client = client or anthropic.AsyncAnthropic(
        auth_token=settings.anthropic_api_key,
        base_url=settings.anthropic_base_url or None,
    )

    messages: list[anthropic.types.MessageParam] = [
        {
            "role": "user",
            "content": build_user_prompt(
                variant=variant,
                target=target,
                job=job,
                facts=facts,
                bullets=bullets,
                allowed_context=allowed_context,
                prior=prior_sends,
                user_note=user_note,
            ),
        }
    ]

    review: DraftReview | None = None
    note = ""
    for attempt in range(MAX_REPAIR_PASSES + 1):
        raw = await _one_pass(
            client,
            settings=settings,
            messages=messages,
            max_tokens=DRAFT_MAX_TOKENS if attempt == 0 else RETRY_MAX_TOKENS,
        )
        try:
            output = parse_model_json(OutreachDraftOutput, raw)
        except ValidationError as error:
            log.warning("outreach.invalid_json", preview=raw[:400], attempt=attempt + 1)
            if attempt == MAX_REPAIR_PASSES:
                raise OutreachDraftRejected(
                    {"model": ["invalid_json_after_retry"]}
                ) from error
            messages = [
                *messages,
                {"role": "assistant", "content": raw[:2000]},
                {"role": "user", "content": JSON_ONLY_RETRY},
            ]
            continue

        # Kept whatever the review then says, because the note is the model
        # talking to the user about the draft ("no evidence names their team, so
        # the opener stays general") and that is most worth reading on the pass
        # that only just made it through.
        note = normalize_dashes(output.note.strip(), separator=", ") or ""
        review = review_draft(
            output,
            variant=variant,
            facts=facts,
            bullets=bullets,
            allowed_context=allowed_context,
        )
        log.info(
            "outreach.pass",
            variant=variant.value,
            attempt=attempt + 1,
            words=review.word_count,
            flags=sorted(
                {flag.split("(")[0] for flags in review.flags.values() for flag in flags}
            ),
            provenance_rows=len(review.provenance),
        )
        if not review.flags:
            break
        if attempt == MAX_REPAIR_PASSES:
            break
        messages = [
            *messages,
            {"role": "assistant", "content": raw},
            {"role": "user", "content": _repair_prompt(review, variant=variant)},
        ]

    if review is None:  # pragma: no cover - the loop always assigns or raises
        raise OutreachDraftRejected({"model": ["no_response"]})

    warnings: list[str] = []
    # Last resort, and the only fix Python can make without a model: cut the
    # sentences carrying an unsupported common-ground claim. Deleting an additive
    # claim cannot make the message less honest.
    if review.has_unbacked_shared_claim:
        trimmed = drop_unbacked_sentences(review.body, allowed_context)
        log.warning(
            "outreach.dropped_unbacked_sentences",
            variant=variant.value,
            before_words=review.word_count,
            after_words=len(trimmed.split()),
        )
        warnings.append(
            "A sentence claiming something you two have in common was removed, "
            "because your verified profile does not back it."
        )
        review = review_draft(
            OutreachDraftOutput(
                subject=review.subject,
                body=trimmed,
                # Only the citations whose phrase survived the cut. Carrying a
                # claim about a deleted sentence forward would come straight back
                # as phantom_provenance and reject a message that is now MORE
                # honest than the one that was flagged. If nothing survives, the
                # re-review says no_provenance and refuses, which is the right
                # answer: a message with every backed sentence removed has
                # nothing left to stand on.
                claims=[
                    DraftedClaim(
                        phrase=row.phrase,
                        evidence_kind=row.evidence_kind,
                        evidence_id=row.evidence_id,
                    )
                    for row in review.provenance
                    if row.phrase.strip().casefold() in trimmed.casefold()
                ],
                shared_context_ids=[entry.id for entry in review.shared_context_used],
            ),
            variant=variant,
            facts=facts,
            bullets=bullets,
            allowed_context=allowed_context,
        )

    if review.flags:
        raise OutreachDraftRejected(review.flags)

    if prior_sends:
        warnings.append(
            f"{len(prior_sends)} message(s) already went to this person. "
            f"Last one {max(s.sent_at for s in prior_sends).date().isoformat()}."
        )

    return OutreachDraft(
        variant=variant,
        subject=review.subject,
        body=review.body,
        word_count=review.word_count,
        provenance=review.provenance,
        shared_context_used=review.shared_context_used,
        follow_up=follow_up_plan(
            variant=variant, sent_at=when, prior_sends=len(prior_sends) + 1
        ),
        warnings=warnings,
        note=note,
    )


async def _one_pass(
    client: Any,
    *,
    settings: Any,
    messages: list[anthropic.types.MessageParam],
    max_tokens: int,
) -> str:
    """One model call, returning its text.

    `anthropic_model_tailor` with the sonnet tier, rather than a new setting.
    The settings module records that job-os-sonnet serves claude-sonnet-5
    whatever model id it is handed, so the tier header is what actually decides
    the model and the id is decoration on this route.
    """
    message = await create_message(
        client,
        model=settings.anthropic_model_tailor,
        max_tokens=max_tokens,
        system=f"{CAREER_OPS_RULES}\n\n{SYSTEM_PROMPT}",
        messages=messages,
        extra_headers={"x-manifest-tier": settings.manifest_tier_sonnet},
    )
    text = response_text(message)
    if not text.strip():
        log.warning("outreach.empty_reply", **response_diagnostics(message))
    return text


def _utcnow() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Postgres adapters.
# ---------------------------------------------------------------------------


async def load_verified_vault(
    session: AsyncSession, user_id: Any
) -> tuple[list[VerifiedFact], list[VerifiedBullet]]:
    """Every VERIFIED fact and its bullets, adapted for drafting.

    The `verified == True` filter is the no-fabrication contract's first line:
    `verified=False` rows are drafts an agent proposed from a gap question and the
    user has not confirmed, so they describe work that may not have happened.
    """
    from sqlalchemy import select

    from job_os.db.models import FactBullet, ProfileFact

    facts_result = await session.execute(
        select(ProfileFact)
        .where(ProfileFact.user_id == user_id, ProfileFact.verified.is_(True))
        .order_by(ProfileFact.kind, ProfileFact.start_date.desc().nullslast())
    )
    rows = list(facts_result.scalars().all())
    facts = [
        VerifiedFact(
            id=str(row.id),
            kind=row.kind,
            title=row.title,
            org=row.org,
            start_date=row.start_date,
            end_date=row.end_date,
            payload=row.payload or {},
        )
        for row in rows
    ]
    if not rows:
        return facts, []
    bullets_result = await session.execute(
        select(FactBullet).where(FactBullet.fact_id.in_([row.id for row in rows]))
    )
    bullets = [
        VerifiedBullet(id=str(b.id), fact_id=str(b.fact_id), text=b.text)
        for b in bullets_result.scalars().all()
    ]
    return facts, bullets


# Event kinds written to `application_events`. Reusing that table rather than
# minting a parallel log means the outreach history shows up on the existing
# application timeline for free, and there is one answer to "what happened with
# this application" instead of two.
EVENT_DRAFTED = "outreach_drafted"
EVENT_SENT = "outreach_sent"


def prior_contacts(events: list[Any], *, contact_id: str) -> list[PriorContact]:
    """Messages already sent to one person, read out of the event log.

    Takes the raw event rows so the caller does not have to know the payload
    shape, and so the double-message guard cannot be fooled by a differently
    filtered query.
    """
    found: list[PriorContact] = []
    for event in events:
        if getattr(event, "kind", None) != EVENT_SENT:
            continue
        payload = getattr(event, "payload", None) or {}
        if str(payload.get("contact_id") or "") != str(contact_id):
            continue
        found.append(
            PriorContact(
                sent_at=getattr(event, "occurred_at", None) or _utcnow(),
                variant=str(payload.get("variant") or "outreach"),
                channel=str(payload.get("channel") or "email"),
            )
        )
    return sorted(found, key=lambda sent: sent.sent_at)
