"""When a posting asks when you can start, answer it from verified dates only.

A real internship posting asked applicants to "state your availability including
start and end dates". The page that came back never mentioned a date anywhere
above the education block, so the one question the recruiter was told to look
for took longer than ten seconds to answer and the application read as
incomplete.

Answering it is not a writing problem, because the answer is not a sentence: it
is one line assembled from dates the profile already holds. So it is assembled
here, in Python, from verified facts, and nothing in this module can produce a
date the vault does not state. A profile with no dates gets a gap instead, which
is the honest outcome and the one the user can act on.

Nothing here knows anything about a particular candidate, school or country. It
reads whatever the profile happens to carry.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

# Phrasings that mean the employer wants to be told when you are free, as
# opposed to the employer telling YOU when the job runs. The distinction is the
# whole point: "the internship starts in June" is information, and answering it
# with an availability line is fine but not required, while "state your
# availability including start and end dates" is an instruction an application
# is scored against.
#
# Deliberately built out of ask-shaped verbs rather than the bare word
# "availability", which appears in plenty of postings as "availability of
# mentorship" and "high availability".
_ASK_VERB = (
    r"(?:state|indicate|include|provide|specify|list|share|note|confirm|mention|"
    r"detail|outline|tell us|let us know|please note)"
)
_AVAILABILITY_ASK_RE = re.compile(
    # "state your availability", "please indicate your start and end dates"
    rf"\b{_ASK_VERB}\b[^.\n]{{0,80}}?\b(?:availability|available|start date|"
    r"start and end date|start/end date|dates of availability)"
    # "your availability", "your expected start date"
    r"|\byour\s+(?:availability|expected\s+(?:start|graduation)\s+date)\b"
    # "availability (start and end dates)"
    r"|\bavailability\b[^.\n]{0,40}\b(?:start|end)\b[^.\n]{0,20}\bdate"
    # "earliest start date", "expected graduation date"
    r"|\bearliest\s+(?:possible\s+)?start\s+date\b"
    r"|\bexpected\s+graduation(?:\s+date)?\b"
    r"|\banticipated\s+graduation(?:\s+date)?\b"
    # "when you can start", "when are you available to start"
    r"|\bwhen\s+(?:you|they|the candidate)\s+(?:can|could|are\s+able\s+to|"
    r"would\s+be\s+able\s+to)\s+start\b"
    r"|\bwhen\s+(?:are|is)\s+\w+\s+available\b",
    re.I,
)

# The other way a posting makes timing matter: it states a schedule the
# applicant has to satisfy, rather than asking them to state theirs.
#
# Salesforce's "Summer 2027 Intern" gates on "Returning to school after Summer
# 2027 to complete your degree". Nothing there is an instruction, so the ask
# pattern above reads it as prose and the page comes out with no date above the
# education block. But the recruiter is checking exactly one thing, the
# candidate answers it (enrolled through May 2028), and the resume never says
# so. A condition the reader is screening against is worth answering whether or
# not they thought to phrase it as a question.
#
# Narrower than the ask patterns on purpose, because the vocabulary here is
# common. Each alternative needs the SHAPE of a requirement, not just the words:
# "returning to school" and "recent graduates" both contain "return"/"graduat",
# and only the first is a condition on when this person is free.
_AVAILABILITY_CONDITION_RE = re.compile(
    # "returning to school after Summer 2027", "must return to school following"
    r"\b(?:returning|return)\s+to\s+(?:school|university|college|studies)\b"
    # "enrolled in the fall following the internship", "enrolled through May 2027"
    r"|\benrolled\b[^.\n]{0,40}\b(?:following|after|through|during)\b[^.\n]{0,40}"
    r"\b(?:internship|programme|program|fall|spring|semester|term)\b"
    # "graduating between December 2027 and June 2028"
    r"|\bgraduat\w*\s+(?:between|in|by|no\s+earlier\s+than|no\s+later\s+than)\b"
    # "must be enrolled for the duration", "currently enrolled and returning"
    r"|\bmust\s+be\s+(?:currently\s+)?enrolled\b"
    # "complete your degree after the internship"
    r"|\bcomplete\s+your\s+degree\b",
    re.I,
)

# How much of a long posting is read for the ask. The instruction lives in the
# application section, which is at the end, so this is deliberately larger than
# the slice the writer prompt gets.
_JD_SCAN_CHARS = 40_000

# Payload keys a profile may carry an availability statement under. Read rather
# than required: a vault that has none of them falls through to the dates it
# does hold.
_AVAILABILITY_KEYS = ("availability", "available", "available_from", "available_to")
_WORK_AUTH_KEYS = (
    "work_authorization",
    "work_auth",
    "authorization",
    "visa",
    "visa_status",
    "sponsorship",
)

_MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)

# Long enough to be a statement, short enough to sit on a contact line. A
# profile that recorded three sentences under "availability" gets the first
# clause rather than a wrapped paragraph across the top of the page.
MAX_AVAILABILITY_CHARS = 90


class _Fact(Protocol):
    """The part of a verified fact this module reads.

    Structural rather than an import of `TailorFact`, so this module stays
    testable on its own and does not drag the Anthropic client in with it.
    """

    kind: str
    title: str
    start_date: date | None
    end_date: date | None
    payload: dict[str, Any]


@dataclass(frozen=True)
class Availability:
    """What the page can honestly say about when this candidate is free.

    `line` is empty when the profile states nothing. That is not a failure, it
    is the gap: see `AVAILABILITY_GAP_*`, which the tailor surfaces so the user
    knows which edit closes it.
    """

    line: str = ""
    # True when the line came from an explicit availability or work-authorization
    # statement rather than from a graduation date. A graduation date answers the
    # recruiter's question well enough to print, and it is still not the window
    # the posting asked for, so the gap is raised either way.
    explicit: bool = False

    def __bool__(self) -> bool:
        return bool(self.line)


AVAILABILITY_GAP_REQUIREMENT = "Your availability (start and end dates)"
AVAILABILITY_GAP_WHY = (
    "This posting asks when you can start and finish. Add your start and end "
    "dates on Profile and tailor again, and they go at the top of the page."
)
AVAILABILITY_GAP_WHY_PARTIAL = (
    "This posting asks for exact start and end dates. The page shows what your "
    "profile states; add the exact dates on Profile to answer it in full."
)


def posting_asks_for_availability(
    jd_parsed: dict[str, Any] | None, jd_clean: str | None
) -> bool:
    """Whether this posting told the applicant to state when they are free.

    Both halves of the parse are read, because the instruction lands in
    different places depending on how the posting is written: inside the
    responsibilities list on one ATS, in a closing paragraph on another. The
    clean text is the reliable one and the parsed lists are a cheap extra.
    """
    haystacks: list[str] = [str(jd_clean or "")[:_JD_SCAN_CHARS]]
    for key in ("responsibilities", "qualifications", "required_skills", "keywords"):
        for entry in (jd_parsed or {}).get(key) or []:
            haystacks.append(str(entry))
    return any(
        _AVAILABILITY_ASK_RE.search(text) or _AVAILABILITY_CONDITION_RE.search(text)
        for text in haystacks
        if text
    )


def _month_year(value: date | None) -> str:
    if value is None:
        return ""
    return f"{_MONTHS[value.month - 1]} {value.year}"


def _clean_statement(value: Any) -> str:
    """One line of the user's own wording, or "" if there is nothing usable."""
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    if len(text) > MAX_AVAILABILITY_CHARS:
        # First sentence, then first clause, then give up rather than print a
        # truncated word across the top of a resume.
        for separator in (". ", "; "):
            head = text.split(separator, 1)[0].strip()
            if head and len(head) <= MAX_AVAILABILITY_CHARS:
                return head
        return ""
    return text


def _payload_statement(fact: _Fact, keys: Iterable[str]) -> str:
    payload = getattr(fact, "payload", None) or {}
    for key in keys:
        statement = _clean_statement(payload.get(key))
        if statement:
            return statement
    return ""


def _stated_window(fact: _Fact) -> str:
    """"Available May 2027 to August 2027", from a fact that carries the dates.

    Only fires for a fact that is ABOUT availability. A project's own start and
    end dates say when the project ran, and reading them as a hiring window is
    exactly the kind of invention this module exists to refuse.
    """
    start = _month_year(getattr(fact, "start_date", None))
    end = _month_year(getattr(fact, "end_date", None))
    if start and end:
        return f"Available {start} to {end}"
    if start:
        return f"Available from {start}"
    return ""


def _is_availability_fact(fact: _Fact) -> bool:
    kind = str(getattr(fact, "kind", "") or "").casefold()
    if kind == "availability":
        return True
    title = str(getattr(fact, "title", "") or "").casefold()
    return title.startswith("available") or title.startswith("availability")


def _graduation(facts: Iterable[_Fact]) -> date | None:
    """The education end date a recruiter would read as the graduation date.

    The latest one, because a candidate part-way through a second degree is
    available on the second degree's terms, not the first one's.
    """
    dates = [
        f.end_date
        for f in facts
        if str(getattr(f, "kind", "") or "") == "education" and f.end_date is not None
    ]
    return max(dates) if dates else None


def derive_availability(
    facts: Iterable[_Fact],
    *,
    basics: dict[str, Any] | None = None,
    today: date | None = None,
) -> Availability:
    """The availability line these verified facts support, or an empty one.

    Assembled from three sources, best first, and every one of them is something
    the user wrote down:

    1. A fact that is about availability, or any fact carrying an availability
       statement in its payload. This is the user's own wording and it wins.
    2. A work-authorization statement, which is the other half of the same
       question for anyone who needs one.
    3. The graduation month and year. Not the window the posting asked for, and
       still the single date a recruiter is looking for in the first ten
       seconds, so it is worth the line on its own.

    Never composed out of anything else. In particular a graduation date is
    never turned into a start date: "graduating May 2028" is a fact, and
    "available from June 2028" is a guess about what the candidate wants.
    """
    facts = list(facts)
    parts: list[str] = []
    explicit = False

    stated = _clean_statement((basics or {}).get("availability"))
    if not stated:
        for fact in facts:
            statement = _payload_statement(fact, _AVAILABILITY_KEYS)
            if not statement:
                statement = _stated_window(fact) if _is_availability_fact(fact) else ""
            if statement:
                stated = statement
                break
    if stated:
        parts.append(stated)
        explicit = True

    work_auth = _clean_statement((basics or {}).get("work_authorization"))
    if not work_auth:
        for fact in facts:
            work_auth = _payload_statement(fact, _WORK_AUTH_KEYS)
            if work_auth:
                break
    if work_auth and work_auth.casefold() not in " ".join(parts).casefold():
        parts.append(work_auth)
        explicit = True

    graduation = _graduation(facts)
    if graduation is not None and not parts:
        # Only when nothing better was found. A page that already states a
        # window does not need the graduation date repeated on the same line;
        # it is in the education block either way.
        reference = today or date.today()
        verb = "Graduating" if graduation > reference else "Graduated"
        parts.append(f"{verb} {_month_year(graduation)}")

    # Joined with a comma rather than a bullet or a dash: the whole document
    # goes through `_normalize_document_text`, which rewrites every separator in
    # that family to ", " anyway, and the user's rules forbid the dashes.
    return Availability(line=", ".join(parts), explicit=explicit)
