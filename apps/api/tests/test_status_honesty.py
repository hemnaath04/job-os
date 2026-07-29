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


def test_carrying_the_qualifier_through_is_fine() -> None:
    assert not upgrades_status(
        "Built, with a team, an AI agent that generates test cases; demoed end to end.",
        PENDING_FACT,
    )


def test_evidence_that_already_claims_completion_is_not_downgraded() -> None:
    """The guard exists to stop the rewrite making a new claim, not to police facts."""
    shipped_fact = "Shipped a public Vercel deployment in a single day."
    assert not upgrades_status("Shipped a public deployment in one day.", shipped_fact)


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
    assert (
        _safe_summary(
            "Backend engineer who has shipped an AI agent for automated test generation.",
            master_json_resume={"basics": {}},
            facts_payload=facts_payload,
        )
        is None
    )
    # An honest summary over the same evidence survives.
    honest = "Backend and test-automation engineer with Go and Python test suites."
    assert (
        _safe_summary(
            honest,
            master_json_resume={"basics": {}},
            facts_payload=facts_payload,
        )
        == honest
    )
