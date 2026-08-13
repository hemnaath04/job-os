"""The no-fabrication contract, applied to prose.

Every test here is a way a letter could say something the vault cannot prove.
The resume pipeline only has to police rewritten bullets; a letter is mostly
connective prose, so it also has to police the sentences that claim to be
connective and are not.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://job_os:job_os@localhost/job_os"
)

from _cover_letter_fixtures import (
    BEDROCKED_SCORE,
    EPAM_AGENT,
    EPAM_TESTS,
    JD_PARSED,
    MASTER_RESUME,
    UNVERIFIED_BULLET,
    letter,
    say,
    vault,
)
from job_os.schemas.resumes import GapQuestion  # noqa: E402
from job_os.services.cover_letter import (  # noqa: E402
    assemble_letter,
    derive_gap_questions,
)


def _assemble(agent, **kwargs):
    facts, bullets = vault()
    return assemble_letter(
        agent,
        facts=facts,
        bullets_by_fact=bullets,
        master_json_resume=MASTER_RESUME,
        company="Corvus Systems",
        role="Backend Engineer, Platform",
        **kwargs,
    )


def test_every_claim_traces_to_a_real_fact_bullet() -> None:
    """The contract, stated as an assertion.

    Not "most claims" and not "the model said so": every provenance row names a
    bullet that exists in the vault, and every row's text is the exact sentence
    printed at the paragraph and sentence index it points to. A row that pointed
    somewhere else would be provenance in name only.
    """
    result = _assemble(
        letter(
            body=[
                [
                    say(
                        "I wrote Python and Go test suites against a rideshare "
                        "pricing engine and triaged the failures they produced.",
                        EPAM_TESTS,
                    ),
                ],
                [
                    say(
                        "On BedRocked I scored 2,404 sewer segments on a "
                        "six-factor index and served the result from FastAPI.",
                        BEDROCKED_SCORE,
                    ),
                ],
            ]
        )
    )
    _facts, bullets = vault()
    known = {b.id for group in bullets.values() for b in group}

    assert len(result.provenance) == 2
    assert result.refused == []
    for row in result.provenance:
        assert row.fact_bullet_id in known
        printed = result.document.paragraphs[row.paragraph]
        assert row.text in printed
    # And the reverse direction: the rows account for every attributed sentence,
    # so a claim cannot print without one.
    assert {row.fact_bullet_id for row in result.provenance} == {
        EPAM_TESTS,
        BEDROCKED_SCORE,
    }


def test_a_sentence_citing_an_unverified_fact_never_prints() -> None:
    """An unverified fact is a draft the user has not confirmed.

    The loader never puts one in the vault, so its bullet id arrives as an id
    Python has never heard of. That has to be a refusal rather than a
    pass-through, because the alternative is a letter claiming Kubernetes
    experience on the strength of a row the user never agreed to.
    """
    result = _assemble(
        letter(
            body=[
                [
                    say(
                        "I ran production workloads on Kubernetes for two years.",
                        UNVERIFIED_BULLET,
                    )
                ]
            ]
        )
    )
    assert [refusal.reason for refusal in result.refused] == ["unknown_fact_bullet_id"]
    assert result.provenance == []
    assert "Kubernetes" not in " ".join(result.document.paragraphs)


@pytest.mark.parametrize(
    ("sentence", "reason"),
    [
        (
            "I have spent 4 years writing backend services.",
            "unattributed_number",
        ),
        (
            "I am comfortable in Kubernetes and Terraform.",
            "unattributed_technology",
        ),
        (
            "I built the internal tooling that team relied on.",
            "unattributed_claim",
        ),
    ],
)
def test_an_unattributed_sentence_cannot_smuggle_a_claim(
    sentence: str, reason: str
) -> None:
    """The hole a prose generator would otherwise leave open.

    A resume is nothing but attributed bullets, so the tailoring pipeline never
    needed this check. A letter is mostly prose, and a sentence that claims four
    years of experience while pointing at no bullet at all is the single easiest
    way for a fabricated claim to reach the page.
    """
    result = _assemble(letter(body=[[say(sentence)]]))
    assert len(result.refused) == 1
    assert result.refused[0].reason.startswith(reason)
    assert sentence not in " ".join(result.document.paragraphs)


def test_the_posting_s_own_words_are_not_a_claim() -> None:
    """A role title full of technologies must not refuse the opening line.

    The check subtracts what the employer wrote from what the candidate claimed,
    so naming the role is free and claiming the skill is not. Without this a
    posting for a "Python Backend Engineer" could not have its own name said out
    loud in the letter applying for it.
    """
    facts, bullets = vault()
    result = assemble_letter(
        letter(opening=[say("I am applying for the Python Backend Engineer role.")]),
        facts=facts,
        bullets_by_fact=bullets,
        master_json_resume=MASTER_RESUME,
        company="Corvus Systems",
        role="Python Backend Engineer",
    )
    assert result.refused == []
    assert "Python Backend Engineer" in result.document.paragraphs[0]


def test_a_claim_cannot_add_a_metric_its_bullet_does_not_carry() -> None:
    result = _assemble(
        letter(
            body=[
                [
                    say(
                        "I wrote Python test suites that cut the pricing engine's "
                        "failure rate by 40%.",
                        EPAM_TESTS,
                    )
                ]
            ]
        )
    )
    assert len(result.refused) == 1
    assert result.refused[0].reason == "unverified_number(40%)"
    assert result.provenance == []


def test_a_claim_cannot_add_a_technology_its_bullet_does_not_carry() -> None:
    """The parent fact's payload counts as evidence, so this is a real test.

    The EPAM fact lists Python, Go and Jenkins, and a sentence naming Jenkins
    invents nothing even though the bullet text does not say it. Naming Kafka
    does.
    """
    result = _assemble(
        letter(
            body=[
                [
                    say(
                        "I wrote the Jenkins pipelines those Go suites ran in.",
                        EPAM_TESTS,
                    ),
                    say(
                        "I also moved the pricing events onto Kafka.",
                        EPAM_TESTS,
                    ),
                ]
            ]
        )
    )
    assert [refusal.reason for refusal in result.refused] == [
        "unverified_technology(kafka)"
    ]
    assert len(result.provenance) == 1


def test_a_demoed_prototype_cannot_become_shipped_work() -> None:
    """The failure the career-ops rules name explicitly.

    The EPAM agent was demoed end to end and pending senior approval. A letter
    saying it shipped is a claim an interviewer punctures in one question, and
    it invents no metric and no technology, so nothing else here would catch it.
    """
    result = _assemble(
        letter(
            body=[
                [
                    say(
                        "I shipped an AI agent that drafts test cases from "
                        "internal requirements documents.",
                        EPAM_AGENT,
                    )
                ]
            ]
        )
    )
    assert [refusal.reason for refusal in result.refused] == ["upgraded_status"]
    assert "shipped" not in " ".join(result.document.paragraphs)


def test_a_claim_cannot_take_a_team_s_credit() -> None:
    """Keeping the verb and deleting the people is a bigger claim, not a fix."""
    stolen = _assemble(
        letter(
            body=[[say("I built an AI agent over requirements documents.", EPAM_AGENT)]]
        )
    )
    assert [refusal.reason for refusal in stolen.refused] == ["dropped_team_credit"]

    shared = _assemble(
        letter(
            body=[
                [
                    say(
                        "I worked with a team on an AI agent over requirements "
                        "documents, demoed and pending approval when I left.",
                        EPAM_AGENT,
                    )
                ]
            ]
        )
    )
    assert shared.refused == []
    assert len(shared.provenance) == 1


def test_a_requirement_the_vault_cannot_support_becomes_a_gap_question() -> None:
    """Gaps are proved by Python, not volunteered by the model.

    Kubernetes is required by the posting and appears nowhere in the vault, so it
    is a gap whether or not the model noticed. Python is required and IS in the
    vault, so a model that reported it as a gap is simply wrong and asking the
    user to fill something already filled.
    """
    facts, bullets = vault()
    gaps = derive_gap_questions(
        jd_parsed=JD_PARSED,
        facts=facts,
        bullets_by_fact=bullets,
        model_gaps=[
            GapQuestion(requirement="Python", why_no_match="not seen"),
            GapQuestion(
                requirement="on-call rotation", why_no_match="no operations evidence"
            ),
        ],
    )
    labels = [gap.requirement for gap in gaps]
    assert "Kubernetes" in labels
    assert "Python" not in labels
    # A gap the model found that word matching would miss is kept, because that
    # is the only kind it is asked for.
    assert "on-call rotation" in labels
    # Nice-to-haves are reported by the resume pipeline separately and are not
    # failures, so they do not become questions here either.
    assert "Terraform" not in labels


def test_the_vault_loader_asks_only_for_verified_facts() -> None:
    """The perimeter, checked at the query rather than downstream of it.

    Every other guard in this module assumes an unverified fact never reaches the
    vault. This is the assumption itself: if the WHERE clause ever loses its
    `verified` filter, agent-proposed drafts become citable and every one of
    those guards passes while the contract is broken.
    """
    from job_os.services import cover_letter

    captured: list[object] = []

    class FakeResult:
        def scalars(self) -> FakeResult:
            return self

        def all(self) -> list[object]:
            return []

    class FakeSession:
        async def execute(self, statement: object) -> FakeResult:
            captured.append(statement)
            return FakeResult()

    import asyncio
    from uuid import uuid4

    facts, bullets = asyncio.run(
        cover_letter.load_verified_vault(FakeSession(), uuid4())
    )
    assert facts == [] and bullets == {}
    assert len(captured) == 1
    assert "profile_facts.verified IS true" in str(captured[0])
