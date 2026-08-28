"""The noise that made a real run's Keyword Match read as a failure.

Live run on 2026-08-27: ByteDance / Software Engineer Intern (AI Platform) 2027
Summer. The page rendered, saved and reviewed 98/100, and the dial next to it
said 27 -- matched 4, missing 11. Three of the eleven were not requirements at
all so much as artefacts of how a short `required_skills` entry was scored:

  * "Experience with C/C++/Java/Go/Python" counted as ONE keyword no resume can
    contain verbatim, with the five languages inside it never recovered. The
    candidate writes Python, so this was a met requirement scored as failed.
  * "optimization techniques" and "optimization" counted as two separate asks,
    so one requirement took two slots in the denominator and the missing list
    said the same word twice in two spellings.
  * "problem-solving" counted as a must-have skill. Nobody answers that with a
    bullet, which is the same reason "communication skills" was already excluded.

The cause is one thing: a short entry took the verbatim path and therefore
skipped every bit of normalisation the prose path had. See
`_keyword_alternatives` in services/tailor.py.
"""
from __future__ import annotations

from job_os.services.tailor import (
    _compute_ats_from_document,
    _is_candidate_skill,
    _jd_requirements,
    _keyword_alternatives,
)

# The posting's short entries, as the parse delivered them.
BYTEDANCE = {
    "required_skills": [
        "Experience with C/C++/Java/Go/Python",
        "data structures",
        "algorithms",
        "problem-solving",
        "optimization techniques",
        "machine learning systems",
        "concurrent systems",
        "profiling tools",
        "architectures",
    ],
    "preferred_skills": ["computer graphics", "computer vision", "deep learning"],
    "technologies": ["AI Platform", "optimization"],
}

# A profile that writes Python, does optimization work and has shipped ML
# systems -- the case the dial got wrong.
RESUME = {
    "basics": {"summary": "AI platform engineer working in Python."},
    "work": [
        {
            "name": "Northeastern",
            "highlights": [
                "Cut inference latency with profiling and kernel optimization.",
                "Built machine learning pipelines over Python and Go services.",
                "Tuned data structures in a concurrent request path.",
            ],
        }
    ],
}


def _requirements(jd: dict) -> dict[str, tuple[str, ...]]:
    reqs, _prose, _excluded = _jd_requirements(jd)
    return {req.label: req.alternatives for req in reqs}


def test_a_slash_joined_language_list_is_satisfied_by_any_one_of_them() -> None:
    """The single largest miss on the real run."""
    alternatives, any_of = _keyword_alternatives("Experience with C/C++/Java/Go/Python")
    assert any_of is True
    assert {"C++", "Java", "Go", "Python"} <= set(alternatives)
    # The posting's own phrasing is kept as the label's first wording, so the
    # panel reads back what the employer actually wrote.
    assert alternatives[0] == "Experience with C/C++/Java/Go/Python"


def test_a_bare_single_character_segment_is_not_offered() -> None:
    """`_mentions` treats "#" as a boundary, so "C" would credit a C#-only resume."""
    alternatives, _any_of = _keyword_alternatives("C/C++/Java/Go/Python")
    assert "C" not in alternatives
    assert "C++" in alternatives


def test_a_two_segment_compound_is_still_not_split() -> None:
    """CI/CD is one skill. `_PROSE_SPLIT_RE` documents why, and that stands."""
    for compound in ("CI/CD", "Pub/Sub", "TCP/IP"):
        alternatives, any_of = _keyword_alternatives(compound)
        assert alternatives == [compound], compound
        assert any_of is False, compound


def test_a_filler_noun_on_the_end_is_another_wording_not_another_requirement() -> None:
    reqs = _requirements(
        {"required_skills": ["optimization techniques"], "technologies": ["optimization"]}
    )
    # One ask, not two, and the label is the posting's own phrasing.
    assert len(reqs) == 1
    label, alternatives = next(iter(reqs.items()))
    assert label == "optimization techniques"
    assert set(alternatives) == {"optimization techniques", "optimization"}


def test_a_trimmed_form_too_generic_to_mean_anything_is_not_offered() -> None:
    """The guardrail on trimming. "build systems" leaving "build" would match
    almost any resume ever written, which is flattery, not a match."""
    for entry in ("build systems", "design patterns", "test frameworks", "data pipelines"):
        alternatives, _any_of = _keyword_alternatives(entry)
        assert alternatives == [entry], entry
    # And the ones worth trimming still are.
    assert "profiling" in _keyword_alternatives("profiling tools")[0]
    assert "machine learning" in _keyword_alternatives("machine learning systems")[0]


def test_a_lead_in_is_stripped_so_the_skill_behind_it_can_match() -> None:
    for entry, skill in (
        ("Experience with Kubernetes", "Kubernetes"),
        ("Strong familiarity with PyTorch", "PyTorch"),
        ("Hands-on experience in Terraform", "Terraform"),
        ("Proficiency in Rust", "Rust"),
    ):
        alternatives, _any_of = _keyword_alternatives(entry)
        assert skill in alternatives, entry


def test_soft_skill_boilerplate_is_not_scored_as_a_must_have() -> None:
    """Arriving as its own short entry rather than inside a sentence."""
    for term in (
        "problem-solving",
        "problem solving",
        "critical thinking",
        "analytical skills",
        "attention to detail",
        "teamwork",
    ):
        assert not _is_candidate_skill(term), term
    # And a real technique that merely contains a filtered word survives.
    assert _is_candidate_skill("collaborative filtering")


def test_the_bytedance_dial_reflects_the_profile_it_measured() -> None:
    """The whole point: the same posting and a profile that genuinely fits.

    Not asserting an exact number -- that would pin the score to this fixture's
    wording rather than to the behaviour. What must hold is that the artefacts
    are gone from the denominator and the language requirement is met.
    """
    _score, report = _compute_ats_from_document(
        jd_parsed=BYTEDANCE,
        json_resume=RESUME,
        fallback_matched=[],
        fallback_missing=[],
    )
    scored = set(report["matched"]) | set(report["missing"])
    # "problem-solving" leaves the score entirely.
    assert "problem-solving" not in scored
    assert "problem-solving" in report["excluded_non_skills"]
    # "optimization" no longer appears twice under two spellings.
    assert "optimization" not in scored
    assert "optimization techniques" in report["matched"]
    # The language list is met by Python rather than missed as a blob.
    assert "Experience with C/C++/Java/Go/Python" in report["matched"]
    # And the derived wordings do their job on the rest of the short entries.
    assert {"machine learning systems", "profiling tools", "data structures"} <= set(
        report["matched"]
    )


def test_the_posting_still_reports_what_the_profile_does_not_hold() -> None:
    """Fairer must not mean flattering: absent asks stay absent."""
    _score, report = _compute_ats_from_document(
        jd_parsed=BYTEDANCE,
        json_resume=RESUME,
        fallback_matched=[],
        fallback_missing=[],
    )
    assert "architectures" in report["missing"]
    assert "computer graphics" in report["preferred_missing"]
    assert report["required_met"] < report["required_total"]
