"""A summary must not claim a subject-matter domain the page cannot back.

The real case, from a tailored resume the user has: the summary said the AI work
processes "real-world geospatial and claims data" while no claims project was
selected. It invented no number, no technology and no completion verb, so every
existing guard passed it. The independent review caught it on one run and missed it
on another, which is why it belongs in a rule rather than a model.

These tests lean hard on the false-positive side, because a guard that fires on an
honest resume just recreates the edit-again loop it exists to prevent.
"""
from __future__ import annotations

from job_os.services.resume_writing import (
    document_quality_flags,
    unevidenced_domains,
)

# BedRocked evidences geospatial. Nothing here evidences claims.
GEOSPATIAL_ONLY = {
    "basics": {
        "name": "A Candidate",
        "summary": "Engineer whose AI work processes real-world geospatial and claims data.",
    },
    "work": [
        {
            "position": "Engineer",
            "highlights": [
                "Wrote automated tests for a Go pricing engine.",
                "Migrated legacy suites to Cucumber and TestNG.",
                "Investigated failing tests daily with developers.",
                "Trained new joiners on the internal tooling.",
            ],
        }
    ],
    "projects": [
        {
            "name": "BedRocked",
            "highlights": [
                "Scored 2,404 sewer segments by fusing street-scan data with public GIS.",
                "Trained a catch-basin classifier by knowledge distillation.",
                "Wired a natural-language search scoped to the dataset.",
            ],
        },
        {
            "name": "Job Searcher",
            "highlights": [
                "Deployed a multi-user web app that scores postings against a resume.",
                "Hardened the API behind nginx and TLS.",
            ],
        },
    ],
}


def test_the_real_overclaim_is_caught() -> None:
    flags = document_quality_flags(GEOSPATIAL_ONLY)
    assert "basics.summary" in flags
    claim = next(f for f in flags["basics.summary"] if f.startswith("unevidenced_domain"))
    assert "claims" in claim
    # Geospatial IS evidenced, by GIS and sewer segments, so it must not be flagged.
    assert "geospatial" not in claim


def test_a_domain_evidenced_in_different_words_does_not_trip_it() -> None:
    """The conservative half. A bullet supporting the domain counts however it is worded."""
    # "claims" in the summary, evidenced by a bullet that says insurance instead.
    assert unevidenced_domains(
        "Builds AI that turns a photo into a filed claim.",
        "an agent that grades damage and files an insurance claim for a farmer",
    ) == []
    # "geospatial" evidenced by GIS, never by the word geospatial.
    assert unevidenced_domains(
        "Ships geospatial scoring pipelines.",
        "fused street-scan data with public sewer GIS across 2,404 segments",
    ) == []
    # "healthcare" evidenced by an infant-cry classifier.
    assert unevidenced_domains(
        "Builds clinical audio models.",
        "classify infant cry types from raw audio for early diagnosis",
    ) == []
    # "hiring" evidenced by a job-matching app.
    assert unevidenced_domains(
        "Builds recruiting tooling.",
        "scores job postings against an uploaded resume for each candidate",
    ) == []


def test_a_word_outside_the_curated_list_can_never_trip_it() -> None:
    """False positives are bounded by the list, which is the point of curating one."""
    assert unevidenced_domains(
        "Backend engineer who builds streaming pipelines and agentic workflows.",
        "wrote automated tests for a pricing engine",
    ) == []


def test_a_summary_that_claims_nothing_it_cannot_back_is_clean() -> None:
    honest = {
        **GEOSPATIAL_ONLY,
        "basics": {
            "name": "A Candidate",
            "summary": "Backend engineer who builds geospatial scoring pipelines and LLM services.",
        },
    }
    flags = document_quality_flags(honest)
    assert not any(
        f.startswith("unevidenced_domain")
        for f in flags.get("basics.summary", [])
    )


def test_the_summary_cannot_evidence_itself() -> None:
    """Otherwise every claim is self-proving and the guard does nothing."""
    self_proving = {
        "basics": {"summary": "Builds claims processing systems for insurance."},
        "work": [{"position": "Engineer", "highlights": ["Wrote a pricing engine test suite."]}],
    }
    flags = document_quality_flags(self_proving)
    assert any(
        f.startswith("unevidenced_domain")
        for f in flags.get("basics.summary", [])
    )


def test_no_summary_means_nothing_to_judge() -> None:
    assert unevidenced_domains("", "anything at all") == []


def test_a_polysemous_word_is_not_evidence() -> None:
    """The flaw only a real run found.

    "claims" is not evidence of insurance-claims work, because a real resume said
    "atomic MongoDB worker claims", which is a concurrency primitive. Evidence has to
    be a word only the domain uses, so the bare noun was removed from the evidence
    set and the guard now catches the overclaim it was letting through.
    """
    concurrency = "built a parallel fetcher with atomic MongoDB worker claims"
    assert unevidenced_domains("processes real-world claims data", concurrency) == ["claims"]
    # An actual insurance project still evidences it.
    insurance = "an agent that turns a crop photo into a filed insurance claim"
    assert unevidenced_domains("processes real-world claims data", insurance) == []


def test_ordinary_engineering_prose_is_not_evidence_of_a_trading_desk() -> None:
    """"cost and latency trade-offs" must not evidence securities trading."""
    assert unevidenced_domains(
        "Builds low-latency trading systems.",
        "managed context and prompt templates against cost and latency trade-offs",
    ) == ["trading"]
