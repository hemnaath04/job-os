"""Whether this posting is one the candidate could actually be hired for.

Some postings state a requirement no resume can answer. A cleared-only role, a
citizens-only role, a role that says it will not sponsor now or ever. Tailoring
against one of those is not a weak application, it is a rejected one, and the
run costs three sequential model calls and several minutes to produce it.

The distinction this module exists for is the one between REFUSING and
FLAGGING, and it is narrower than it looks:

    "we do not sponsor for this internship"          -> a student on CPT can
                                                        take it. Flag it.
    "must be authorized to work without sponsorship
     now or in the future"                           -> the same student
                                                        cannot. Refuse it.

Those two sentences look alike and mean opposite things. Getting them
backwards in either direction is expensive: refuse the first and the candidate
silently loses a job they were eligible for, with no page and no explanation;
pass the second and they spend a real application on a posting that already
said no.

## What this module does NOT do

It does not decide anybody's immigration status. Every question it asks about
the candidate is answered by the candidate, in Settings, as a stored
`WorkEligibility` (see `job_os.schemas.me`). This compares two sets of stated
facts: what the posting says it requires, and what the user says of themselves.

That is deliberate and it is the only defensible shape. Whether a particular
person qualifies as a "US person" under ITAR, or could obtain a clearance, or
may work on CPT, are legal determinations with real consequences, and a
resume tool inferring them from a degree end-date would be wrong eventually and
silently. So nothing here is derived: the user states it once and this reads it.

## Defaults refuse nothing

Every field of `WorkEligibility` defaults to the reading that lets a posting
through. An account that has never opened Settings gates exactly as it did
before this existed. A wrong refusal is worse than a wrong pass, because a pass
costs a few minutes and a page the user can throw away, while a refusal costs
an application they never learn they could have made.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from job_os.schemas.enrichment import Eligibility, JobEnrichment
from job_os.schemas.me import WorkEligibility

Verdict = Literal["pass", "flag", "refuse"]


@dataclass(frozen=True)
class EligibilityFinding:
    """One clause in the posting the candidate does not meet.

    `reason` is a stable id so the UI and the tests can key off it; `detail` is
    the sentence a person reads. Both are always populated, because a blocker
    the user cannot read is a blocker they will assume is a bug.
    """

    verdict: Verdict
    reason: str
    detail: str


@dataclass(frozen=True)
class EligibilityReading:
    """What the gate concluded, and everything it noticed on the way."""

    verdict: Verdict
    findings: tuple[EligibilityFinding, ...]

    @property
    def refusals(self) -> tuple[EligibilityFinding, ...]:
        return tuple(f for f in self.findings if f.verdict == "refuse")

    @property
    def flags(self) -> tuple[EligibilityFinding, ...]:
        return tuple(f for f in self.findings if f.verdict == "flag")

    def message(self) -> str:
        """The refusal, as one paragraph for `TailorInputError`."""
        reasons = " ".join(f.detail for f in self.refusals)
        return (
            f"{reasons} Tailoring a resume for it would not change that, so "
            "this run was stopped before it started. If the posting has been "
            "misread, open it and check the wording."
        )


# The clause that closes the door on a future petition as well as this hire.
#
# This is the single most important pattern in the file, because it is what
# separates a posting a CPT-eligible student may take from one they may not,
# and the two are worded almost identically. Every alternative here names the
# future explicitly; a posting that only declines to sponsor THIS role matches
# none of them and is flagged rather than refused.
_NO_FUTURE_SPONSORSHIP_RE = re.compile(
    r"(?:"
    r"now or in the future"
    r"|now and in the future"
    r"|without (?:the need for )?(?:current or future|future or current)"
    r"|(?:currently or|currently and) in the future"
    r"|not (?:now|currently),? (?:nor|or) in the future"
    r"|no(?:t|w)? .{0,30}sponsorship .{0,20}(?:now or|or in the) future"
    r"|will not sponsor .{0,40}(?:at any time|ever|in the future)"
    r"|unable to (?:offer|provide) .{0,30}sponsorship .{0,30}future"
    r"|does not and will not sponsor"
    r"|(?:require|requires) .{0,40}not (?:require|need) sponsorship .{0,30}future"
    r")",
    re.I,
)

# Export control stated as a possibility rather than a requirement. A posting
# that says some roles MAY need it has not said this role does, and refusing on
# it would throw away a job over a sentence the employer wrote for their whole
# careers site.
_CONDITIONAL_RE = re.compile(
    r"(?:"
    r"some (?:roles|positions|projects) may"
    r"|may be (?:required|subject)"
    r"|(?:certain|specific) (?:roles|positions|projects)"
    r"|depending on (?:the )?(?:role|project|assignment|program)"
    r"|if (?:required|applicable)"
    r"|where (?:required|applicable)"
    r"|could be required"
    r")",
    re.I,
)

# A posting that is an internship or a co-op. Read off the enrichment's own
# taxonomy rather than guessed from the title, because that field is extracted
# for this purpose and a title is not reliable ("Intern Manager" is not one).
_TRAINING_COMMITMENTS = frozenset({"internship", "co-op"})


def _mentions_future(text: str) -> bool:
    return bool(_NO_FUTURE_SPONSORSHIP_RE.search(text))


def _is_conditional(text: str) -> bool:
    return bool(_CONDITIONAL_RE.search(text))


def evaluate(
    enrichment: JobEnrichment | None,
    candidate: WorkEligibility | None,
    *,
    jd_text: str = "",
) -> EligibilityReading:
    """Compare a posting's stated requirements against the candidate's answers.

    `jd_text` is consulted only for the two distinctions the structured
    `Eligibility` object cannot carry: whether a sponsorship clause reaches
    into the future, and whether an export-control clause is stated flatly or
    as a possibility. Both are about the WORDING of a clause the extractor has
    already found, never about finding one it missed, so a posting whose
    enrichment says nothing is never refused on prose alone.
    """
    if enrichment is None or candidate is None:
        # Nothing stated on one side or the other. Two unknowns are not a
        # blocker; they are a reason to let the run proceed.
        return EligibilityReading("pass", ())

    gates: Eligibility = enrichment.eligibility
    findings: list[EligibilityFinding] = []
    text = jd_text or ""

    # ── 1. Citizenship and clearance. Binary, stated, and not negotiable. ──
    #
    # These two are the only genuine walls, and that is checked rather than
    # assumed. EO 12968 s 3.1(b) grants clearance eligibility "only to employees
    # who are United States citizens", and 32 CFR 117.10(l) adds that
    # non-citizens are not eligible for access on a temporary basis either, so
    # there is no interim route. The one exception, a Limited Access
    # Authorization under 32 CFR 117.10(k), is capped below TOP SECRET and
    # reserved for "rare circumstances" involving unique skills; it is not
    # something an applicant can pursue.
    #
    # Both are refused only when the candidate has actually said they cannot
    # meet them. `clearance_eligible` defaults to False and that default alone
    # must never refuse: "we never asked" and "they cannot" are different
    # answers, and only the second is a reason to stop. `status` being set is
    # what distinguishes them.
    answered = candidate.status is not None

    if gates.citizenship_required and answered and candidate.status not in (
        "us_citizen",
        "permanent_resident",
    ):
        findings.append(
            EligibilityFinding(
                "refuse",
                "citizenship_required",
                "This posting requires US citizenship, which your profile says "
                "you do not hold.",
            )
        )

    if gates.security_clearance == "required" and answered and not candidate.clearance_eligible:
        findings.append(
            EligibilityFinding(
                "refuse",
                "security_clearance_required",
                "This posting requires an active US security clearance, which "
                "your profile says you cannot hold.",
            )
        )
    elif gates.security_clearance == "preferred" and answered and not candidate.clearance_eligible:
        findings.append(
            EligibilityFinding(
                "flag",
                "security_clearance_preferred",
                "This posting prefers a security clearance you cannot hold. "
                "Worth applying to, but expect it to weigh against you.",
            )
        )

    # ── 2. Export control. FLAGGED, never refused. ──
    #
    # This rule was specified as "refuse when stated flatly, flag when
    # conditional" and that is wrong, so it is not what this does.
    #
    # The Justice Department's Immigrant and Employee Rights section says in
    # writing that "the ITAR and the EAR don't contain employment or hiring
    # requirements, so they don't require employers or recruiters ... to limit
    # jobs or recruitment to U.S. citizens or workers with other citizenship or
    # immigration statuses", and lists "don't use the ITAR or the EAR as a
    # reason to limit jobs to candidates with certain citizenships" as a
    # best practice. Postings carry ITAR boilerplate constantly with no
    # citizenship requirement attached to it, which makes this the single
    # highest-risk false-refusal trigger in the file: it would silently discard
    # real jobs over a paragraph the employer's legal team puts on every
    # requisition.
    #
    # The term itself is also not the clean binary the field name suggests.
    # Under ITAR (22 CFR 120.62) a student on a visa is a foreign person. Under
    # the EAR both are true at once: 15 CFR 772.1 makes "any person in the
    # United States" a US person for a specific enumerated list of prohibition
    # sections, while the same student remains a FOREIGN person for
    # deemed-export licensing. A gate that refused on this would be refusing on
    # a question with two correct answers.
    #
    # So it is surfaced and never acted on. A flag costs the user a sentence;
    # a refusal costs them the job.
    if gates.work_authorization_required and not candidate.us_person_for_export_control:
        findings.append(
            EligibilityFinding(
                "flag",
                "export_control_conditional"
                if _is_conditional(text)
                else "export_control_mentioned",
                "This posting mentions US person status for export control. That "
                "is often standard legal text rather than a hiring requirement, "
                "so it is worth asking rather than skipping.",
            )
        )

    # ── 3. Sponsorship. The distinction the whole module is for. ──
    if gates.visa_sponsorship == "no" and candidate.needs_future_sponsorship:
        closes_the_future = _mentions_future(text)
        is_training_role = bool(_TRAINING_COMMITMENTS & set(enrichment.commitment or []))

        if closes_the_future:
            # "Now or in the future" is a statement about the employment
            # relationship, not about this requisition. A candidate who will
            # need a petition later cannot satisfy it however they start.
            #
            # Refused, unlike the export-control case above, because of what
            # the sentence IS: a requirement written about the candidate, which
            # this candidate does not meet. It is employer policy rather than
            # law and an employer may waive it, so this is the weakest of the
            # three refusals; it is still a refusal because the posting stated
            # the requirement in terms the applicant answers on the form.
            findings.append(
                EligibilityFinding(
                    "refuse",
                    "no_sponsorship_now_or_future",
                    "This posting requires authorization to work without "
                    "sponsorship now or in the future, and your profile says you "
                    "will need sponsorship later.",
                )
            )
        elif is_training_role and candidate.cpt_eligible_now:
            # The AMD/Amex shape. The posting declines to sponsor THIS role and
            # says nothing about later; a student authorized to work now can
            # take it without the employer filing anything.
            findings.append(
                EligibilityFinding(
                    "flag",
                    "no_sponsorship_this_role_only",
                    "This posting says it will not sponsor, but does not mention "
                    "future sponsorship, and your profile says you can work now "
                    "without it. Confirm with your school before applying.",
                )
            )
        elif candidate.cpt_eligible_now:
            # Can start now, but this is not a training role, so the clause is
            # more likely to be about the ongoing relationship. Not refused:
            # the posting did not actually say so, and guessing that it meant
            # to is exactly the wrong-refusal this module is careful about.
            findings.append(
                EligibilityFinding(
                    "flag",
                    "no_sponsorship_unclear_term",
                    "This posting says it will not sponsor. It does not say "
                    "whether that covers future sponsorship, which your profile "
                    "says you will eventually need.",
                )
            )
        else:
            findings.append(
                EligibilityFinding(
                    "refuse",
                    "no_sponsorship",
                    "This posting states it does not sponsor visas, and your "
                    "profile says you need sponsorship to work.",
                )
            )

    if any(f.verdict == "refuse" for f in findings):
        verdict: Verdict = "refuse"
    elif findings:
        verdict = "flag"
    else:
        verdict = "pass"
    return EligibilityReading(verdict, tuple(findings))
