"""The browse-time scorer: attribution, determinism, and the sparse-JD guard.

The attribution tests are the important ones. A match score whose parts do not
add up to the whole is worse than no breakdown at all, because it invites the
user to trust an explanation that is not the explanation. These assert the sum
exactly, on real fixtures, per axis and overall, across profiles chosen to hit
every branch.
"""
from __future__ import annotations

import pathlib

import pytest

from job_os.schemas.enrichment import (
    DegreeRequirement,
    EducationRequirements,
    Eligibility,
    JobEnrichment,
    SkillRequirement,
)
from job_os.services.job_match import (
    AXIS_WEIGHTS,
    BASE_SCORE,
    MIN_SKILL_WEIGHT_POOL,
    CandidateProfile,
    MatchScore,
    _allocate,
    explain,
    score_job,
)

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "enrichment"
FIXTURE_NAMES = (
    "cisco_cloud_engineer",
    "worlds_ml_research_intern",
    "vienna_fullstack_engineer",
    "first_tee_play9_intern",
)


def load(name: str) -> JobEnrichment:
    return JobEnrichment.model_validate_json((FIXTURES / f"{name}.json").read_text())


# A profile shaped like this product's actual user: a few years of backend and
# test automation, a bachelors held and a masters in progress, no frontend.
BACKEND_MS_STUDENT = CandidateProfile.build(
    skills=[
        "Python",
        "TypeScript",
        "FastAPI",
        "PostgreSQL",
        "Docker",
        "AWS",
        "REST APIs",
        "pytest",
        "Selenium",
        "CI/CD",
        "Git",
        "LLMs",
        "RAG",
        "prompt engineering",
        "SQL",
        "Java",
        "distributed systems",
        "microservices",
    ],
    years_experience=3.0,
    seniority="mid",
    highest_degree="bachelors",
    in_progress_degree="masters",
    degree_fields=["Computer Science"],
    industries=["Information Technology", "Enterprise Software"],
    target_titles=["Backend Engineer", "Software Engineer", "Cloud Engineer"],
    needs_visa_sponsorship=True,
    wants_commitment=["full-time", "internship", "co-op"],
)

# Every field at its default, to prove the scorer is total on an empty profile
# rather than only correct on a populated one.
EMPTY_PROFILE = CandidateProfile.build()

# A senior IC with no degree, to reach the overqualified and degree-short branches.
SENIOR_NO_DEGREE = CandidateProfile.build(
    skills=["Python", "Go", "Kubernetes", "AWS", "Terraform", "observability"],
    years_experience=9.0,
    seniority="staff",
    highest_degree="none",
    industries=["Healthcare"],
    target_titles=["Platform Engineer"],
)

PROFILES = {
    "backend_ms_student": BACKEND_MS_STUDENT,
    "empty": EMPTY_PROFILE,
    "senior_no_degree": SENIOR_NO_DEGREE,
}

CASES = [(job, profile) for job in FIXTURE_NAMES for profile in PROFILES]


# --- the attribution contract -----------------------------------------------


@pytest.mark.parametrize(("job_name", "profile_name"), CASES)
def test_lines_sum_to_the_reported_score(job_name: str, profile_name: str) -> None:
    """100 plus every line equals the score, with no residual term.

    This is the promise the product is built on: every point of the number traces
    to a named reason. If a future axis forgets to record a line for something it
    deducted, this fails rather than the number quietly drifting away from its
    own explanation.
    """
    score = score_job(load(job_name), PROFILES[profile_name])
    assert BASE_SCORE + sum(line.points for line in score.lines) == score.raw_overall


@pytest.mark.parametrize(("job_name", "profile_name"), CASES)
def test_axis_points_sum_to_the_reported_score(job_name: str, profile_name: str) -> None:
    """The same total, reached the other way, so a mis-filed line cannot hide.

    A line recorded against the wrong axis would still satisfy the flat sum. This
    catches it, because the axis totals are computed from each axis's own weight
    and its own lines.
    """
    score = score_job(load(job_name), PROFILES[profile_name])
    assert sum(axis.points for axis in score.axes) == score.raw_overall


@pytest.mark.parametrize(("job_name", "profile_name"), CASES)
def test_each_axis_accounts_for_its_own_weight(job_name: str, profile_name: str) -> None:
    score = score_job(load(job_name), PROFILES[profile_name])
    for axis in score.axes:
        assert axis.points == axis.weight + sum(line.points for line in axis.lines), axis.axis


@pytest.mark.parametrize(("job_name", "profile_name"), CASES)
def test_no_axis_overdraws_or_goes_negative(job_name: str, profile_name: str) -> None:
    """An axis can lose everything it has and no more.

    Budgeting on the way down rather than clamping afterwards is what keeps the
    line items summing to the axis total. A negative axis would mean one axis had
    started eating another's points, and the per-axis bar in the UI would be
    showing something that is not a fraction.
    """
    score = score_job(load(job_name), PROFILES[profile_name])
    for axis in score.axes:
        if axis.axis == "bonus":
            continue
        assert 0 <= axis.points <= axis.weight, axis.axis


@pytest.mark.parametrize(("job_name", "profile_name"), CASES)
def test_every_line_names_a_reason(job_name: str, profile_name: str) -> None:
    """No anonymous points, and no line that is only a machine code."""
    score = score_job(load(job_name), PROFILES[profile_name])
    for line in score.lines:
        assert line.reason
        assert line.detail
        assert line.axis in AXIS_WEIGHTS


@pytest.mark.parametrize(("job_name", "profile_name"), CASES)
def test_score_stays_inside_the_reported_range(job_name: str, profile_name: str) -> None:
    score = score_job(load(job_name), PROFILES[profile_name])
    assert 0 <= score.overall <= 100


def test_axis_weights_sum_to_the_base_score() -> None:
    """The four real axes are the whole of the number.

    Bonuses carry weight zero on purpose, so they recover points rather than
    quietly raising the ceiling above 100.
    """
    real = {name: weight for name, weight in AXIS_WEIGHTS.items() if name != "bonus"}
    assert sum(real.values()) == BASE_SCORE
    assert AXIS_WEIGHTS["bonus"] == 0


# --- determinism -------------------------------------------------------------


@pytest.mark.parametrize(("job_name", "profile_name"), CASES)
def test_scoring_is_deterministic(job_name: str, profile_name: str) -> None:
    """Same inputs, same output, down to the order of the reasons.

    The number is cached and shown next to a job the user may revisit for weeks.
    A scorer that walked an unordered set would reorder its own explanation
    between two reads of the same job, which reads as the product changing its
    mind.
    """
    job, profile = load(job_name), PROFILES[profile_name]
    first, second = score_job(job, profile), score_job(job, profile)
    assert first == second
    assert explain(first) == explain(second)


def test_a_rebuilt_profile_scores_the_same() -> None:
    """Canonicalization does not depend on the order skills were entered."""
    job = load("cisco_cloud_engineer")
    forward = CandidateProfile.build(skills=["Python", "AWS", "Docker", "Go"])
    backward = CandidateProfile.build(skills=["Go", "Docker", "AWS", "Python"])
    assert score_job(job, forward).overall == score_job(job, backward).overall


# --- the guard against the failure that shaped the old scorer ----------------
#
# `fit-score.ts` divides by `max(named_skills, 8)` because of a specific,
# documented incident: a mechanical engineering internship named only three
# skills, a backend profile matched all three, and it scored 100% and outranked
# roles that were genuinely a fit. That protection must not be lost in the move
# to the server, and these are the tests that hold it.


def _sparse_job(skills: list[str]) -> JobEnrichment:
    return JobEnrichment(
        core_job_title="Mechanical Engineering Intern",
        skills=[
            SkillRequirement(skill=name, importance=2, necessity="required") for name in skills
        ],
    )


def test_a_sparse_posting_cannot_score_full_marks_on_skills() -> None:
    """Three skills, all matched, must not be a perfect skills axis.

    This is the original failure, reproduced. Matching everything a nearly-empty
    posting asked for is not evidence of fit, it is an absence of evidence, and
    a coverage ratio cannot tell those apart.
    """
    job = _sparse_job(["computer vision", "git", "R"])
    profile = CandidateProfile.build(skills=["computer vision", "git", "R"])
    score = score_job(job, profile)
    skills = score.axis("skills")
    assert skills.points < skills.weight
    assert score.overall < 100


def test_the_sparse_deduction_is_named_rather_than_silent() -> None:
    """The improvement over the TypeScript version.

    The old scorer returned 37% for a three-skill posting and had nothing to say
    about the missing 63 points, because the floor lived in a denominator. Here
    the same arithmetic produces a line item a user can read, which is the whole
    difference between a score and an explanation.
    """
    job = _sparse_job(["computer vision", "git", "R"])
    profile = CandidateProfile.build(skills=["computer vision", "git", "R"])
    score = score_job(job, profile)
    thin = [line for line in score.lines if line.reason == "posting_too_thin_to_judge"]
    assert len(thin) == 1
    assert thin[0].points < 0
    assert "only 3 skills" in thin[0].detail
    # And it is still fully accounted for.
    assert BASE_SCORE + sum(line.points for line in score.lines) == score.raw_overall


def test_a_posting_naming_no_skills_earns_no_skills_credit() -> None:
    """Nothing named is nothing proven, so the axis pays out zero.

    Deducting nothing here would hand every content-free posting a free 45
    points, which is the same bug as the sparse case with the volume turned up.
    """
    score = score_job(JobEnrichment(core_job_title="Intern"), CandidateProfile.build())
    assert score.axis("skills").points == 0
    reasons = {line.reason for line in score.lines}
    assert "posting_too_thin_to_judge" in reasons


def test_a_sparse_posting_is_reported_as_low_confidence() -> None:
    """A thin posting produces a number the UI should not present as confident.

    Low confidence is not a low score. It says the posting did not tell us
    enough, which deserves different words on the card than a job the candidate
    is genuinely a poor fit for.
    """
    score = score_job(_sparse_job(["git"]), CandidateProfile.build(skills=["git"]))
    assert score.confidence == "low"
    assert score.confidence_reasons


def test_a_dense_posting_outranks_a_sparse_one_at_equal_coverage() -> None:
    """The ordering the incident was actually about.

    Both postings have every named skill covered. The one that named ten of them
    has told us ten times as much, and must rank above the one that named three.
    """
    sparse = _sparse_job(["computer vision", "git", "R"])
    dense_skills = [
        "Python",
        "FastAPI",
        "PostgreSQL",
        "Docker",
        "AWS",
        "REST APIs",
        "pytest",
        "CI/CD",
        "microservices",
        "distributed systems",
    ]
    dense = JobEnrichment(
        core_job_title="Backend Engineer",
        skills=[
            SkillRequirement(skill=name, importance=2, necessity="required")
            for name in dense_skills
        ],
    )
    profile = CandidateProfile.build(skills=[*dense_skills, "computer vision", "git", "R"])
    assert score_job(dense, profile).axis("skills").points > (
        score_job(sparse, profile).axis("skills").points
    )


def test_the_floor_stops_applying_once_a_posting_is_specific_enough() -> None:
    """A posting at or above the floor is judged entirely on its own terms.

    Eight ordinary required asks is exactly the floor, so a profile covering all
    of them should see a full skills axis and no thin-posting line at all.
    """
    skills = ["Python", "Go", "Docker", "Kubernetes", "AWS", "Terraform", "SQL", "REST APIs"]
    job = JobEnrichment(
        core_job_title="Platform Engineer",
        skills=[
            SkillRequirement(skill=name, importance=2, necessity="required") for name in skills
        ],
    )
    pool = sum(2 * 2 for _ in skills)
    assert pool == MIN_SKILL_WEIGHT_POOL
    score = score_job(job, CandidateProfile.build(skills=skills))
    assert score.axis("skills").points == AXIS_WEIGHTS["skills"]
    assert not [line for line in score.lines if line.reason == "posting_too_thin_to_judge"]


# --- the axes ---------------------------------------------------------------


def test_missing_a_required_skill_costs_more_than_missing_a_preferred_one() -> None:
    """A flat coverage ratio cannot make this distinction, and it matters.

    Charging the same for a must-have and for something mentioned once under
    nice-to-have is how a role nobody wants outranks one they do.
    """
    job = JobEnrichment(
        core_job_title="Backend Engineer",
        skills=[
            SkillRequirement(skill="Kubernetes", importance=3, necessity="required"),
            SkillRequirement(skill="Jira", importance=1, necessity="preferred"),
        ],
    )
    score = score_job(job, CandidateProfile.build(skills=["Python"]))
    by_subject = {line.subject: line.points for line in score.lines if line.subject}
    assert by_subject["Kubernetes"] < by_subject["Jira"]


def test_a_missing_skill_quotes_the_posting() -> None:
    """Attribution cites the JD phrase rather than asserting against it.

    "Missing Kubernetes" is a claim. "Missing Kubernetes, from 'containers and
    orchestration platforms such as Docker, Kubernetes, or AWS EKS'" is a claim
    the user can check, and checking it is what turns a score into advice.
    """
    job = load("cisco_cloud_engineer")
    score = score_job(job, CandidateProfile.build(skills=["Python"]))
    cited = [
        line for line in score.lines if line.reason == "skill_missing" and line.evidence
    ]
    assert cited
    assert any(len(line.evidence or "") > 40 for line in cited)


def test_a_compound_requirement_is_satisfied_by_the_skill_inside_it() -> None:
    """Real sources name compound requirements, and exact matching misses them.

    The Jobright record carries "Cloud Computing AWS" and "Networking TCP/IP".
    Telling a candidate who has AWS that they are missing "Cloud Computing AWS"
    is wrong, and it is the kind of wrong a user spots immediately.
    """
    job = JobEnrichment(
        core_job_title="Cloud Engineer",
        skills=[SkillRequirement(skill="Cloud Computing AWS", importance=3)],
    )
    score = score_job(job, CandidateProfile.build(skills=["AWS"]))
    assert score.matched_skills == ("Cloud Computing AWS",)
    assert score.missing_skills == ()


def test_containment_matching_does_not_confuse_java_with_javascript() -> None:
    """The guard on the containment rule.

    Substring matching on raw strings would satisfy a JavaScript requirement
    with Java. Matching on whole tokens is what prevents it, and this product
    must never claim frontend skills it does not have.
    """
    job = JobEnrichment(
        core_job_title="Frontend Engineer",
        skills=[SkillRequirement(skill="JavaScript frameworks", importance=3)],
    )
    score = score_job(job, CandidateProfile.build(skills=["Java"]))
    assert score.missing_skills == ("JavaScript frameworks",)


def test_falling_short_on_years_is_priced_and_named() -> None:
    """The line the whole differentiator is modelled on.

    The Cisco posting requires five years and this profile has three, so the
    experience axis loses sixteen points and says exactly why.
    """
    score = score_job(load("cisco_cloud_engineer"), BACKEND_MS_STUDENT)
    short = [line for line in score.lines if line.reason == "experience_short"]
    assert len(short) == 1
    assert short[0].points == -16
    assert short[0].detail == "2 years short of the 5 this posting requires"


def test_an_unstated_experience_requirement_is_not_free() -> None:
    """A posting that says nothing about experience has told us nothing.

    Deducting zero would rank a vague posting above one whose stated requirement
    the candidate actually meets, which inverts the thing the axis is for.
    """
    job = JobEnrichment(core_job_title="Engineer", seniority_level="unknown")
    score = score_job(job, BACKEND_MS_STUDENT)
    reasons = {line.reason for line in score.axis("experience").lines}
    assert "experience_requirement_unstated" in reasons


def test_seniority_bands_are_used_when_no_years_are_stated() -> None:
    job = JobEnrichment(core_job_title="Staff Engineer", seniority_level="staff")
    score = score_job(job, BACKEND_MS_STUDENT)
    short = [line for line in score.lines if line.reason == "seniority_short"]
    assert len(short) == 1
    assert "2 bands below" in short[0].detail


def test_being_well_past_the_band_costs_something_but_much_less() -> None:
    """A staff engineer shown an internship is being shown a bad result.

    It still costs far less than falling short, because a stretch downwards is a
    choice and a stretch upwards is a rejection.
    """
    intern = JobEnrichment(core_job_title="Intern", seniority_level="intern")
    over = score_job(intern, SENIOR_NO_DEGREE).axis("experience")
    staff = JobEnrichment(core_job_title="Staff Engineer", seniority_level="staff")
    under = score_job(staff, CandidateProfile.build(seniority="new-grad")).axis("experience")
    assert over.points > under.points
    assert over.points < AXIS_WEIGHTS["experience"]


def test_an_in_progress_degree_is_a_near_miss_not_a_gap() -> None:
    """The case both references get wrong, and most of this product's users.

    Someone part way through the masters a posting requires is not in the same
    position as someone who never enrolled.
    """
    job = JobEnrichment(
        core_job_title="Research Engineer",
        education={"masters": {"status": "required"}},  # type: ignore[arg-type]
    )
    enrolled = score_job(job, BACKEND_MS_STUDENT).axis("education")
    never = score_job(
        job, CandidateProfile.build(highest_degree="bachelors")
    ).axis("education")
    assert never.points < enrolled.points < AXIS_WEIGHTS["education"]
    assert any(line.reason == "degree_in_progress" for line in enrolled.lines)


def test_a_posting_that_accepts_students_costs_an_enrolled_candidate_nothing() -> None:
    """"Currently pursuing an MS" is asking for exactly this candidate."""
    job = JobEnrichment(
        core_job_title="Research Intern",
        education={  # type: ignore[arg-type]
            "masters": {"status": "required"},
            "enrolled_student_ok": True,
        },
    )
    education = score_job(job, BACKEND_MS_STUDENT).axis("education")
    assert education.points == AXIS_WEIGHTS["education"]
    assert any(line.reason == "enrolled_student_accepted" for line in education.lines)


def test_an_unstated_degree_requirement_deducts_nothing_and_says_so() -> None:
    """A full axis with an empty breakdown would look like a bug.

    The note carries no points and answers the question anyway, which is what a
    zero-point line is for.
    """
    education = score_job(load("vienna_fullstack_engineer"), EMPTY_PROFILE).axis("education")
    assert education.points == AXIS_WEIGHTS["education"]
    assert [line.reason for line in education.lines] == ["education_requirement_unstated"]
    assert all(line.points == 0 for line in education.lines)


def test_industry_overlap_costs_nothing_and_a_full_miss_costs_the_axis() -> None:
    score = score_job(load("cisco_cloud_engineer"), BACKEND_MS_STUDENT)
    assert score.axis("industry").points == AXIS_WEIGHTS["industry"]

    mismatch = score_job(load("first_tee_play9_intern"), BACKEND_MS_STUDENT)
    assert mismatch.axis("industry").points == 0
    assert any(line.reason == "industry_mismatch" for line in mismatch.lines)


def test_an_unknown_industry_history_costs_nothing() -> None:
    """No evidence and contrary evidence are different: one is free, one is not.

    Rewritten deliberately. This used to assert that an unknown industry history
    cost SOMETHING (less than a mismatch, but more than nothing), and that flat
    charge was the bug: `build_candidate_profile` has no industry field to fill,
    so the branch fired on every posting that named an industry and capped a
    perfect-fit resume at 93 with a gap no rewrite could close.

    A deduction has to mean "the evidence is against you". Silence is not
    evidence against a candidate, and this axis already charges nothing when the
    POSTING names no industry -- a profile that names none is the same absence
    from the other side. Contrary evidence still costs the whole axis.
    """
    job = load("cisco_cloud_engineer")
    unknown = score_job(job, CandidateProfile.build()).axis("industry")
    mismatch = score_job(job, CandidateProfile.build(industries=["Agriculture"])).axis("industry")
    assert unknown.points == AXIS_WEIGHTS["industry"]
    assert mismatch.points < unknown.points
    assert any(line.reason == "industry_history_unknown" for line in unknown.lines)
    assert all(line.points == 0 for line in unknown.lines)


def test_a_perfect_fit_can_now_reach_the_top_of_the_industry_axis() -> None:
    """The ceiling this unblocks: nothing about an absent industry holds a score down."""
    job = load("cisco_cloud_engineer")
    unknown = score_job(job, CandidateProfile.build())
    assert unknown.axis("industry").points == AXIS_WEIGHTS["industry"]
    # And the arithmetic still adds up, which is the invariant this file guards.
    assert 100 + sum(line.points for line in unknown.lines) == unknown.raw_overall


# --- bonuses and blockers ---------------------------------------------------


def test_an_exact_title_match_is_worth_more_than_a_partial_one() -> None:
    job = JobEnrichment(core_job_title="Backend Engineer")
    exact = score_job(job, CandidateProfile.build(target_titles=["Backend Engineer"]))
    partial = score_job(job, CandidateProfile.build(target_titles=["Backend Developer"]))
    assert exact.axis("bonus").points > partial.axis("bonus").points > 0


def test_seniority_words_do_not_break_a_title_match() -> None:
    """The experience axis already priced the seniority.

    Counting it again in the title bonus would charge the candidate twice for one
    fact.
    """
    job = JobEnrichment(core_job_title="Senior Backend Engineer II")
    score = score_job(job, CandidateProfile.build(target_titles=["Backend Engineer"]))
    assert any(line.reason == "title_match" for line in score.lines)


def test_bonuses_are_capped_and_say_when_they_were_capped() -> None:
    """A truncated bonus must not read as a reason worth less than it is.

    Without the note, the same fact would appear to be worth different amounts on
    two different jobs, and the breakdown would look inconsistent.
    """
    job = JobEnrichment(
        core_job_title="Backend Engineer",
        workplace={"workplace_type": "remote"},  # type: ignore[arg-type]
        eligibility=Eligibility(visa_sponsorship="yes"),
        commitment=["full-time"],
    )
    profile = CandidateProfile.build(
        target_titles=["Backend Engineer"],
        needs_visa_sponsorship=True,
        prefers_remote=True,
        wants_commitment=["full-time"],
    )
    score = score_job(job, profile)
    bonus = score.axis("bonus")
    assert bonus.points == 15
    assert any("capped at" in line.detail for line in bonus.lines)


def test_a_refused_visa_is_a_blocker_and_not_a_deduction() -> None:
    """Binary facts do not belong in a ranking number.

    A posting that will not sponsor is not a slightly worse match for a
    candidate who needs sponsorship, it is an impossible one. Folding it into the
    score is how a 90% match turns out to be unapplyable.
    """
    job = JobEnrichment(
        core_job_title="Backend Engineer",
        eligibility=Eligibility(visa_sponsorship="no"),
    )
    score = score_job(job, CandidateProfile.build(needs_visa_sponsorship=True))
    assert not score.is_eligible
    assert [blocker.reason for blocker in score.blockers] == ["no_visa_sponsorship"]
    # And it took no points, so the arithmetic is untouched.
    assert all(blocker.points == 0 for blocker in score.blockers)


def test_silence_about_sponsorship_is_not_a_refusal() -> None:
    """The tri-state, earning its place.

    The reference collapses this to a boolean, so "we do not sponsor" and "the
    posting never said" are the same value. For an international candidate that
    is the difference between skipping a job and asking about it.
    """
    job = JobEnrichment(core_job_title="Backend Engineer")
    assert job.eligibility.visa_sponsorship == "not-mentioned"
    score = score_job(job, CandidateProfile.build(needs_visa_sponsorship=True))
    assert score.is_eligible


def test_a_clearance_requirement_blocks_a_candidate_without_one() -> None:
    job = JobEnrichment(
        core_job_title="Security Engineer",
        eligibility=Eligibility(security_clearance="required"),
    )
    score = score_job(job, CandidateProfile.build())
    assert [blocker.reason for blocker in score.blockers] == ["security_clearance_required"]


# --- the allocator ----------------------------------------------------------


def test_allocation_sums_to_the_total_exactly() -> None:
    """Largest remainder, which is why there is never a leftover point.

    Rounding each line independently drifts by a point or two, and that drift is
    exactly the unexplained residual the whole design exists to avoid.
    """
    from fractions import Fraction

    for total in range(0, 46):
        for count in range(1, 20):
            shares = [Fraction(1, count)] * count
            allocated = _allocate(total, shares)
            assert sum(allocated) == total, (total, count)
            assert all(amount >= 0 for amount in allocated)


def test_allocation_favours_the_heavier_share() -> None:
    from fractions import Fraction

    allocated = _allocate(3, [Fraction(6), Fraction(1), Fraction(1)])
    assert allocated[0] > allocated[1]


def test_every_named_requirement_appears_in_the_breakdown() -> None:
    """A dense posting spreads its points thin, and nothing may vanish.

    The Cisco posting names 68 skills against a 45 point axis, so some misses
    round to under a point. Those are recorded at zero rather than dropped, so
    the breakdown stays a complete account of what the posting asked for and no
    skill shows on the card without a matching line.
    """
    job = load("cisco_cloud_engineer")
    score = score_job(job, BACKEND_MS_STUDENT)
    accounted = {
        line.subject
        for line in score.lines
        if line.reason in {"skill_missing", "skill_missing_below_a_point"}
    }
    assert accounted == set(score.missing_skills)
    assert len(score.matched_skills) + len(score.missing_skills) == len(job.skills)


# --- presentation -----------------------------------------------------------


def test_a_negative_score_is_unreachable_by_construction() -> None:
    """The worst possible match is 0, not a negative number, and not by clamping.

    Every axis is budgeted, so the four together can deduct at most exactly 100.
    That means the lower clamp in `score_job` can never fire, which is the
    property worth having: the floor comes from the arithmetic rather than from a
    correction applied on top of it.

    This is the deliberately hostile case: nothing matched, nowhere near the
    seniority, a doctorate required, a people-manager role, and the wrong
    industry.
    """
    job = JobEnrichment(
        core_job_title="Golf Instructor",
        company_industry="Sports",
        skills=[SkillRequirement(skill="golf", importance=3, necessity="required")],
        seniority_level="director",
        role_type="people-manager",
        education={"doctorate": {"status": "required"}},  # type: ignore[arg-type]
    )
    score = score_job(job, CandidateProfile.build(industries=["Software"]))
    assert score.raw_overall == score.overall
    assert score.raw_overall >= 0
    assert score.axis("skills").points == 0
    assert score.axis("industry").points == 0


@pytest.mark.parametrize(("job_name", "profile_name"), CASES)
def test_deductions_never_exceed_the_base_score(job_name: str, profile_name: str) -> None:
    """The same property across every fixture and profile pairing."""
    score = score_job(load(job_name), PROFILES[profile_name])
    deductions = sum(line.points for line in score.lines if line.points < 0)
    assert deductions >= -BASE_SCORE


def test_explain_reports_a_clamp_when_one_happened() -> None:
    """A clamp is a thing that happened to the number, so it is disclosed.

    Hiding it would break the promise that the breakdown explains the score. Only
    the upper clamp is reachable: a flawless match keeps all 100 and the bonuses
    then push it past the top of the scale.
    """
    skills = ["Python", "Go", "Docker", "Kubernetes", "AWS", "Terraform", "SQL", "REST APIs"]
    job = JobEnrichment(
        core_job_title="Backend Engineer",
        company_industry="Information Technology",
        seniority_level="mid",
        role_type="individual-contributor",
        workplace={"workplace_type": "remote"},  # type: ignore[arg-type]
        eligibility=Eligibility(visa_sponsorship="yes"),
        commitment=["full-time"],
        skills=[
            SkillRequirement(skill=name, importance=2, necessity="required") for name in skills
        ],
    )
    profile = CandidateProfile.build(
        skills=skills,
        seniority="mid",
        industries=["Information Technology"],
        target_titles=["Backend Engineer"],
        needs_visa_sponsorship=True,
        prefers_remote=True,
        wants_commitment=["full-time"],
    )
    score = score_job(job, profile)
    assert score.raw_overall == 115
    assert score.overall == 100
    assert any("clamped from 115" in line for line in explain(score))


def test_top_reasons_are_the_biggest_movers_and_deterministic() -> None:
    score = score_job(load("cisco_cloud_engineer"), BACKEND_MS_STUDENT)
    top = score.top_reasons(3)
    assert len(top) == 3
    assert [abs(line.points) for line in top] == sorted(
        (abs(line.points) for line in top), reverse=True
    )
    assert score.top_reasons(3) == top
    assert all(line.points != 0 for line in top)


def test_explain_covers_every_axis_that_did_anything() -> None:
    score = score_job(load("worlds_ml_research_intern"), BACKEND_MS_STUDENT)
    rendered = "\n".join(explain(score))
    for axis in score.axes:
        if axis.lines:
            assert axis.axis in rendered


def test_score_carries_the_schema_version_it_was_computed_against() -> None:
    """So a stored score can be invalidated when the schema moves under it."""
    score = score_job(load("cisco_cloud_engineer"), BACKEND_MS_STUDENT)
    assert score.schema_version == JobEnrichment().schema_version


def test_a_stale_enrichment_is_reported_as_low_confidence() -> None:
    job = load("cisco_cloud_engineer")
    stale = job.model_copy(update={"schema_version": 0})
    assert score_job(stale, BACKEND_MS_STUDENT).confidence == "low"


def test_a_failed_enrichment_is_reported_as_low_confidence() -> None:
    """A job that could not be enriched must not present a confident number.

    Otherwise an ingest failure is indistinguishable on the card from a genuine
    poor fit, and the user acts on a number that was never computed from anything.
    """
    job = JobEnrichment(core_job_title="Engineer", extraction_gaps=["gateway_error"])
    score = score_job(job, BACKEND_MS_STUDENT)
    assert score.confidence == "low"
    assert any("gateway_error" in reason for reason in score.confidence_reasons)


def test_the_scorer_is_total_on_an_empty_job_and_an_empty_profile() -> None:
    """Neither half may raise, because both halves come from the network.

    An enrichment that failed and a profile that was never filled in are both
    ordinary, and the browse feed has to render either way.
    """
    score: MatchScore = score_job(JobEnrichment(), CandidateProfile.build())
    assert 0 <= score.overall <= 100
    assert BASE_SCORE + sum(line.points for line in score.lines) == score.raw_overall


# ---------------------------------------------------------------------------
# Postings open only to current undergraduates.
#
# Salesforce's "Summer 2027 Intern - Software Engineer" asks for someone
# "Enrolled and currently pursuing a BS in Computer Science" who is "Returning
# to school after Summer 2027 to complete your degree". A candidate holding a
# bachelors and enrolled in a masters is not that person, and the axis scored
# him 15 out of 15 on it: he holds the degree it names, so `held >= required`
# was true and the branch read "education_requirement_met".
#
# The rule has to fire on the CANDIDATE rather than on the posting, or it
# becomes a blanket penalty on internships. The three negative tests below are
# the ones that keep that true, and are the point of this block.
# ---------------------------------------------------------------------------


def _student_posting(
    *, bachelors: str = "required", masters: str = "not-mentioned", enrolled_ok: bool = True
) -> JobEnrichment:
    return JobEnrichment(
        core_job_title="Software Engineer Intern",
        education=EducationRequirements(
            bachelors=DegreeRequirement(status=bachelors),  # type: ignore[arg-type]
            masters=DegreeRequirement(status=masters),  # type: ignore[arg-type]
            enrolled_student_ok=enrolled_ok,
        ),
    )


def _education_reasons(score: MatchScore) -> set[str]:
    return {line.reason for line in score.lines if line.axis == "education"}


BACHELORS_STUDENT = CandidateProfile.build(
    skills=["Python", "Java"],
    highest_degree="none",
    in_progress_degree="bachelors",
    degree_fields=["Computer Science"],
)


def test_an_undergraduate_only_posting_costs_a_masters_student() -> None:
    """The reported bug: a posting he cannot hold scored full marks on education."""
    score = score_job(_student_posting(), BACKEND_MS_STUDENT)

    assert "undergraduate_only_posting" in _education_reasons(score)
    assert "education_requirement_met" not in _education_reasons(score)
    education = next(axis for axis in score.axes if axis.axis == "education")
    assert education.points < AXIS_WEIGHTS["education"]


def test_the_undergraduate_it_is_addressed_to_loses_nothing() -> None:
    """Not a global cap on undergraduate roles, which was the explicit ask.

    Same posting, and the candidate it was written for. If this ever deducts,
    the rule has stopped being about the candidate and started being about the
    posting.
    """
    score = score_job(_student_posting(), BACHELORS_STUDENT)

    assert "undergraduate_only_posting" not in _education_reasons(score)


def test_a_posting_open_to_both_degrees_is_not_undergraduate_only() -> None:
    """"Bachelor's or Master's" is open to him and records that it is."""
    score = score_job(
        _student_posting(masters="required"), BACKEND_MS_STUDENT
    )

    assert "undergraduate_only_posting" not in _education_reasons(score)


def test_a_full_time_role_requiring_a_bachelors_is_not_undergraduate_only() -> None:
    """The distinction the whole rule turns on.

    A permanent role that requires a bachelors is happy to hire someone with
    more education. Only a posting addressed to CURRENT students is restricting
    a degree status rather than stating a minimum, which is why
    `enrolled_student_ok` is the first thing checked.
    """
    score = score_job(
        _student_posting(enrolled_ok=False), BACKEND_MS_STUDENT
    )

    assert "undergraduate_only_posting" not in _education_reasons(score)


def test_the_deduction_still_explains_itself_in_the_totals() -> None:
    """The module's own invariant, on the new line.

    100 + every line == raw_overall, with no residual. A deduction that does not
    participate in that is a number nobody can trace.
    """
    score = score_job(_student_posting(), BACKEND_MS_STUDENT)

    assert BASE_SCORE + sum(line.points for line in score.lines) == score.raw_overall


# ---------------------------------------------------------------------------
# A posting naming several degree levels is listing alternatives.
#
# Roblox: "Pursuing an undergraduate or graduate degree in computer science,
# engineering, or a related field." Every level it names is marked required,
# because that is what the extraction is told to do. Reading the tallest as the
# bar turned an either-or into a demand for the highest one.
#
# The same rule the tailoring pass already applies to skills, where "one or more
# of Go, Node.js or Python" is one requirement Python satisfies rather than
# three the candidate mostly fails.
# ---------------------------------------------------------------------------


UNDERGRAD_STUDENT = CandidateProfile.build(
    skills=["Python", "Java"],
    highest_degree="none",
    in_progress_degree="bachelors",
    degree_fields=["Computer Science"],
)


def _any_of(*levels: str, enrolled_ok: bool = True) -> JobEnrichment:
    kwargs = {
        level: DegreeRequirement(status="required")  # type: ignore[arg-type]
        for level in levels
    }
    return JobEnrichment(
        core_job_title="Machine Learning Research Intern",
        education=EducationRequirements(enrolled_student_ok=enrolled_ok, **kwargs),
    )


def test_a_bachelors_student_is_eligible_for_a_bs_ms_or_phd_posting() -> None:
    """The failure this fixes, at its worst.

    A research internship open to a BS, MS or PhD scored an eligible bachelors
    student two levels short: fourteen points off a fifteen-point axis, on a
    posting they can apply to.
    """
    score = score_job(_any_of("bachelors", "masters", "doctorate"), UNDERGRAD_STUDENT)

    assert "degree_short" not in _education_reasons(score)
    education = next(axis for axis in score.axes if axis.axis == "education")
    assert education.points == AXIS_WEIGHTS["education"]


def test_a_masters_student_is_eligible_for_the_same_posting() -> None:
    score = score_job(_any_of("bachelors", "masters", "doctorate"), BACKEND_MS_STUDENT)

    assert "degree_short" not in _education_reasons(score)


def test_undergraduate_or_graduate_is_not_a_demand_for_a_masters() -> None:
    """Roblox's wording, and the shape it produces."""
    score = score_job(_any_of("bachelors", "masters"), UNDERGRAD_STUDENT)

    assert "degree_short" not in _education_reasons(score)


def test_a_single_required_degree_is_still_a_floor_to_clear() -> None:
    """The rule is about alternatives, not about lowering every bar.

    One level named is not a list, and someone short of it is still short.
    """
    posting = JobEnrichment(
        core_job_title="Research Scientist",
        education=EducationRequirements(
            doctorate=DegreeRequirement(status="required"),
        ),
    )
    score = score_job(posting, UNDERGRAD_STUDENT)

    assert "degree_short" in _education_reasons(score)


def test_the_tallest_named_degree_is_still_reported_as_such() -> None:
    """`highest_required` answers a different question and keeps answering it.

    It describes the posting; `required_floor` scores a candidate against it.
    Collapsing the two would lose the ability to say what a posting asked for.
    """
    education = _any_of("bachelors", "masters", "doctorate").education

    assert education.highest_required() == "doctorate"
    assert education.required_floor() == "bachelors"
