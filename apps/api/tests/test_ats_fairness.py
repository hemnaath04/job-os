"""Whether the ATS score is FAIR, not merely honest.

The user's complaint was that a first tailor reads lower than the real fit. Every
case here is a way the old term-counting scorer deflated a genuine match, taken
from the two real postings the workflow was run against.
"""
from __future__ import annotations

from job_os.services.tailor import (
    _compute_ats_from_document,
    _mentions,
    _skills_inside_prose,
)

RESUME = {
    "basics": {"summary": "Backend engineer working in Python."},
    "work": [
        {
            "name": "EPAM Systems",
            "highlights": [
                "Wrote automated tests for a Go pricing engine.",
                "Built a FastAPI service backed by MongoDB.",
            ],
        }
    ],
}


def _score(jd: dict) -> tuple[float, dict]:
    score, report = _compute_ats_from_document(
        jd_parsed=jd, json_resume=RESUME, fallback_matched=[], fallback_missing=[]
    )
    return float(score), report


def test_a_nice_to_have_does_not_count_against_the_headline_score() -> None:
    """The real deflation: 25 absent terms, every one under "Nice to have".

    A posting the candidate genuinely fits scored 35 because its bonus stack was
    averaged in with its requirements.
    """
    jd = {
        "required_skills": ["Python", "FastAPI"],
        "preferred_skills": [
            "Vector database hands-on: Weaviate, Pinecone, pgvector, FAISS, or Milvus",
            "Infrastructure as Code: Terraform, Pulumi, or CloudFormation",
        ],
        "technologies": ["Python", "FastAPI", "Weaviate", "Terraform", "Pinecone"],
    }
    score, report = _score(jd)
    assert score == 100.0
    assert report["required_met"] == report["required_total"] == 2
    # The bonus stack is still reported, just not averaged into the headline.
    assert "Weaviate" in report["preferred_missing"]
    assert "Terraform" in report["preferred_missing"]
    assert report["preferred_coverage"] == 0.0


def test_a_term_named_as_required_stays_required_when_the_bonus_list_repeats_it() -> None:
    """FastAPI was demoted to a bonus by "nice to have: production FastAPI systems"."""
    jd = {
        "required_skills": ["FastAPI"],
        "preferred_skills": ["Production FastAPI systems"],
    }
    _score_value, report = _score(jd)
    assert report["matched"] == ["FastAPI"]
    assert "FastAPI" not in report["preferred_matched"]


def test_alternatives_are_one_requirement_satisfied_by_any_of_them() -> None:
    """He writes Python, so this requirement is met, not two-thirds failed."""
    jd = {"required_skills": ["Comfortable with one or more of C++, Python or TypeScript"]}
    score, report = _score(jd)
    assert score == 100.0
    assert report["required_total"] == 1


def test_an_eligibility_rule_is_not_scored_as_a_skill() -> None:
    """A degree window is not something a resume answers with a skill.

    The old scorer mined "Computer Engineering" out of this sentence and counted it
    as a missing skill.
    """
    jd = {
        "required_skills": [
            "Currently pursuing a bachelor's or master's in Computer Science, "
            "Computer Engineering, or a similar technical field",
            "At least junior standing, with a graduation date of December 2027 or May 2028",
            "Python",
        ]
    }
    score, report = _score(jd)
    assert score == 100.0
    assert report["required_total"] == 1
    assert any("Currently pursuing" in term for term in report["excluded_non_skills"])


def test_an_eligibility_statement_phrased_as_who_the_role_suits_is_excluded() -> None:
    """"Ideal for students" is who the role is for, not a skill.

    A real Crowe Advisory posting scored 29 with this counted as a missing
    required skill no resume text could ever satisfy -- structurally capping
    the score regardless of true fit.
    """
    jd = {
        "required_skills": ["Ideal for students", "Python"],
    }
    score, report = _score(jd)
    assert score == 100.0
    assert report["required_total"] == 1
    assert any("Ideal for students" in term for term in report["excluded_non_skills"])


def test_a_skill_is_matched_as_a_word_not_as_a_substring() -> None:
    """MongoDB does not prove Go, and Cloud Storage does not prove RAG."""
    assert not _mentions("built a service backed by mongodb", "Go")
    assert _mentions("wrote automated tests for a go pricing engine", "Go")
    assert not _mentions("wired up cloud storage", "RAG")
    # Terms whose edges are not word characters still match.
    assert _mentions("comfortable with c++ and python", "C++")
    assert _mentions("tightened ci/cd pipelines", "CI/CD")


def test_the_scorer_credits_go_only_where_the_resume_says_go() -> None:
    jd = {"technologies": ["Go", "Rust"]}
    _score_value, report = _score(jd)
    assert report["matched"] == ["Go"]
    assert report["missing"] == ["Rust"]


def test_clause_debris_is_not_scored_as_a_requirement() -> None:
    """Splitting sentences used to mint requirements nobody wrote.

    "Git and DevOps thinking: version control, CI/CD concepts, deploying code"
    produced "CI" plus "CD concepts" plus "deploying code", and
    "Built APIs or backend systems that other code calls" produced "Built APIs".
    """
    recovered = _skills_inside_prose(
        "Git and DevOps thinking: version control, CI/CD concepts, deploying code"
    )
    assert recovered == ["version control", "CI/CD"]
    assert _skills_inside_prose(
        "Built APIs or backend systems that other code calls"
    ) == []
    # A parenthetical holds real alternatives and must survive the split.
    assert "Claude" in _skills_inside_prose(
        "Hands-on LLM API integration (Claude, ChatGPT, or another LLM)"
    )


def test_a_genuine_mismatch_is_not_flattered() -> None:
    """The hard limit: fairer must not mean higher on a role he does not fit.

    The stack the job runs on is the part that must stay in the denominator.
    Satisfying the "one or more of" eligibility line with Python does not mean
    the C++ codebase stops being a gap, so C++ and TypeScript are still scored
    against him and the score is still a clear miss rather than a pass.

    The domain words moved. `keywords` is a nice-to-have field now, because a
    parser fills it with whatever it saw: across the real postings in this
    workspace, the keyword-only terms included "housing stipend", "June to
    August 2027", "Dragon", "Starlink" and "Anthropic Fellows Program", each
    scored as a must-have the candidate had failed to have. So the domain terms
    here are asserted to be *reported* rather than to be *scored*. They are
    still visible, which was the point of naming them; they no longer sit in a
    denominator alongside a housing stipend.
    """
    jd = {
        "required_skills": ["Comfortable with one or more of C++, Python or TypeScript"],
        "technologies": ["C++", "TypeScript"],
        "keywords": ["trading", "electronic trading"],
    }
    score, report = _score(jd)
    assert score <= 40, score
    assert {"C++", "TypeScript"} <= set(report["missing"])
    assert report["required_total"] == 3
    # Not silently dropped: the domain gap is still on the report.
    assert {"trading", "electronic trading"} <= set(report["preferred_missing"])


def test_requirements_are_reported_as_a_count_for_context() -> None:
    """So a moderate score reads as "met 2 of 3", not as a broken tool."""
    jd = {"technologies": ["Python", "Go", "Rust"]}
    _score_value, report = _score(jd)
    assert (report["required_met"], report["required_total"]) == (2, 3)
