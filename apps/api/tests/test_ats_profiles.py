"""Which applicant tracking system a posting belongs to, and what that costs.

The feature's whole claim is that the same resume is worth a different score
depending on who reads it, so the tests that matter are the ones that show two
platforms disagreeing about one document, and the ones that show detection
picking the right platform from a URL a user would actually paste.

There is a second claim worth pinning: that none of this touches the template.
The user picks the look. A tool that quietly swapped a two-column template for a
one-column one because Workday prefers it would be making a decision that is not
its to make, and `test_scoring_never_changes_the_template` is what stops that
from becoming true by accident later.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://job_os:job_os@localhost/job_os"
)

from job_os.services import ats_profiles  # noqa: E402
from job_os.services.ats_profiles import (  # noqa: E402
    DIMENSIONS,
    GENERIC,
    GREENHOUSE,
    LEVER,
    PROFILES,
    TALEO,
    WORKDAY,
    detect,
    evaluate,
    weighted_keyword_score,
)


def _document() -> dict:
    """A plausible early-career document, quantified in half its bullets."""
    return {
        "basics": {
            "name": "Hemnaath Balasubramani",
            "email": "balasubramani.h@northeastern.edu",
            "phone": "+1 617 555 0134",
            "summary": "Backend and AI engineer.",
        },
        "work": [
            {
                "name": "EPAM Systems",
                "position": "Software Engineer, Test Automation",
                "highlights": [
                    "Cut flaky failures from 14% of runs to under 1%.",
                    "Wrote Python suites against a pricing engine.",
                ],
            }
        ],
        "education": [
            {
                "institution": "Northeastern University",
                "area": "Computer Science",
                "studyType": "Master of Science",
                "endDate": "2028-05",
            }
        ],
        "skills": [{"name": "Languages", "keywords": ["Python", "Java", "SQL"]}],
        "projects": [
            {
                "name": "Ledger Reconciler",
                "highlights": ["Reconciles 200k rows in 8 seconds."],
            }
        ],
    }


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/job/x", "workday"),
        ("https://job-boards.greenhouse.io/anthropic/jobs/4020", "greenhouse"),
        ("https://jobs.lever.co/matchgroup/abc-123", "lever"),
        ("https://careers.icims.com/jobs/1234/engineer/job", "icims"),
        ("https://tas-example.taleo.net/careersection/jobdetail.ftl?job=9", "taleo"),
        ("https://ejlk.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/job/1", "taleo"),
        ("https://career5.successfactors.eu/careers?company=acme", "successfactors"),
        ("https://performancemanager.successfactors.com/careers", "successfactors"),
    ],
)
def test_a_posting_url_names_the_system_that_will_read_the_pdf(url: str, expected: str) -> None:
    assert detect(url).key == expected


def test_a_company_s_own_careers_page_falls_back_to_job_os_s_own_logic() -> None:
    """The product decision: outside the six modelled systems, use our own."""
    assert detect("https://apply.careers.microsoft.com/careers/job/197039").key == "generic"
    assert detect("https://example.com/jobs/1").key == "generic"


@pytest.mark.parametrize("value", [None, "", "   ", "not a url", "://broken"])
def test_a_missing_or_unparseable_url_scores_generic_rather_than_failing(value: str | None) -> None:
    """A pasted job description has no URL at all, and is still worth scoring."""
    assert detect(value).key == "generic"


def test_a_url_with_no_scheme_still_resolves() -> None:
    """A stored source_url is not reliably absolute.

    urlsplit reads a bare "boards.greenhouse.io/acme" as a path with no host,
    which detected as generic and silently gave every such posting the wrong
    profile.
    """
    assert detect("boards.greenhouse.io/acme/jobs/1").key == "greenhouse"


def test_a_lookalike_host_is_not_matched() -> None:
    """Suffix matching has to respect the label boundary.

    "notgreenhouse.io" and "greenhouse.io.evil.com" both contain the vendor
    string, and neither is Greenhouse.
    """
    assert detect("https://notgreenhouse.io/jobs/1").key == "generic"
    assert detect("https://greenhouse.io.evil.example/jobs/1").key == "generic"


# ---------------------------------------------------------------------------
# The profiles themselves
# ---------------------------------------------------------------------------


def test_every_profile_weights_all_six_dimensions_to_one() -> None:
    """Enforced at import too, but a silent renormalisation would be worse."""
    for profile in (*PROFILES, GENERIC):
        assert set(profile.weights) == set(DIMENSIONS), profile.key
        assert round(sum(profile.weights.values()), 6) == 1.0, profile.key


def test_every_profile_says_something_actionable_to_the_writer() -> None:
    """`guidance` is the tailoring half of the feature.

    A profile with an empty one would detect correctly, score correctly, and
    change nothing about the document, which is the failure mode most likely to
    go unnoticed.
    """
    for profile in (*PROFILES, GENERIC):
        assert len(profile.guidance) > 80, profile.key


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def test_the_same_document_scores_differently_on_two_platforms() -> None:
    """The premise of the whole module, asserted rather than assumed.

    Taleo weights keywords 0.35 and quantification 0.05. Lever inverts that.
    So the document that separates them hardest is the one this builds: every
    bullet quantified, and keyword coverage on the floor. That is a real
    candidate shape, not a contrived one. It is the strong-project, wrong-stack
    applicant, and the two platforms should disagree about them by enough to
    change a decision rather than by enough to show up only in a log.

    The 10-point floor is derived rather than guessed. Summing the weight
    differences against these dimension values gives about 11.7, so a change
    that drops the gap below 10 has changed the weights or the model, which is
    exactly when this should fail.
    """
    document = _document()
    document["work"][0]["highlights"] = [
        "Cut flaky failures from 14% of runs to under 1%.",
        "Reduced p95 latency from 900 ms to 210 ms.",
    ]
    document["projects"][0]["highlights"] = ["Reconciles 200k rows in 8 seconds."]

    taleo, _dims, _report = evaluate(
        document=document, keyword_score=10.0, columns=1, page_count=1, ats=TALEO
    )
    lever, dims, _report2 = evaluate(
        document=document, keyword_score=10.0, columns=1, page_count=1, ats=LEVER
    )
    assert dims.quantification == 100.0
    assert lever > taleo
    assert float(lever) - float(taleo) > 10.0


def test_a_two_column_template_costs_more_on_workday_than_on_lever() -> None:
    """Strictness is the mechanism, so it gets its own assertion.

    Same layout, same content, and the deduction differs by a factor of about
    two and a half between the strictest and the most lenient parser.
    """
    document = _document()
    one, _d, _r = evaluate(
        document=document, keyword_score=70.0, columns=1, page_count=1, ats=WORKDAY
    )
    two, _d2, _r2 = evaluate(
        document=document, keyword_score=70.0, columns=2, page_count=1, ats=WORKDAY
    )
    workday_cost = float(one) - float(two)

    one_l, _d3, _r3 = evaluate(
        document=document, keyword_score=70.0, columns=1, page_count=1, ats=LEVER
    )
    two_l, _d4, _r4 = evaluate(
        document=document, keyword_score=70.0, columns=2, page_count=1, ats=LEVER
    )
    lever_cost = float(one_l) - float(two_l)

    assert workday_cost > lever_cost
    assert workday_cost > 3.0


def test_the_report_names_what_it_did_not_check() -> None:
    """A formatting score of 100 must not read as "a parser will love this".

    job.os renders its own templates and none of them emit tables or images, so
    those checks are absent rather than passed. Saying so is the difference
    between a score and a claim.
    """
    _score, _dims, report = evaluate(
        document=_document(), keyword_score=70.0, columns=1, page_count=1, ats=WORKDAY
    )
    assert "tables" in report["not_checked"]
    assert "images" in report["not_checked"]


def test_a_pass_is_reported_against_the_platform_s_own_threshold() -> None:
    """50 on Lever is a pass and 50 on Taleo is not, which is the point."""
    document = _document()
    _s, _d, lever = evaluate(
        document=document, keyword_score=60.0, columns=1, page_count=1, ats=LEVER
    )
    _s2, _d2, taleo = evaluate(
        document=document, keyword_score=60.0, columns=1, page_count=1, ats=TALEO
    )
    assert lever["pass_threshold"] == 50
    assert taleo["pass_threshold"] == 75
    assert taleo["auto_rejects"] is True
    assert lever["auto_rejects"] is False


def test_quantification_is_the_plain_ratio_of_bullets_carrying_a_number() -> None:
    document = _document()
    # Two of the three highlights have digits in them.
    _score, dims, _report = evaluate(
        document=document, keyword_score=50.0, columns=1, page_count=1, ats=GREENHOUSE
    )
    assert dims.quantification == pytest.approx(66.0, abs=1.0)


def test_a_document_with_no_bullets_does_not_divide_by_zero() -> None:
    """A resume stripped to nothing is a real state during trimming."""
    bare = {"basics": {"name": "A", "email": "a@example.com"}}
    score, dims, _report = evaluate(
        document=bare, keyword_score=0.0, columns=1, page_count=1, ats=WORKDAY
    )
    assert dims.quantification == 0.0
    assert dims.experience_relevance == 0.0
    assert float(score) >= 0.0


def test_a_missing_required_section_is_named_not_just_counted() -> None:
    document = _document()
    del document["education"]
    _score, _dims, report = evaluate(
        document=document, keyword_score=70.0, columns=1, page_count=1, ats=WORKDAY
    )
    assert any("education" in note for note in report["notes"])


def test_passive_bullets_cost_experience_relevance() -> None:
    """"Responsible for" is the canonical low-scoring shape in every ATS guide."""
    strong = _document()
    weak = _document()
    weak["work"][0]["highlights"] = [
        "Responsible for various projects and tasks as assigned.",
        "Worked on the pricing engine.",
    ]
    weak["projects"] = []
    _s1, strong_dims, _r1 = evaluate(
        document=strong, keyword_score=50.0, columns=1, page_count=1, ats=LEVER
    )
    _s2, weak_dims, _r2 = evaluate(
        document=weak, keyword_score=50.0, columns=1, page_count=1, ats=LEVER
    )
    assert weak_dims.experience_relevance < strong_dims.experience_relevance


# ---------------------------------------------------------------------------
# Frequency weighting
# ---------------------------------------------------------------------------


def test_missing_a_term_the_posting_repeats_costs_more_than_missing_an_aside() -> None:
    """The reason frequency weighting was worth adding at all.

    Plain coverage says both resumes met one of two requirements and scores
    them identically. The posting does not agree: it says Python nine times and
    Terraform once.
    """
    jd = "Python " * 9 + "Terraform once. "
    missed_the_big_one, _w1 = weighted_keyword_score(
        matched=["Terraform"], missing=["Python"], jd_text=jd
    )
    missed_the_aside, _w2 = weighted_keyword_score(
        matched=["Python"], missing=["Terraform"], jd_text=jd
    )
    assert missed_the_aside > missed_the_big_one


def test_the_weighting_names_the_gaps_that_cost_the_most() -> None:
    """Actionable, not just a number: it says which absence to fix first."""
    jd = "Kubernetes " * 6 + "We also use Rust. "
    _score, weighting = weighted_keyword_score(
        matched=[], missing=["Kubernetes", "Rust"], jd_text=jd
    )
    assert weighting["most_repeated_gaps"] == ["Kubernetes"]


def test_frequency_weighting_saturates_rather_than_scaling_linearly() -> None:
    """A term said fifty times must not make every other requirement worthless.

    This is the part of BM25 that is actually being borrowed, so it is the part
    worth pinning.
    """
    shouty = "Python " * 50 + "Go once."
    score, _w = weighted_keyword_score(matched=["Go"], missing=["Python"], jd_text=shouty)
    # Linear weighting would put this near 2. Logarithmic keeps it defensible.
    assert score > 15.0


def test_weighting_is_skipped_when_there_is_nothing_to_weight() -> None:
    score, weighting = weighted_keyword_score(matched=[], missing=[], jd_text="")
    assert score == 0.0
    assert weighting["weighted"] is False


def test_an_empty_jd_falls_back_to_equal_weights() -> None:
    """A job imported without clean text still has requirements worth scoring."""
    score, _w = weighted_keyword_score(matched=["A"], missing=["B"], jd_text="")
    assert score == pytest.approx(50.0)


# ---------------------------------------------------------------------------
# The boundary the user drew
# ---------------------------------------------------------------------------


def test_scoring_never_changes_the_template() -> None:
    """The user's explicit decision: scoring and tailoring change, the look does not.

    `evaluate` takes the column count as an input and returns a score. It has no
    route to a template key and no way to suggest one, and this test exists so
    that adding one later is a deliberate act rather than a drift.
    """
    import inspect

    source = inspect.getsource(ats_profiles)
    assert "template_key" not in source
    signature = inspect.signature(evaluate)
    assert "template" not in " ".join(signature.parameters)


def test_an_unknown_profile_key_falls_back_instead_of_raising() -> None:
    """A stored run can name a platform a later build no longer defines."""
    assert ats_profiles.profile("a-platform-that-was-removed") is GENERIC
    assert ats_profiles.profile(None) is GENERIC
    assert ats_profiles.profile("workday") is WORKDAY
