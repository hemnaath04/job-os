from __future__ import annotations

from job_os.services.tailor import (
    _compute_ats_from_document,
    _is_candidate_skill,
    _jd_requirements,
)

# The real Point72 internship JD that scored 30 before this was fixed.
JD = {
    "required_skills": [
        "Currently pursuing a bachelor's or master's in Computer Science, "
        "Computer Engineering, or a similar technical field",
        "Minimum 3.0 GPA",
        "Comfortable with one or more of C++, Python or TypeScript",
        "A solid grasp of computer science fundamentals: data structures, "
        "algorithms, systems",
        "Ability to start the internship on June 1, 2027",
    ],
    "technologies": ["C++", "Python", "TypeScript"],
    "keywords": [
        "trading",
        "proprietary trading firm",
        "electronic trading",
        "market initiatives",
        "internship",
        "summer internship",
    ],
}

RESUME = {
    "basics": {"summary": "Backend engineer working in Python."},
    "work": [
        {
            "name": "EPAM Systems",
            "highlights": [
                "Built Python test automation across data structures and algorithms.",
                "Tuned distributed systems for throughput.",
            ],
        }
    ],
}

# What the agent itself reported, including the three skills that only ever
# appear inside a prose requirement.
MODEL_MATCHED = [
    "Python",
    "data structures",
    "algorithms",
    "systems",
    "summer internship",
    "internship",
]
MODEL_MISSING = ["C++", "TypeScript"]


def _score() -> tuple[float, dict]:
    score, report = _compute_ats_from_document(
        jd_parsed=JD,
        json_resume=RESUME,
        fallback_matched=MODEL_MATCHED,
        fallback_missing=MODEL_MISSING,
    )
    return float(score), report


def test_eligibility_and_employer_phrases_leave_the_score() -> None:
    _, report = _score()
    scored = {term.casefold() for term in report["matched"] + report["missing"]}
    for term in (
        "minimum 3.0 gpa",
        "proprietary trading firm",
        "market initiatives",
    ):
        assert term not in scored
    assert "Minimum 3.0 GPA" in report["excluded_non_skills"]


def test_job_type_words_do_not_pad_the_matches() -> None:
    """"internship" matching is not evidence of a relevant skill."""
    _, report = _score()
    assert "internship" not in {term.casefold() for term in report["matched"]}


def test_skills_buried_in_a_prose_requirement_are_recovered() -> None:
    # These live only inside "computer science fundamentals: data structures,
    # algorithms, systems", which is too long to be a keyword, so they used to
    # be dropped entirely and counted neither way.
    _, report = _score()
    matched = {term.casefold() for term in report["matched"]}
    assert {"data structures", "algorithms", "systems"} <= matched


def test_an_any_one_of_language_list_is_one_requirement_not_nine() -> None:
    """A real Roblox JD's 'one or more of Go, Node.js, Ruby, Python, C++, Lua,
    Swift, C#, Java' scored a Go+Python+Java candidate 9/26 required_met and
    read as a failed match. `_jd_requirements` already collapses this
    correctly when it arrives as ONE comma/or-joined string (the shape
    jd_parse.py's prompt now asks the extraction model to produce) -- this
    guards that contract. The failure mode this guards against is the
    extraction model splitting the list into nine separate required_skills
    entries instead, which this function has no way to recover from after the
    fact: see test_ats_scoring.py's own history and jd_parse.SYSTEM_PROMPT.
    """
    reqs, _prose, _excluded = _jd_requirements(
        {
            "required_skills": [
                "Go, Node.js, Ruby, Python, C++, Lua, Swift, C#, or Java",
            ]
        }
    )
    assert len(reqs) == 1
    assert set(reqs[0].alternatives) == {
        "Go",
        "Node.js",
        "Ruby",
        "Python",
        "C++",
        "Lua",
        "Swift",
        "C#",
        "Java",
    }
    assert reqs[0].covered_by("built backend services in go and python")


def test_genuinely_absent_skills_still_count_against_the_score() -> None:
    score, report = _score()
    missing = {term.casefold() for term in report["missing"]}
    assert {"c++", "typescript"} <= missing
    # A real mismatch stays a moderate score, not a flattering one.
    assert 40 <= score <= 60


def test_a_term_the_model_claims_is_not_credited_unless_the_resume_says_it() -> None:
    """No inflation: every scored term comes from the JD and is checked on the page.

    The model's own lists no longer reach the denominator either. Reading them
    meant the keyword set changed with however many terms a pass chose to
    enumerate, so the same JD scored 20.0 on one run and 42.9 on the next. The JD
    is fixed, so the score is now fixed too. Kubernetes is absent below because
    the JD never asked for it, whoever claimed it.
    """
    score, report = _compute_ats_from_document(
        jd_parsed={"technologies": ["Rust"]},
        json_resume={"basics": {"summary": "Python only."}},
        fallback_matched=["Rust", "Kubernetes"],
        fallback_missing=[],
    )
    assert report["matched"] == []
    assert {term.casefold() for term in report["missing"]} == {"rust"}
    # Still reported, as the model's account of its own work.
    assert report["model_reported_matched"] == ["Rust", "Kubernetes"]
    assert score == 0


def test_prose_requirements_are_reported_not_hidden() -> None:
    _, report = _score()
    assert any("Currently pursuing" in term for term in report["prose_requirements"])


def test_is_candidate_skill_boundaries() -> None:
    # Real skills survive, including ones that merely contain a filtered word.
    for term in ("firmware", "Python", "C++", "distributed systems", "trading"):
        assert _is_candidate_skill(term), term
    for term in ("Minimum 3.0 GPA", "a proprietary trading firm", "internship"):
        assert not _is_candidate_skill(term), term


def test_enthusiasm_phrases_are_not_scoreable_skills() -> None:
    """A real posting's required_skills included 'excited to learn' and 'open
    to feedback' verbatim, each scored as a missing skill no bullet could ever
    contain."""
    for term in (
        "excited to learn",
        "open to feedback",
        "excited about generative AI",
        "eager to learn",
        "growth mindset",
    ):
        assert not _is_candidate_skill(term), term


def test_the_same_jd_scores_the_same_however_the_model_reports_itself() -> None:
    """The number shown to the user must not depend on the model's own bookkeeping."""
    modest, _ = _compute_ats_from_document(
        jd_parsed=JD,
        json_resume=RESUME,
        fallback_matched=["Python"],
        fallback_missing=["C++"],
    )
    verbose, _ = _compute_ats_from_document(
        jd_parsed=JD,
        json_resume=RESUME,
        fallback_matched=MODEL_MATCHED,
        fallback_missing=[*MODEL_MISSING, "Kubernetes", "Rust", "Terraform"],
    )
    assert modest == verbose


def test_a_prose_requirement_yields_its_skills_and_not_its_clauses() -> None:
    from job_os.services.tailor import _skills_inside_prose

    assert _skills_inside_prose(
        "A solid grasp of computer science fundamentals: data structures, "
        "algorithms, systems"
    ) == ["data structures", "algorithms", "systems"]
    # A clause is not a skill, and neither is an eligibility rule or a date.
    assert _skills_inside_prose("Genuine interest in markets and how software supports them") == []
    assert _skills_inside_prose("Ability to start the internship on June 1, 2027") == []
    assert _skills_inside_prose(
        "A strong work ethic and ability to adapt quickly in a fast-paced environment"
    ) == []
