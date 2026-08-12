"""How ready the candidate is for this interview, and why that number is defensible.

The whole point of deriving it in Python is that the same evidence always
produces the same number and every point of it names a topic. These cases pin
both properties, and the boundaries where an honest score has to refuse to guess.
"""
from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from _interview_fixtures import JD_PARSED, vault
from job_os.services.interview_prep import (
    READY_STRONG,
    ResumeBullet,
    readiness,
    topic_briefing,
    verified_facts_statement,
)


def _report(**kwargs):
    facts, bullets = vault()
    return readiness(jd_parsed=JD_PARSED, facts=facts, bullets_by_fact=bullets, **kwargs)


def test_the_same_evidence_always_produces_the_same_number() -> None:
    """No model in the path, so nothing to swing.

    The reviewing model's own 0-100 moved nine points across three identical
    reviews of one resume. A readiness score that moved like that would tell a
    candidate to spend a different evening preparing depending on when they
    clicked.
    """
    first = _report()
    second = _report()
    assert first.score == second.score
    assert [topic.status for topic in first.topics] == [
        topic.status for topic in second.topics
    ]


def test_every_topic_says_where_the_evidence_is() -> None:
    """A score is only explainable if each part of it can be checked."""
    report = _report()
    by_topic = {topic.topic: topic for topic in report.topics}
    assert by_topic["Python"].status == "evidenced"
    assert by_topic["Python"].citations
    assert by_topic["FastAPI"].status == "evidenced"
    assert "Job Searcher bullet b-js-api" in by_topic["FastAPI"].citations
    # Nothing in the vault mentions Kubernetes, and the report says so rather than
    # reaching for the nearest infrastructure-shaped fact.
    assert by_topic["Kubernetes"].status == "gap"
    assert by_topic["Kubernetes"].citations == []
    # The wordings that were tried are reported, so a gap cannot be dismissed as
    # the tool only having looked for one spelling.
    assert by_topic["Kubernetes"].alternatives == ["Kubernetes"]


def test_the_score_is_the_evidenced_share_of_the_must_haves() -> None:
    report = _report()
    expected = (
        Decimal(report.evidenced_topics) / Decimal(report.scored_topics) * Decimal("100")
    ).quantize(Decimal("0.1"))
    assert report.score == expected
    assert report.formula.startswith("readiness =")


def test_a_nice_to_have_is_reported_and_not_scored() -> None:
    """Missing a bonus item is not the interview bar.

    Averaging a posting's bonus stack in took a genuine match down to 35 on the
    resume side. The same arithmetic would tell a candidate who can answer every
    requirement that they are half prepared.
    """
    report = _report()
    bonus = [topic for topic in report.topics if topic.preferred]
    assert {topic.topic for topic in bonus} >= {"Terraform"}
    assert report.scored_topics == len([t for t in report.topics if not t.preferred])
    # The bonus items are present in the report and absent from the arithmetic:
    # counting them would have moved the number.
    assert bonus
    assert report.scored_topics < len(report.topics)
    assert report.evidenced_topics == len(
        [t for t in report.topics if not t.preferred and t.status == "evidenced"]
    )


def test_a_posting_with_nothing_scoreable_is_not_scored_zero() -> None:
    """Zero would be a statement about our parser dressed up as one about them."""
    facts, bullets = vault()
    report = readiness(jd_parsed={}, facts=facts, bullets_by_fact=bullets)
    assert report.score is None
    assert report.band == "not_scored"
    assert report.scored_topics == 0


def test_an_empty_vault_scores_thin_rather_than_erroring() -> None:
    report = readiness(jd_parsed=JD_PARSED, facts=[], bullets_by_fact={})
    assert report.score == Decimal("0.0")
    assert report.band == "thin"
    assert all(topic.status == "gap" for topic in report.topics)


def test_the_band_thresholds_are_reported_with_the_band() -> None:
    report = _report()
    assert report.thresholds["strong"] == READY_STRONG
    if report.score is not None and report.score >= READY_STRONG:
        assert report.band == "strong"


def test_a_bullet_whose_source_fact_is_gone_is_flagged_as_a_defence_risk() -> None:
    """The resume is out in the world; the evidence behind it is not.

    An interviewer holding a bullet the vault can no longer back is the most
    expensive surprise in the pack, and it is entirely detectable in advance.
    """
    report = _report(
        resume_bullets=[
            ResumeBullet(
                section="work",
                text="Cut pipeline runtime from 2 minutes to 10 seconds.",
                fact_bullet_id="b-deleted",
                fact_id="fact-epam",
            )
        ]
    )
    assert len(report.defence_risks) == 1
    assert "no longer in your verified profile" in report.defence_risks[0].reason


def test_a_number_the_candidate_marked_unconfirmed_is_flagged() -> None:
    report = _report(
        resume_bullets=[
            ResumeBullet(
                section="projects",
                text="Scored 2,404 sewer segments with a six-factor model.",
                fact_bullet_id="b-br-score",
                fact_id="fact-bedrocked",
            )
        ],
        unverified_metric_bullet_ids=frozenset({"b-br-score"}),
    )
    assert len(report.defence_risks) == 1
    assert "unconfirmed" in report.defence_risks[0].reason


def test_a_backed_bullet_is_not_a_defence_risk() -> None:
    report = _report(
        resume_bullets=[
            ResumeBullet(
                section="projects",
                text="Built a FastAPI service over PostgreSQL.",
                fact_bullet_id="b-js-api",
                fact_id="fact-jobsearcher",
            )
        ]
    )
    assert report.defence_risks == []


def test_defence_risks_stay_out_of_the_number() -> None:
    """Two different questions, two different answers, one number each.

    "Can you speak to what they asked for" and "what on your page will you be
    asked to defend" are both worth knowing. Averaged together they stop meaning
    anything.
    """
    clean = _report()
    risky = _report(
        resume_bullets=[
            ResumeBullet(
                section="work",
                text="Cut runtime by 90%.",
                fact_bullet_id="b-deleted",
                fact_id="fact-epam",
            )
        ]
    )
    assert clean.score == risky.score
    assert risky.defence_risks and not clean.defence_risks


def test_the_briefing_hands_the_model_the_answer_key() -> None:
    """The tailor's lesson: a model should not spend a pass deriving what Python knows."""
    briefing = topic_briefing(
        _report(
            resume_bullets=[
                ResumeBullet(
                    section="work",
                    text="Cut runtime by 90%.",
                    fact_bullet_id="b-deleted",
                    fact_id="fact-epam",
                )
            ]
        )
    )
    assert "BACKED BY VERIFIED EVIDENCE" in briefing
    assert "FastAPI  ->" in briefing
    assert "WORDED NOWHERE IN THE VAULT" in briefing
    assert "Kubernetes" in briefing
    assert "not part of the readiness number" in briefing
    # The instruction that keeps a gap a gap.
    assert "leave the scaffold null" in briefing
    # A claim the resume makes and the vault cannot back is named as a probe.
    assert "CLAIMS ALREADY ON THE RESUME" in briefing


def test_the_eligibility_line_never_becomes_a_scored_topic() -> None:
    """A degree requirement is not a thing evidence word-matches.

    Scoring it would deduct for the one requirement the candidate cannot fail,
    which is how a fair match reads as a weak one.
    """
    report = _report()
    scored = {topic.topic.casefold() for topic in report.topics}
    assert "bachelor's or master's in computer science or equivalent practical experience" not in (
        scored
    )
    assert any("bachelor" in sentence.casefold() for sentence in report.unscored_requirements)


def test_the_vault_query_still_filters_on_verified() -> None:
    """The storage-layer half of the no-fabrication contract.

    Dropping this filter would let a draft fact the user never confirmed become an
    answer scaffold, and nothing else in the stack would notice. The failure would
    surface in an interview.
    """
    sql = str(verified_facts_statement(uuid4()))
    assert "profile_facts.verified IS true" in sql
    assert "profile_facts.user_id =" in sql
