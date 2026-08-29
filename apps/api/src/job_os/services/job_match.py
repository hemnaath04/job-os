"""Score a candidate against an enriched job. No LLM, no network, no I/O.

This is the cheap half of the split described in `schemas/enrichment.py`. Every
fact this needs was extracted once at ingest, so matching is set intersection and
integer arithmetic: one profile against fifty thousand jobs costs zero model
calls and no round trips.

Four axes, which is what the market leader exposes, rolled into one number:

    skills 45  |  experience 25  |  education 15  |  industry 15   = 100

The weights say what this product believes: what you can do matters more than
how long you have been doing it, and both matter more than where you did it.

The deductive model
-------------------
The score starts at 100 and every axis spends its weight on reasons the fit is
not perfect. That direction is deliberate. A ratio built upwards ("you matched 4
of 11 things") cannot say why the other 7 cost what they cost, and a coverage
ratio that divides by whatever the posting happened to name rewards a posting
for being vague. Counting downwards from a perfect match forces every lost point
to name the thing that took it.

So the invariant this module exists to hold is:

    100 + sum(every line's points) == raw_overall

with no residual term anywhere. `test_job_match.py` asserts it on real fixtures,
and asserts it per axis as well. If a future axis cannot explain a point, the
test fails rather than the number quietly drifting.

Why that invariant is the differentiator
----------------------------------------
Jobright's own FAQ has an accordion titled "What is a Match Score and How is it
Calculated?" whose answer is not present in the served HTML at all. Their job
payload ships `recommendationScores` as three named features
(`q_seniority_match`, `q_job_skill_match`, `q_industry_match`) with a bare score
against each and nothing that says how any of them was reached. Nobody in this
market explains the number. A breakdown where every point traces to a named
reason and an inspectable JD phrase is a thing users can act on, and it is
cheap to produce once the facts are precomputed.

Bonuses, blockers and points
----------------------------
Bonuses are a fifth pseudo-axis of weight zero, so they can recover points a
deduction took without breaking the arithmetic. Blockers are NOT points. A
posting that will not sponsor a visa is not a worse match for a candidate who
needs sponsorship, it is an impossible one, and averaging a binary fact into a
ranking number is how a 90% match turns out to be unapplyable. They travel
beside the score, in `blockers`.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from fractions import Fraction
from typing import Literal

from job_os.db.models.profile import ProfileFact
from job_os.schemas.enrichment import (
    DEGREE_ORDER,
    ENRICHMENT_SCHEMA_VERSION,
    SENIORITY_ORDER,
    DegreeLevel,
    JobEnrichment,
    Seniority,
    SkillRequirement,
    canonical_skill,
)
from job_os.schemas.me import WorkEligibility
from job_os.services.skill_match import known_skill_terms, satisfies

Axis = Literal["skills", "experience", "education", "industry", "bonus"]

#: Every skill name the alias table knows, longest first so a bullet naming
#: "amazon web services" is credited with that rather than stopping at a
#: shorter overlapping term. Built once at import; this file does no I/O.
_KNOWN_SKILL_TERMS = known_skill_terms()

BASE_SCORE = 100
AXIS_WEIGHTS: dict[Axis, int] = {
    "skills": 45,
    "experience": 25,
    "education": 15,
    "industry": 15,
    # Weight zero: bonuses recover points rather than adding a sixth budget.
    "bonus": 0,
}

# How much one requirement counts, before it is scaled into the axis budget.
# Multiplying required asks by two is what stops "missing Kubernetes" and
# "missing Jira, mentioned once under nice-to-have" costing the same, which is
# the failure mode of every flat coverage ratio.
REQUIRED_MULTIPLIER = 2
PREFERRED_MULTIPLIER = 1

# The floor on the skills denominator, and the direct port of
# `fit-score.ts`'s MIN_SCORE_DENOMINATOR of 8.
#
# The failure it exists to prevent is documented and real: a mechanical
# engineering internship named only three skills, a backend profile matched all
# three, and it scored 100% and outranked roles that were genuinely a fit. A raw
# matched-over-named ratio pays a posting for saying little.
#
# 32 is that same floor of eight requirements expressed in this module's
# currency: eight required asks of ordinary importance, at 2 x 2 each.
#
# The improvement over the TypeScript version is not the floor, which is
# identical in effect, but that the shortfall becomes a NAMED line item. The old
# scorer silently returned 37% for a three-skill posting with nothing to explain
# the missing 63. This one says "minus 28: the posting names only 3 skills, too
# thin to judge a match on", which is the same arithmetic and an answerable
# statement.
MIN_SKILL_WEIGHT_POOL = 32

# Below this many named requirements, the result is reported as low confidence.
# Mirrors `fit-score.ts`'s MIN_TERMS_FOR_CONFIDENCE.
MIN_SKILLS_FOR_CONFIDENCE = 3

# Experience. Eight points a year short reaches the full 25 at just over three
# years, which is about where "stretch role" becomes "different role".
EXPERIENCE_POINTS_PER_YEAR_SHORT = 8
# Used only when the posting states no number, so the seniority band is all
# there is. One band is roughly two to three years.
EXPERIENCE_POINTS_PER_BAND_SHORT = 9
# Being well past the band costs something, because a staff engineer shown an
# internship is being shown a bad result, but much less than falling short.
EXPERIENCE_POINTS_PER_BAND_OVER = 4
# A posting that states neither years nor seniority has told us nothing about
# the axis. Deducting nothing would rank vagueness above a stated requirement
# the candidate meets, so it costs a little, and says so.
EXPERIENCE_UNKNOWN_DEDUCTION = 8
# A people-manager role read against a profile with no management history.
EXPERIENCE_MANAGER_MISMATCH = 6

# Education.
EDUCATION_POINTS_PER_LEVEL_SHORT = 7
# A degree in progress against a posting that requires it completed. Most of the
# credit, because "graduating in May" is a scheduling fact rather than a gap, and
# neither reference handles this case at all.
EDUCATION_IN_PROGRESS_DEDUCTION = 4
EDUCATION_PREFERRED_MISS = 3
EDUCATION_FIELD_MISS = 4
# A posting open only to current undergraduates, read by someone already
# enrolled in a higher degree. Costs a level, the same as being a level short
# of the requirement, because it is the same kind of fact seen from the other
# side: the posting gates on a degree status this candidate does not have.
#
# Not the whole axis. The wording is a strong signal and not a certainty --
# some teams take a master's student who applies to an undergraduate
# programme -- so this should cost a rank, not the result.
EDUCATION_UNDERGRAD_ONLY_DEDUCTION = EDUCATION_POINTS_PER_LEVEL_SHORT

# Industry. A full miss costs the whole axis, which is what Jobright does: the
# real payload carries `industryMatchingScores: []` and scores the industry
# feature 0 for a candidate with no matching background. The axis is only 15% of
# the total precisely because industry transfers well in software, so a zero here
# is survivable rather than disqualifying.

# Bonuses, and the ceiling on all of them together.
BONUS_CAP = 15
BONUS_TITLE_EXACT = 8
BONUS_TITLE_PARTIAL = 4
BONUS_SPONSORSHIP_OFFERED = 6
BONUS_COMMITMENT_MATCH = 4
BONUS_WORKPLACE_MATCH = 3

# Token overlap at which two titles count as the same kind of role.
TITLE_PARTIAL_THRESHOLD = Fraction(1, 2)

_TITLE_NOISE = frozenset(
    {
        "senior",
        "staff",
        "principal",
        "lead",
        "junior",
        "entry",
        "level",
        "i",
        "ii",
        "iii",
        "iv",
        "sr",
        "jr",
        "the",
        "and",
        "of",
        "a",
        "an",
        "intern",
        "internship",
        "co",
        "op",
        "new",
        "grad",
        "graduate",
    }
)


@dataclass(frozen=True)
class CandidateProfile:
    """The user-dependent half of a match, in the form the scorer needs.

    Deliberately plain data with no ORM behind it. It is built once per scoring
    run from the profile facts and reused across every job, which is the other
    half of why scoring a whole corpus is free.
    """

    # Canonicalized on construction, so a caller cannot accidentally pass raw
    # surface forms and get silent misses.
    skills: frozenset[str] = frozenset()
    years_experience: float = 0.0
    seniority: Seniority = "unknown"
    highest_degree: DegreeLevel = "none"
    in_progress_degree: DegreeLevel | None = None
    degree_fields: frozenset[str] = frozenset()
    industries: frozenset[str] = frozenset()
    target_titles: tuple[str, ...] = ()
    has_management_experience: bool = False
    needs_visa_sponsorship: bool = False
    has_security_clearance: bool = False
    prefers_remote: bool = False
    wants_commitment: frozenset[str] = frozenset()

    @staticmethod
    def build(
        *,
        skills: list[str] | None = None,
        years_experience: float = 0.0,
        seniority: Seniority = "unknown",
        highest_degree: DegreeLevel = "none",
        in_progress_degree: DegreeLevel | None = None,
        degree_fields: list[str] | None = None,
        industries: list[str] | None = None,
        target_titles: list[str] | None = None,
        has_management_experience: bool = False,
        needs_visa_sponsorship: bool = False,
        has_security_clearance: bool = False,
        prefers_remote: bool = False,
        wants_commitment: list[str] | None = None,
    ) -> CandidateProfile:
        """Build a profile, canonicalizing every free-text field on the way in.

        The skills and the job's requirements have to pass through the same
        normalizer or the intersection is meaningless, so this is the only
        supported way to construct one from user data.
        """
        return CandidateProfile(
            skills=frozenset(
                canon for raw in (skills or []) if (canon := canonical_skill(raw))
            ),
            years_experience=years_experience,
            seniority=seniority,
            highest_degree=highest_degree,
            in_progress_degree=in_progress_degree,
            degree_fields=frozenset(
                canon for raw in (degree_fields or []) if (canon := canonical_skill(raw))
            ),
            industries=frozenset(
                canon for raw in (industries or []) if (canon := canonical_skill(raw))
            ),
            target_titles=tuple(target_titles or []),
            has_management_experience=has_management_experience,
            needs_visa_sponsorship=needs_visa_sponsorship,
            has_security_clearance=has_security_clearance,
            prefers_remote=prefers_remote,
            wants_commitment=frozenset(wants_commitment or []),
        )


@dataclass(frozen=True)
class ScoreLine:
    """One named reason the score is what it is, worth exactly `points`.

    `points` is signed: negative took points away, positive gave them back, zero
    explains why an axis lost nothing (which is a reason a user asks about too).

    `evidence` carries the JD phrase the requirement came from, so the breakdown
    can quote the posting rather than assert against it. That is the difference
    between "missing Kubernetes" and "missing Kubernetes, from 'hands-on
    experience with containers and orchestration platforms such as Docker,
    Kubernetes, or AWS EKS'", and the second one is actionable.
    """

    axis: Axis
    points: int
    reason: str
    detail: str
    subject: str | None = None
    evidence: str | None = None


@dataclass(frozen=True)
class AxisScore:
    axis: Axis
    weight: int
    points: int
    lines: tuple[ScoreLine, ...]

    @property
    def percent(self) -> int:
        """The axis as a 0-100 figure, for a per-axis bar in the UI."""
        if self.weight == 0:
            return 0
        return round(self.points * 100 / self.weight)


@dataclass(frozen=True)
class MatchScore:
    overall: int
    # The pre-clamp total. Reported because a clamp is a thing that happened to
    # the number, and hiding it would break the promise that the breakdown
    # explains the score.
    raw_overall: int
    axes: tuple[AxisScore, ...]
    lines: tuple[ScoreLine, ...]
    confidence: Literal["high", "low"]
    confidence_reasons: tuple[str, ...]
    blockers: tuple[ScoreLine, ...]
    matched_skills: tuple[str, ...]
    missing_skills: tuple[str, ...]
    schema_version: int = ENRICHMENT_SCHEMA_VERSION

    @property
    def is_eligible(self) -> bool:
        return not self.blockers

    def top_reasons(self, limit: int = 5) -> tuple[ScoreLine, ...]:
        """The lines that moved the score most, for a card with no room.

        `lines` stays the complete account, because that is what the arithmetic
        is checked against. This is the render-time shortlist, kept here so
        every consumer shortens the breakdown the same way rather than each one
        picking its own and disagreeing about what mattered.
        """
        ranked = sorted(
            (line for line in self.lines if line.points != 0),
            key=lambda line: (-abs(line.points), line.axis, line.detail),
        )
        return tuple(ranked[:limit])

    def axis(self, name: Axis) -> AxisScore:
        for entry in self.axes:
            if entry.axis == name:
                return entry
        raise KeyError(name)


def _note_truncation(detail: str, wanted: int, taken: int) -> str:
    """Say so when an axis ran out of budget mid-reason.

    Without this, a bonus worth 4 that only had 1 point of headroom left reads
    as a reason worth 1, which understates it and makes the breakdown look
    inconsistent between two jobs where the same fact was worth different
    amounts. The score is still `taken`; the sentence explains why.
    """
    if taken >= wanted:
        return detail
    return f"{detail} (worth {wanted}, capped at {taken} by this axis's remaining budget)"


def _readable_list(items: list[str], limit: int = 3) -> str:
    """Join names for a sentence a person reads, without running to 14 of them.

    The Cisco record in the evidence set carries fourteen company categories.
    Naming all of them in one line item turns the breakdown back into the wall
    of text the breakdown exists to replace.
    """
    unique = sorted({item for item in items if item})
    if len(unique) <= limit:
        return ", ".join(unique)
    remainder = len(unique) - limit
    return f"{', '.join(unique[:limit])} and {remainder} more"


@dataclass
class _Budget:
    """An axis's points, spent down so an axis can never overdraw its weight.

    Capping this way rather than clamping a total afterwards is what keeps the
    arithmetic honest. A post-hoc clamp would leave line items that sum to more
    than the axis actually lost, so the breakdown would no longer add up to the
    score. Here a deduction that does not fit is truncated to what is left, and
    a deduction with nothing left to take is dropped instead of recorded as a
    reason that cost nothing.
    """

    axis: Axis
    remaining: int
    lines: list[ScoreLine] = field(default_factory=list)

    def deduct(
        self,
        amount: int,
        reason: str,
        detail: str,
        *,
        subject: str | None = None,
        evidence: str | None = None,
    ) -> None:
        taken = min(amount, self.remaining)
        if taken <= 0:
            return
        self.remaining -= taken
        self.lines.append(
            ScoreLine(
                axis=self.axis,
                points=-taken,
                reason=reason,
                detail=_note_truncation(detail, amount, taken),
                subject=subject,
                evidence=evidence,
            )
        )

    def credit(
        self,
        amount: int,
        reason: str,
        detail: str,
        *,
        subject: str | None = None,
    ) -> None:
        taken = min(amount, self.remaining)
        if taken <= 0:
            return
        self.remaining -= taken
        self.lines.append(
            ScoreLine(
                axis=self.axis,
                points=taken,
                reason=reason,
                detail=_note_truncation(detail, amount, taken),
                subject=subject,
            )
        )

    def note(self, reason: str, detail: str, *, subject: str | None = None) -> None:
        """Record a zero-point reason, for why an axis lost nothing.

        "This posting states no degree requirement, so nothing was deducted" is
        an answer to the question the breakdown exists to answer, and it costs
        the arithmetic nothing because zero sums to zero.
        """
        self.lines.append(
            ScoreLine(axis=self.axis, points=0, reason=reason, detail=detail, subject=subject)
        )

    def finish(self, weight: int) -> AxisScore:
        points = weight + sum(line.points for line in self.lines)
        return AxisScore(
            axis=self.axis, weight=weight, points=points, lines=tuple(self.lines)
        )


def score_job(job: JobEnrichment, candidate: CandidateProfile) -> MatchScore:
    """Score one enriched job against one candidate. Pure, total, deterministic.

    Same inputs always give the same output: no clock, no randomness, no
    dict-ordering dependence (every collection this walks is a list from the
    document or a sorted set). That is what makes the number cacheable and what
    makes "why did this change" answerable.
    """
    skills_axis, matched, missing = _score_skills(job, candidate)
    axes = (
        skills_axis,
        _score_experience(job, candidate),
        _score_education(job, candidate),
        _score_industry(job, candidate),
        _score_bonuses(job, candidate),
    )
    lines = tuple(line for axis in axes for line in axis.lines)
    raw_overall = BASE_SCORE + sum(line.points for line in lines)
    confidence, reasons = _confidence(job)
    return MatchScore(
        overall=max(0, min(100, raw_overall)),
        raw_overall=raw_overall,
        axes=axes,
        lines=lines,
        confidence=confidence,
        confidence_reasons=reasons,
        blockers=_blockers(job, candidate),
        matched_skills=matched,
        missing_skills=missing,
    )


def _requirement_weight(requirement: SkillRequirement) -> int:
    multiplier = (
        REQUIRED_MULTIPLIER if requirement.necessity == "required" else PREFERRED_MULTIPLIER
    )
    return requirement.importance * multiplier


def _match_requirement(
    requirement: SkillRequirement, candidate: CandidateProfile
) -> str | None:
    """The candidate skill that satisfies this requirement, or None.

    The rule itself is `skill_match.satisfies`, shared with the tailoring
    coverage pass so the two cannot drift apart. See that module for why each
    direction is allowed, and why the more-specific-candidate direction is gated
    on a multi-token requirement.
    """
    if requirement.canonical in candidate.skills:
        return requirement.canonical
    # Longest candidate skill wins, so "google cloud" beats "cloud" on a
    # requirement that contains both and the attribution names the better one.
    best: str | None = None
    for skill in candidate.skills:
        if satisfies(requirement.canonical, skill) and (best is None or len(skill) > len(best)):
            best = skill
    return best


def _score_skills(
    job: JobEnrichment, candidate: CandidateProfile
) -> tuple[AxisScore, tuple[str, ...], tuple[str, ...]]:
    """The skills axis, plus the matched and missing lists the card renders.

    The deduction for each missed requirement is proportional to that
    requirement's weight in the pool, and the whole set is allocated with
    largest remainder so the integers sum to exactly the axis total. Without
    that, per-item rounding would leave a point or two unattributed, which is
    the residual this module promises not to have.
    """
    weight = AXIS_WEIGHTS["skills"]
    budget = _Budget("skills", weight)
    requirements = job.skills

    pool = sum(_requirement_weight(item) for item in requirements)
    effective_pool = max(pool, MIN_SKILL_WEIGHT_POOL)

    matched: list[str] = []
    missing: list[SkillRequirement] = []
    for item in requirements:
        if _match_requirement(item, candidate) is not None:
            matched.append(item.skill)
        else:
            missing.append(item)

    # Shares to allocate: one per missed requirement, plus one for the gap
    # between what the posting named and the floor. The second is the thin
    # posting guard, and giving it a share of the same allocation is what turns
    # it from an invisible denominator into a line the user can read.
    shares: list[tuple[Fraction, str]] = [
        (Fraction(_requirement_weight(item), effective_pool), "missing") for item in missing
    ]
    thin_weight = effective_pool - pool
    if thin_weight > 0:
        shares.append((Fraction(thin_weight, effective_pool), "thin"))

    total_deduction = round(weight * sum(share for share, _ in shares))
    amounts = _allocate(total_deduction, [share for share, _ in shares])

    missing_index = 0
    for amount, (_, kind) in zip(amounts, shares, strict=True):
        if kind == "thin":
            budget.deduct(
                amount,
                "posting_too_thin_to_judge",
                _thin_detail(len(requirements)),
            )
            continue
        item = missing[missing_index]
        missing_index += 1
        necessity = " (required)" if item.necessity == "required" else " (preferred)"
        if amount == 0:
            # A dense posting spreads 45 points across so many requirements that
            # some individually round to less than one. Recording them at zero
            # rather than dropping them keeps the breakdown a complete account of
            # what the posting asked for. It costs the arithmetic nothing, and
            # the alternative is a missing skill the user can see on the card
            # with no corresponding line explaining it.
            budget.note(
                "skill_missing_below_a_point",
                f"missing {item.skill}{necessity}, worth under a point across the "
                f"{len(requirements)} skills this posting names",
                subject=item.skill,
            )
            continue
        budget.deduct(
            amount,
            "skill_missing",
            f"missing {item.skill}{necessity}",
            subject=item.skill,
            evidence=item.evidence,
        )

    if not budget.lines:
        budget.note(
            "all_named_skills_matched",
            f"profile covers all {len(requirements)} skills this posting names",
        )

    return (
        budget.finish(weight),
        tuple(matched),
        tuple(item.skill for item in missing),
    )


def _thin_detail(named: int) -> str:
    if named == 0:
        return "the posting names no specific skills, so a skills match cannot be judged"
    return (
        f"the posting names only {named} "
        f"{'skill' if named == 1 else 'skills'}, too thin to judge a match on"
    )


def _allocate(total: int, shares: list[Fraction]) -> list[int]:
    """Split `total` across `shares` as integers that sum to exactly `total`.

    Largest remainder. Each share gets its floor, then the leftover units go to
    the largest fractional parts, biggest first, with the earlier index winning
    a tie so the result does not depend on sort stability.

    This exists so the per-line integers add up to the axis total exactly. The
    alternative, rounding each line independently, drifts by a point or two and
    that drift is precisely the unexplained residual the whole design is built
    to avoid.
    """
    if not shares or total <= 0:
        return [0] * len(shares)
    denominator = sum(shares)
    if denominator == 0:
        return [0] * len(shares)

    exact = [Fraction(total) * share / denominator for share in shares]
    floors = [int(value) for value in exact]
    leftover = total - sum(floors)
    if leftover > 0:
        order = sorted(
            range(len(shares)),
            key=lambda index: (-(exact[index] - floors[index]), index),
        )
        for index in order[:leftover]:
            floors[index] += 1
    return floors


def _score_experience(job: JobEnrichment, candidate: CandidateProfile) -> AxisScore:
    """Years when the posting states years, seniority bands when it does not.

    Keeping those two paths separate is what `years_experience_mentioned` is
    for. Without it, "no experience required" and "the posting never said" are
    the same null, and a scorer that treats them alike either punishes every
    silent posting or lets every silent posting through.
    """
    weight = AXIS_WEIGHTS["experience"]
    budget = _Budget("experience", weight)

    if job.years_experience_mentioned and job.min_years_experience is not None:
        _deduct_years(budget, job, candidate)
    elif job.seniority_level != "unknown":
        _deduct_bands(budget, job, candidate)
    else:
        budget.deduct(
            EXPERIENCE_UNKNOWN_DEDUCTION,
            "experience_requirement_unstated",
            "the posting states neither years of experience nor a seniority level",
        )

    if job.role_type == "people-manager" and not candidate.has_management_experience:
        budget.deduct(
            EXPERIENCE_MANAGER_MISMATCH,
            "management_experience_missing",
            "this is a people-manager role and the profile shows no management experience",
        )

    if not budget.lines:
        budget.note(
            "experience_requirement_met",
            _experience_met_detail(job, candidate),
        )
    return budget.finish(weight)


def _experience_met_detail(job: JobEnrichment, candidate: CandidateProfile) -> str:
    if job.years_experience_mentioned and job.min_years_experience is not None:
        return (
            f"profile has {_years(candidate.years_experience)} against the "
            f"{job.min_years_experience} required"
        )
    return f"profile is at or above the {job.seniority_level} level this posting asks for"


def _deduct_years(budget: _Budget, job: JobEnrichment, candidate: CandidateProfile) -> None:
    minimum = job.min_years_experience or 0
    shortfall = minimum - candidate.years_experience
    if shortfall > 0:
        budget.deduct(
            round(shortfall * EXPERIENCE_POINTS_PER_YEAR_SHORT),
            "experience_short",
            f"{_years(shortfall)} short of the {minimum} this posting requires",
        )
        return
    # Overqualification, judged against the top of the stated range so a posting
    # asking for "3 to 5 years" does not penalize someone with 6.
    ceiling = job.max_years_experience
    if ceiling is not None and candidate.years_experience > ceiling + 3:
        budget.deduct(
            EXPERIENCE_POINTS_PER_BAND_OVER,
            "experience_over",
            f"profile has {_years(candidate.years_experience)} against a "
            f"stated ceiling of {ceiling}",
        )


def _deduct_bands(budget: _Budget, job: JobEnrichment, candidate: CandidateProfile) -> None:
    job_band = _band_index(job.seniority_level)
    candidate_band = _band_index(candidate.seniority)
    if job_band is None:
        return
    if candidate_band is None:
        # The posting was specific and the profile is not. That is a gap in what
        # we know, and it belongs to this axis rather than being waved through.
        budget.deduct(
            EXPERIENCE_UNKNOWN_DEDUCTION,
            "candidate_seniority_unknown",
            f"the posting asks for {job.seniority_level} and the profile does not "
            "state a level",
        )
        return
    gap = job_band - candidate_band
    if gap > 0:
        budget.deduct(
            gap * EXPERIENCE_POINTS_PER_BAND_SHORT,
            "seniority_short",
            f"the posting asks for {job.seniority_level} and the profile is at "
            f"{candidate.seniority}, {gap} {'band' if gap == 1 else 'bands'} below",
        )
    elif gap < 0:
        budget.deduct(
            -gap * EXPERIENCE_POINTS_PER_BAND_OVER,
            "seniority_over",
            f"the posting is {job.seniority_level} and the profile is at "
            f"{candidate.seniority}, {-gap} {'band' if gap == -1 else 'bands'} above",
        )


def _band_index(level: str) -> int | None:
    try:
        return SENIORITY_ORDER.index(level)
    except ValueError:
        return None


def _years(value: float) -> str:
    whole = int(value) if float(value).is_integer() else value
    return f"{whole} {'year' if whole == 1 else 'years'}"


def _degree_index(level: DegreeLevel | None) -> int:
    if level is None:
        return 0
    try:
        return DEGREE_ORDER.index(level)
    except ValueError:
        return 0


def _score_education(job: JobEnrichment, candidate: CandidateProfile) -> AxisScore:
    """Degrees per level, with the in-progress case treated as the near-miss it is.

    A candidate part way through the master's a posting requires is not in the
    same position as one who never enrolled, and a posting that says "currently
    pursuing" is asking for exactly that candidate. Neither reference models
    this, and for a product whose users are largely students it is most of the
    axis.
    """
    weight = AXIS_WEIGHTS["education"]
    budget = _Budget("education", weight)
    education = job.education

    # The floor, not the ceiling. A posting naming several degree levels is
    # listing alternatives, and scoring against the tallest one turns "pursuing
    # an undergraduate or graduate degree" into a demand for a doctorate.
    required = education.required_floor()
    preferred = education.highest_preferred()
    held = _degree_index(candidate.highest_degree)
    in_progress = _degree_index(candidate.in_progress_degree)

    if required == "none" and preferred == "none" and not education.high_school_required:
        budget.note(
            "education_requirement_unstated",
            "the posting states no degree requirement, so nothing is deducted here",
        )
        return budget.finish(weight)

    required_index = _degree_index(required)
    # Checked before the "requirement met" branch, because this candidate meets
    # it. That is the whole failure: an undergraduate-only posting asks for a
    # bachelors, someone in a master's holds one, and the axis called it a match
    # and scored 15 out of 15 on the one thing the posting actually gates on.
    #
    # This fires on the candidate, not on the posting. Someone currently in a
    # bachelors is exactly who the posting wants and loses nothing here, which
    # is what keeps it from becoming a blanket penalty on internships.
    if education.undergraduate_only() and in_progress > _degree_index("bachelors"):
        budget.deduct(
            EDUCATION_UNDERGRAD_ONLY_DEDUCTION,
            "undergraduate_only_posting",
            f"the posting is open to students pursuing a bachelors and the "
            f"profile is enrolled in a {candidate.in_progress_degree}",
        )
    elif held >= required_index:
        budget.note(
            "education_requirement_met",
            f"profile holds a {candidate.highest_degree} against the {required} required",
        )
    elif education.enrolled_student_ok and in_progress >= required_index:
        budget.note(
            "enrolled_student_accepted",
            f"the posting accepts current students and the profile is enrolled in a "
            f"{candidate.in_progress_degree}",
        )
    elif in_progress >= required_index:
        budget.deduct(
            EDUCATION_IN_PROGRESS_DEDUCTION,
            "degree_in_progress",
            f"the posting requires a completed {required} and the profile's "
            f"{candidate.in_progress_degree} is in progress",
        )
    else:
        levels_short = required_index - max(held, in_progress)
        budget.deduct(
            levels_short * EDUCATION_POINTS_PER_LEVEL_SHORT,
            "degree_short",
            f"the posting requires a {required} and the profile's highest is "
            f"{candidate.highest_degree}",
        )

    if preferred != "none" and max(held, in_progress) < _degree_index(preferred):
        budget.deduct(
            EDUCATION_PREFERRED_MISS,
            "preferred_degree_missing",
            f"a {preferred} is preferred and the profile does not hold one",
        )

    wanted_fields = {canonical_skill(item) for item in education.all_fields_of_study()}
    wanted_fields.discard("")
    if wanted_fields and not (wanted_fields & candidate.degree_fields):
        readable = _readable_list(education.all_fields_of_study())
        budget.deduct(
            EDUCATION_FIELD_MISS,
            "field_of_study_mismatch",
            f"the posting names fields of study ({readable}) that the profile's "
            "degrees do not cover",
        )

    return budget.finish(weight)


def _score_industry(job: JobEnrichment, candidate: CandidateProfile) -> AxisScore:
    """Sector and product-domain overlap.

    A full miss costs the whole axis, which is what the market leader does. The
    axis is only 15 points precisely because industry transfers well in
    software: a zero here should cost a good candidate a rank or two, not the
    result.
    """
    weight = AXIS_WEIGHTS["industry"]
    budget = _Budget("industry", weight)

    wanted = {canonical_skill(item) for item in [job.company_industry or "", *job.company_domains]}
    wanted.discard("")
    if not wanted:
        budget.note(
            "industry_unstated",
            "the posting does not identify an industry, so nothing is deducted here",
        )
        return budget.finish(weight)

    readable = _readable_list([job.company_industry or "", *job.company_domains])
    if not candidate.industries:
        # Unknown is not a mismatch. `build_candidate_profile` has no industry
        # field to fill (see its docstring), so this branch fired on EVERY
        # posting that named an industry -- a flat, uncorrectable -7 that put a
        # perfect-fit resume's ceiling at 93 and left the user with a gap no
        # rewrite could close and no explanation of why.
        #
        # A deduction has to mean "the evidence is against you". Silence about
        # an industry is not evidence against a candidate, and this axis already
        # treats a posting that names no industry as costing nothing; a profile
        # that names none is the same absence seen from the other side. A real
        # mismatch -- both sides stated, no overlap -- still costs the axis
        # below, which is the case this deduction was actually written for.
        budget.note(
            "industry_history_unknown",
            f"this role is in {readable}; the profile records no industry history "
            "either way, so this is not counted for or against the fit",
            subject=readable,
        )
        return budget.finish(weight)

    overlap = wanted & candidate.industries
    if overlap:
        budget.note(
            "industry_match",
            f"profile has background in {readable}",
            subject=readable,
        )
    else:
        budget.deduct(
            weight,
            "industry_mismatch",
            f"this role is in {readable} and the profile shows no experience there",
            subject=readable,
        )
    return budget.finish(weight)


def _score_bonuses(job: JobEnrichment, candidate: CandidateProfile) -> AxisScore:
    """Points a deduction took, given back for a reason worth naming.

    Weight zero and capped, so bonuses can lift a score without becoming a
    sixth budget that quietly inflates everything.
    """
    budget = _Budget("bonus", BONUS_CAP)

    title_bonus, title_detail = _title_bonus(job, candidate)
    if title_bonus:
        budget.credit(title_bonus, "title_match", title_detail, subject=job.core_job_title)

    if candidate.needs_visa_sponsorship and job.eligibility.visa_sponsorship == "yes":
        budget.credit(
            BONUS_SPONSORSHIP_OFFERED,
            "sponsorship_offered",
            "the posting states it sponsors visas and the profile needs sponsorship",
        )

    wanted_commitment = candidate.wants_commitment & set(job.commitment)
    if wanted_commitment:
        readable = _readable_list(sorted(wanted_commitment))
        budget.credit(
            BONUS_COMMITMENT_MATCH,
            "commitment_match",
            f"the posting is {readable}, which is what the profile is looking for",
            subject=readable,
        )

    if candidate.prefers_remote and job.workplace.workplace_type == "remote":
        budget.credit(
            BONUS_WORKPLACE_MATCH,
            "workplace_match",
            "the role is remote and the profile prefers remote",
        )

    return budget.finish(AXIS_WEIGHTS["bonus"])


def _title_tokens(title: str) -> frozenset[str]:
    tokens = {token for token in canonical_skill(title).split(" ") if token}
    return frozenset(tokens - _TITLE_NOISE)


def _title_bonus(job: JobEnrichment, candidate: CandidateProfile) -> tuple[int, str]:
    """Exact then partial title overlap, seniority words stripped first.

    "Senior Backend Engineer" and "Backend Engineer" are the same target with a
    different seniority, and the experience axis has already priced the
    seniority. Counting it again here would charge the candidate twice for one
    fact.
    """
    job_tokens = _title_tokens(job.core_job_title)
    if not job_tokens:
        return 0, ""
    best = (0, "")
    for target in candidate.target_titles:
        target_tokens = _title_tokens(target)
        if not target_tokens:
            continue
        if target_tokens == job_tokens:
            return (
                BONUS_TITLE_EXACT,
                f"the title matches a target role on the profile ({target})",
            )
        shared = target_tokens & job_tokens
        overlap = Fraction(len(shared), len(job_tokens))
        if overlap >= TITLE_PARTIAL_THRESHOLD and best[0] < BONUS_TITLE_PARTIAL:
            best = (
                BONUS_TITLE_PARTIAL,
                f"the title overlaps a target role on the profile ({target})",
            )
    return best


def _blockers(job: JobEnrichment, candidate: CandidateProfile) -> tuple[ScoreLine, ...]:
    """Facts that make an application impossible rather than merely unlikely.

    Reported beside the score and never folded into it. A candidate who needs
    sponsorship looking at a posting that refuses to sponsor does not want a
    slightly lower number, they want to be told. Folding a binary into a ranking
    is how a 90% match turns out to be unapplyable.
    """
    found: list[ScoreLine] = []
    eligibility = job.eligibility
    if candidate.needs_visa_sponsorship:
        if eligibility.visa_sponsorship == "no":
            found.append(
                ScoreLine(
                    axis="bonus",
                    points=0,
                    reason="no_visa_sponsorship",
                    detail="the posting states it does not sponsor visas",
                )
            )
        if eligibility.citizenship_required:
            found.append(
                ScoreLine(
                    axis="bonus",
                    points=0,
                    reason="citizenship_required",
                    detail="the posting requires citizenship",
                )
            )
        elif eligibility.work_authorization_required:
            found.append(
                ScoreLine(
                    axis="bonus",
                    points=0,
                    reason="work_authorization_required",
                    detail="the posting requires existing work authorization",
                )
            )
    if eligibility.security_clearance == "required" and not candidate.has_security_clearance:
        found.append(
            ScoreLine(
                axis="bonus",
                points=0,
                reason="security_clearance_required",
                detail="the posting requires a security clearance the profile does not hold",
            )
        )
    return tuple(found)


def _confidence(job: JobEnrichment) -> tuple[Literal["high", "low"], tuple[str, ...]]:
    """Whether the number deserves to be shown as a number.

    Low confidence is not a low score. It says the posting or the extraction did
    not give us enough to judge, which is a different message to the user and
    should be a different presentation: "not enough detail to score" rather than
    a confident 43%.
    """
    reasons: list[str] = []
    if job.extraction_gaps:
        reasons.append(f"enrichment incomplete: {', '.join(job.extraction_gaps)}")
    if len(job.skills) < MIN_SKILLS_FOR_CONFIDENCE:
        reasons.append(
            f"the posting names only {len(job.skills)} skills, "
            f"fewer than the {MIN_SKILLS_FOR_CONFIDENCE} needed to judge a match"
        )
    if job.schema_version != ENRICHMENT_SCHEMA_VERSION:
        reasons.append(
            f"enriched against schema v{job.schema_version}, "
            f"current is v{ENRICHMENT_SCHEMA_VERSION}"
        )
    return ("low" if reasons else "high"), tuple(reasons)


def explain(score: MatchScore) -> list[str]:
    """The breakdown as readable lines, in the shape a card renders.

    Exists so the attribution has one canonical rendering rather than each
    consumer inventing its own and drifting from the arithmetic.
    """
    out = [f"{score.overall} overall, starting from {BASE_SCORE} and accounting for:"]
    for axis in score.axes:
        if axis.axis == "bonus" and not axis.lines:
            continue
        out.append(f"  {axis.axis}: {axis.points}/{axis.weight}")
        for line in axis.lines:
            sign = "+" if line.points > 0 else ""
            prefix = f"{sign}{line.points}" if line.points else "note"
            out.append(f"    {prefix}: {line.detail}")
    if score.raw_overall != score.overall:
        out.append(f"  clamped from {score.raw_overall} to the 0-100 range")
    for blocker in score.blockers:
        out.append(f"  blocker: {blocker.detail}")
    return out


# Substring match against a JSON-Resume `studyType` (free text: "Bachelor of
# Science", "BS", "Master's", "MBA", ...), checked longest-keyword-first so
# "master of business administration" does not fall through to a shorter
# false positive. Ordered by degree level otherwise, low to high.
_DEGREE_KEYWORDS: tuple[tuple[str, DegreeLevel], ...] = (
    ("ged", "high-school"),
    ("high school", "high-school"),
    ("associate", "associates"),
    ("bachelor", "bachelors"),
    ("b.s", "bachelors"),
    ("b.a", "bachelors"),
    ("mba", "masters"),
    ("master", "masters"),
    ("m.s", "masters"),
    ("m.a", "masters"),
    ("phd", "doctorate"),
    ("ph.d", "doctorate"),
    ("doctorate", "doctorate"),
    ("doctoral", "doctorate"),
)


def _degree_level_from_text(text: str | None) -> DegreeLevel:
    if not text:
        return "none"
    lowered = text.lower()
    for keyword, level in _DEGREE_KEYWORDS:
        if keyword in lowered:
            return level
    return "none"


def _seniority_from_years(years: float) -> Seniority:
    """A rough band from total experience. Used only when no seniority is
    tracked anywhere else in today's profile data model -- see
    `build_candidate_profile`."""
    if years < 1:
        return "intern"
    if years < 2:
        return "new-grad"
    if years < 5:
        return "mid"
    if years < 8:
        return "senior"
    if years < 12:
        return "staff"
    return "principal"


_MANAGEMENT_TITLE_TERMS = ("manager", "lead", "head of", "director", "chief", "vp ", "vp,")


def _skill_terms_in(text: str) -> list[str]:
    """Skill names the alias table already knows, as written in this text.

    Word-boundary matched rather than substring: "Go" must not be found inside
    "MongoDB", and "R" must not be found inside every sentence. The same lesson
    `tailor._mentions` records, applied to the scorer's side of the match.
    """
    if not text:
        return []
    haystack = text.casefold()
    return [term for term in _KNOWN_SKILL_TERMS if _mentions_term(haystack, term)]


def _mentions_term(haystack: str, term: str) -> bool:
    return bool(re.search(rf"(?<!\w){re.escape(term)}(?!\w)", haystack))


def build_candidate_profile(
    facts: Sequence[ProfileFact],
    *,
    eligibility: WorkEligibility | None = None,
) -> CandidateProfile:
    """The only supported way to turn a user's profile facts into a
    `CandidateProfile`, so every caller canonicalizes and degrades the same way.

    Best-effort by necessity: today's profile data model has no field for
    industries, target titles, remote preference, or desired commitment type,
    so those stay at `CandidateProfile.build`'s safe defaults. That is not a
    bug to fix here -- `score_job` already treats an absent signal as "no
    opinion" rather than "no", which is what lets the industry axis and the
    bonus lines degrade gracefully instead of inventing a claim about the
    candidate. Only `verified` facts count, the same rule generated resumes
    already follow: an unconfirmed draft is not something to score a
    candidate's fit on.

    `eligibility` is the exception, and the reason it is a separate argument
    rather than a fact: it is a statement the user makes about themselves in
    Settings, not something extractable from their career history. Until it
    was passed, `needs_visa_sponsorship` and `has_security_clearance` were
    always at their defaults, which meant every blocker in `_eligibility_lines`
    was unreachable code for every real account -- a whole eligibility system
    that could not fire.

    None keeps that old behaviour exactly, which is what an account that has
    never opened Settings deserves: no blockers claimed on its behalf.
    """
    verified = [f for f in facts if f.verified]
    # `needs_future_sponsorship`, not "is on a visa". The blocker this feeds
    # asks whether the employer would have to file something, and a candidate
    # who can start now under CPT but needs an H-1B later still answers yes to
    # a full-time posting. The narrower, internship-only case ("this role does
    # not sponsor, but CPT sidesteps it") is not expressible in this boolean at
    # all, and is handled by `eligibility_gate`, which can see the posting's
    # commitment type. This stays the conservative reading.
    needs_sponsorship = bool(eligibility and eligibility.needs_future_sponsorship)
    has_clearance = bool(eligibility and eligibility.clearance_eligible)

    skills: list[str] = []
    for fact in verified:
        if fact.kind == "skill" and fact.title:
            skills.append(fact.title)
        if fact.kind in ("experience", "project"):
            skills.extend(str(k) for k in (fact.payload or {}).get("keywords") or [])
        # The bullets are where the work is actually described, and until now
        # this function could not see them: a fact whose bullet says "built the
        # retrieval service in FastAPI" contributed FastAPI to the resume the
        # tailor writes and nothing at all to the score, so the same vault read
        # as strong on the page and thin in the ranking. The tailoring coverage
        # pass has always searched bullet text; this is the scorer catching up.
        #
        # Only the alias table's own surface forms are harvested, not every word
        # in the bullet. A bullet is prose, and crediting the candidate with
        # every noun in it would inflate every score at once -- "reduced cost"
        # is not the skill "cost". Matching against known skill names keeps this
        # to terms someone already decided were skills.
        #
        # `bullets` is lazy="selectin" on the model, so it is already loaded
        # here and this adds no query.
        for bullet in fact.bullets:
            skills.extend(_skill_terms_in(bullet.text))

    today = date.today()
    years_experience = 0.0
    has_management = False
    for fact in verified:
        if fact.kind != "experience" or fact.start_date is None:
            continue
        end = fact.end_date or today
        years_experience += max((end - fact.start_date).days / 365.25, 0.0)
        if any(term in (fact.title or "").lower() for term in _MANAGEMENT_TITLE_TERMS):
            has_management = True

    highest_degree: DegreeLevel = "none"
    in_progress_degree: DegreeLevel | None = None
    degree_fields: list[str] = []
    for fact in verified:
        if fact.kind != "education":
            continue
        payload = fact.payload or {}
        level = _degree_level_from_text(payload.get("studyType") or fact.title)
        if level == "none":
            continue
        area = payload.get("area")
        if area:
            degree_fields.append(str(area))
        if fact.end_date is None or fact.end_date > today:
            if in_progress_degree is None or DEGREE_ORDER.index(level) > DEGREE_ORDER.index(
                in_progress_degree
            ):
                in_progress_degree = level
        elif DEGREE_ORDER.index(level) > DEGREE_ORDER.index(highest_degree):
            highest_degree = level

    has_experience = any(f.kind == "experience" for f in verified)
    seniority = _seniority_from_years(years_experience) if has_experience else "unknown"

    return CandidateProfile.build(
        skills=skills,
        years_experience=round(years_experience, 1),
        seniority=seniority,
        highest_degree=highest_degree,
        in_progress_degree=in_progress_degree,
        degree_fields=degree_fields,
        has_management_experience=has_management,
        needs_visa_sponsorship=needs_sponsorship,
        has_security_clearance=has_clearance,
    )
