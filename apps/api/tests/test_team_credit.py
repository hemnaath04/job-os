"""A rewrite may lose the weak opener. It may not lose the team.

The verified EPAM evidence reads "was part of a team building an AI agent that
generates test cases". A real tailored pass returned "Built agentic workflows
that generate test cases", which invents no metric and no technology, satisfies
every existing guard, and quietly turns shared work into solo work. The
independent review caught it as an ownership warning worth five points.

The rule is deliberately narrow: only evidence that names a TEAM triggers it.
"Worked on and extended the Go test suite" hedges scope, not authorship, and
rewriting it as "Wrote and maintained automated tests" is an improvement nobody
has objected to.
"""
from __future__ import annotations

from job_os.schemas.resumes import SelectedBullet
from job_os.services.resume_writing import drops_team_credit
from job_os.services.tailor import TailorBullet, TailorFact, _sanitize_selected_bullets

TEAM_FACT = (
    "In the latter half of the role, was part of a team building an AI agent that "
    "generates test cases directly from user stories, SRS, and FSDs. Demoed "
    "end-to-end; pending senior approval at the time I left."
)
SOLO_FACT = (
    "Worked on and extended the Go test suite for the Fares team's pricing engine; "
    "triaged daily failures and fixed flaky cases."
)


def test_deleting_the_team_is_an_ownership_claim() -> None:
    assert drops_team_credit(
        "Built agentic workflows that generate test cases from user stories.",
        TEAM_FACT,
    )


def test_keeping_the_team_with_a_real_verb_is_fine() -> None:
    # Exactly what the writing rules want: a concrete opener AND honest credit.
    assert not drops_team_credit(
        "Built, with a team, an AI agent that generates test cases from user stories.",
        TEAM_FACT,
    )
    assert not drops_team_credit(
        "Collaborated on an AI agent that generates test cases; demoed end to end.",
        TEAM_FACT,
    )


def test_evidence_that_never_named_a_team_is_left_alone() -> None:
    # The rule must not fire on ordinary scope hedging, or it would revert good
    # rewrites across most of the page.
    assert not drops_team_credit(
        "Wrote and maintained automated tests for a Go pricing engine.", SOLO_FACT
    )


def test_the_tailor_reverts_a_rewrite_that_drops_the_team() -> None:
    fact = TailorFact(id="f1", kind="experience", title="Engineer", org="EPAM")
    bullet = TailorBullet(id="b1", fact_id="f1", text=TEAM_FACT)
    safe = _sanitize_selected_bullets(
        [
            SelectedBullet(
                fact_bullet_id="b1",
                rewritten_text="Built agentic workflows that generate test cases.",
                target_section="work",
            )
        ],
        bullets_by_id={"b1": bullet},
        facts_by_id={"f1": fact},
    )
    # Reverted to the verified wording, which is already true and already credits
    # the team.
    assert safe[0].rewritten_text == TEAM_FACT


def test_a_rewrite_that_keeps_the_team_survives_the_tailor() -> None:
    fact = TailorFact(id="f1", kind="experience", title="Engineer", org="EPAM")
    bullet = TailorBullet(id="b1", fact_id="f1", text=TEAM_FACT)
    kept = "Built, with a team, an AI agent generating test cases; demoed, pending approval."
    safe = _sanitize_selected_bullets(
        [
            SelectedBullet(
                fact_bullet_id="b1", rewritten_text=kept, target_section="work"
            )
        ],
        bullets_by_id={"b1": bullet},
        facts_by_id={"f1": fact},
    )
    assert safe[0].rewritten_text == kept
