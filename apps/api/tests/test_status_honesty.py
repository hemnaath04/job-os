"""A rewrite must not promote a prototype into something that shipped.

The independent review caught this on a real run: the tailored summary said the
candidate "has shipped ... an AI agent for automated test generation" about work
the verified fact records as "demoed end-to-end; pending senior approval at the
time I left". No metric and no technology was invented, so the existing guards
passed it.
"""
from __future__ import annotations

from job_os.schemas.resumes import SelectedBullet
from job_os.services.resume_writing import upgrades_status
from job_os.services.tailor import (
    TailorBullet,
    TailorFact,
    _safe_summary,
    _sanitize_selected_bullets,
)

PENDING_FACT = (
    "In the latter half of the role, was part of a team building an AI agent that "
    "generates test cases from user stories, SRS, and FSDs. Demoed end-to-end; "
    "pending senior approval at the time I left."
)


def test_shipped_is_refused_when_the_evidence_says_pending() -> None:
    assert upgrades_status("Shipped an AI agent for test generation.", PENDING_FACT)
    assert upgrades_status("Launched an AI agent.", PENDING_FACT)


def test_present_tense_counts_as_a_completion_claim() -> None:
    """A summary reads in the present tense, which a past-tense pattern missed.

    The real line: "ships production FastAPI systems and agentic AI workflows",
    about work the evidence records as demoed and pending approval.
    """
    assert upgrades_status(
        "Backend engineer who ships production FastAPI systems and agentic AI "
        "workflows.",
        PENDING_FACT,
    )
    assert upgrades_status("Delivers agentic test tooling.", PENDING_FACT)


def test_carrying_the_qualifier_through_is_fine() -> None:
    assert not upgrades_status(
        "Built, with a team, an AI agent that generates test cases; demoed end to end.",
        PENDING_FACT,
    )


def test_evidence_that_already_claims_completion_is_not_downgraded() -> None:
    """The guard exists to stop the rewrite making a new claim, not to police facts."""
    shipped_fact = "Shipped a public Vercel deployment in a single day."
    assert not upgrades_status("Shipped a public deployment in one day.", shipped_fact)


# The BedRocked fact, whose payload names the event that makes every bullet on it
# provisional. The tailor hands the fact's payload to the guard alongside the
# bullet, so the hackathon marker is in scope even when the bullet does not
# mention it.
HACKATHON_FACT = (
    "Wired a natural-language search backed by Claude, strictly scoped to the "
    "dataset, and an Autodesk handoff that exports the dig-plan as a DXF.\n"
    '{"description": "Cyvl x Autodesk x NVIDIA x City of Boston Physical-AI Hackathon"}'
)


def test_production_as_an_adjective_is_a_completion_claim() -> None:
    """The wording that survived every guard and blocked a real review.

    The pattern used to require the preposition, so "in production" was caught
    and "a guardrailed production interface" was not, though both say the same
    thing: this runs for real. On a single-day hackathon build it was the one
    blocking issue on an otherwise clean 80-point review.
    """
    assert upgrades_status(
        "Wired a guardrailed production interface over civic data.", HACKATHON_FACT
    )
    assert upgrades_status(
        "Built a production-ready retrieval service over the sewer dataset.",
        HACKATHON_FACT,
    )
    assert upgrades_status(
        "Productionised the scoring model for all 2,404 segments.", HACKATHON_FACT
    )


def test_the_wider_pattern_does_not_flag_honest_wordings() -> None:
    # Same hackathon evidence, no completion claim: nothing to report.
    assert not upgrades_status(
        "Wired a natural-language search over the sewer dataset, scoped to it.",
        HACKATHON_FACT,
    )
    # Real, non-provisional work may say production, because nothing about the
    # evidence contradicts it.
    assert not upgrades_status(
        "Worked on the production pricing engine's Go regression suite.",
        "Worked on Go automated test suites for the rideshare client's pricing engine.",
    )


def test_a_bare_production_adjective_is_not_enough_to_reject_a_summary() -> None:
    """The summary is judged against every fact, so scope matters.

    A bullet rewrite came from one fact and describes it. The summary is one line
    about the whole page, tried against each bullet in turn, so a bare
    "production" cannot be pinned to whichever bullet is currently under test.
    Judged as strictly as a bullet, this real summary was rejected because an
    unrelated EPAM bullet is provisional, and the page lost its tailored lede.
    """
    honest_summary = (
        "Backend engineer who builds Python and FastAPI systems, backed by "
        "experience automating tests for a production rideshare pricing engine."
    )
    assert not upgrades_status(honest_summary, PENDING_FACT, text_is_about_source=False)
    # As a bullet about that very fact, the same adjective is a claim.
    assert upgrades_status(
        "Built a production interface for test generation.", PENDING_FACT
    )


def test_the_summary_still_cannot_claim_provisional_work_shipped() -> None:
    # Loosening the adjective must not loosen the explicit claims.
    for summary in (
        "Backend engineer who has shipped an AI agent for automated test generation.",
        "Backend engineer whose test-generation agent runs in production.",
        "Delivers agentic test tooling.",
    ):
        assert upgrades_status(summary, PENDING_FACT, text_is_about_source=False)


def test_a_bullet_that_upgrades_status_reverts_to_the_verified_wording() -> None:
    fact = TailorFact(id="f1", kind="experience", title="Engineer", org="EPAM Systems")
    source = TailorBullet(id="b1", fact_id="f1", text=PENDING_FACT)
    selected = _sanitize_selected_bullets(
        [
            SelectedBullet(
                fact_bullet_id="b1",
                rewritten_text="Shipped an AI agent that generates test cases.",
                target_section="work",
            )
        ],
        bullets_by_id={"b1": source},
        facts_by_id={"f1": fact},
    )
    assert [b.rewritten_text for b in selected] == [PENDING_FACT]


def test_a_summary_that_upgrades_status_is_dropped() -> None:
    facts_payload = [
        {"title": "EPAM Systems", "bullets": [{"text": PENDING_FACT}]},
        # Go and Python have to be in the evidence or the pre-existing technology
        # guard reverts the honest summary before the status check is reached.
        {"title": "Go", "kind": "skill", "bullets": []},
        {"title": "Python", "kind": "skill", "bullets": []},
    ]
    value, reason = _safe_summary(
        "Backend engineer who has shipped an AI agent for automated test generation.",
        master_json_resume={"basics": {}},
        facts_payload=facts_payload,
    )
    assert value is None
    # The reason travels with the refusal, so the loop can tell the model instead
    # of dropping the resume's lede without explanation and getting the same
    # overstatement back on the next pass.
    assert reason is not None
    assert "provisional" in reason

    # An honest summary over the same evidence survives, with nothing to report.
    honest = "Backend and test-automation engineer with Go and Python test suites."
    assert _safe_summary(
        honest,
        master_json_resume={"basics": {}},
        facts_payload=facts_payload,
    ) == (honest, None)
