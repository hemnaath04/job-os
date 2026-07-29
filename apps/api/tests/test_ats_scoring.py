from __future__ import annotations

from job_os.services.tailor import _compute_ats_from_document, _is_candidate_skill

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


def test_genuinely_absent_skills_still_count_against_the_score() -> None:
    score, report = _score()
    missing = {term.casefold() for term in report["missing"]}
    assert {"c++", "typescript"} <= missing
    # A real mismatch stays a moderate score, not a flattering one.
    assert 40 <= score <= 60


def test_a_term_the_model_claims_is_not_credited_unless_the_resume_says_it() -> None:
    """No inflation: every term is verified against the assembled document."""
    score, report = _compute_ats_from_document(
        jd_parsed={"technologies": ["Rust"]},
        json_resume={"basics": {"summary": "Python only."}},
        fallback_matched=["Rust", "Kubernetes"],
        fallback_missing=[],
    )
    assert report["matched"] == []
    assert {term.casefold() for term in report["missing"]} == {"rust", "kubernetes"}
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
