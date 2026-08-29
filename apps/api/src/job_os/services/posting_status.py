"""Whether the page behind a pasted link is still a job posting at all.

Four rows in one workspace are not job descriptions, and each one arrived the
same way: a link was pasted, a card appeared, and the card never scored. The
text those pages actually returned, verbatim:

    Tesla       139 chars   "Powered and protected by Akamai. Privacy"
    Greenhouse  519 chars   "Enter your email address to continue.
                             Send security code"
    Anthropic  8656 chars   "The job you are looking for is no longer open."
    Disney    17453 chars   "Job Not Found. We are sorry this job post no
                             longer exists."

Two of them say outright that the job is gone. One is a sign-in wall and one is
a bot wall. None of that is a parse failure, and reporting it as one loses the
only useful thing on the page: an answer the user can act on. "This posting has
closed" and "we could not read this posting" send somebody to different places.

Deliberately phrase-matching rather than asking a model. These pages are the
cheapest possible thing to recognise and the most expensive to get wrong, and a
sentence someone can read in the source beats a judgement nobody can audit.

The precision bar is high on purpose: a false positive hides a real job the
user wanted. Every pattern here is a phrase a live posting does not contain,
and `test_posting_status.py` runs the whole set over the real corpus to check
that it flags the four and nothing else.
"""
from __future__ import annotations

import re
from typing import Literal

PostingStatus = Literal["ok", "expired", "sign_in_required", "blocked"]

# Deliberately no "this page is too thin" rule. A first version used a 700
# character floor and flagged two real postings, a 369 character paste and a
# 400 character Datadog listing, alongside the junk. A genuinely contentless
# page already parses to `parse_incomplete`, which is the honest report for it
# and predates this module. This only claims the cases a page states about
# itself.
#
# The wording a job description always has somewhere. Used as a second,
# independent signal for the wall cases below rather than as a rule of its own:
# on its own it also flags that 369 character paste, which is a real posting
# that simply never uses the word "responsibilities".
_JOB_VOCABULARY_RE = re.compile(
    r"(?:"
    r"responsibilit|qualification|requirement"
    r"|what you.{0,10}(?:will|ll) do|we.{0,5}re looking for|you will"
    r"|minimum qualif|preferred qualif|basic qualif"
    r"|experience with|about the (?:role|job|team|position)|job description"
    r")",
    re.I,
)

# The page saying, in its own words, that the job is gone. Every one of these is
# taken from a page that actually returned it. A live posting does not say any
# of them about itself, which is the property that makes this safe.
_EXPIRED_RE = re.compile(
    r"(?:"
    r"job you are looking for is no longer open"
    r"|job (?:post(?:ing)?|req(?:uisition)?) no longer exists"
    r"|this (?:job|position|posting|role) (?:is |has )?(?:no longer|been)"
    r" (?:open|available|filled|closed)"
    r"|no longer accepting applications"
    r"|job not found"
    r"|position has been filled"
    r"|posting has (?:expired|closed)"
    r"|this (?:job|posting) has been removed"
    r")",
    re.I,
)

# An authentication wall. The distinguishing feature is that it asks for
# credentials INSTEAD of showing a job, so it is only consulted on a page too
# thin to hold one: plenty of real postings link to a sign-in somewhere.
_SIGN_IN_RE = re.compile(
    r"(?:"
    r"enter your email address to continue"
    r"|sign in to (?:continue|view|apply)"
    r"|please (?:log ?in|sign in) to"
    r"|create an account to (?:continue|view)"
    r")",
    re.I,
)

# A bot wall or an unrendered shell. Same reasoning as above: only consulted on
# a page with no room for a job description, because a real posting can
# perfectly well be served through Cloudflare.
_BLOCKED_RE = re.compile(
    r"(?:"
    r"powered and protected by"
    r"|enable javascript"
    r"|checking your browser"
    r"|are you a (?:robot|human)"
    r"|access denied"
    r"|request blocked"
    r"|cloudflare"
    r"|akamai"
    r")",
    re.I,
)

_REASONS: dict[str, str] = {
    "expired": "This posting is closed. The page says the job is no longer open.",
    "sign_in_required": "This link needs a sign-in, so the posting could not be read.",
    "blocked": "The site blocked the fetch, so the posting could not be read.",
}


def classify(text: str | None, *, title: str | None = None) -> PostingStatus:
    """What kind of page this is, from its own words.

    Expiry is checked first and at any length, because that is the one a long
    page can still be: Disney's "Job Not Found" arrives with 17KB of site
    furniture around it and would otherwise look substantial.

    The two wall cases need a second signal, because "sign in to apply" and a
    Cloudflare footer both appear on perfectly real postings. They are only
    claimed when the page ALSO contains none of the wording a job description
    always has. Two independent signals, because one of them alone is wrong on
    the corpus and both together are not.
    """
    body = f"{title or ''}\n{text or ''}"
    if _EXPIRED_RE.search(body):
        return "expired"

    if _JOB_VOCABULARY_RE.search(body):
        return "ok"
    if _SIGN_IN_RE.search(body):
        return "sign_in_required"
    if _BLOCKED_RE.search(body):
        return "blocked"
    return "ok"


def reason_for(status: PostingStatus) -> str | None:
    """A sentence for the user, or None when there is nothing wrong."""
    return _REASONS.get(status)


def is_usable(status: PostingStatus) -> bool:
    return status == "ok"
