"""The drafting loop, against a model that is scripted to misbehave.

Every reply here is a fixture. Nothing in this file reaches a network, and the
interesting cases are the ones where the model does the thing the prompt told it
not to: claims a shared employer nobody can back, keeps claiming it after being
told, or answers with prose instead of JSON. The prompt cannot refuse any of
those. Python can, and these tests are the proof that it does.
"""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://job_os:job_os@localhost/job_os"
)

from _fake_llm import StreamingFakeMessages
from job_os.services import outreach  # noqa: E402
from job_os.services.llm_json import JSON_ONLY_RETRY  # noqa: E402
from job_os.services.outreach import (  # noqa: E402
    OutreachDraftRejected,
    OutreachTarget,
    OutreachVariant,
    PriorContact,
    VerifiedBullet,
    VerifiedFact,
    run_outreach_draft,
)

EPAM = VerifiedFact(
    id="fact-epam",
    kind="experience",
    title="Software Test Automation Engineer",
    org="EPAM Systems",
)
KHOURY = VerifiedFact(
    id="fact-khoury",
    kind="education",
    title="MS Computer Science",
    org="Northeastern University, Khoury College",
)
BULLET = VerifiedBullet(
    id="bullet-go",
    fact_id="fact-epam",
    text=(
        "Wrote Go integration tests for the Fares pricing service, cutting the "
        "manual regression pass from 3 hours to 25 minutes."
    ),
)
FACTS = [EPAM, KHOURY]
BULLETS = [BULLET]

JOB = {
    "title": "Backend Engineer, Payments",
    "company": "Stripe",
    "description": "Go services, high volume payments, strong testing culture.",
}
TARGET = OutreachTarget(
    full_name="Priya Raman",
    title="Engineering Manager, Payments",
    company_name="Stripe",
    relationship="hiring_manager",
)

CLEAN_BODY = (
    "I saw the backend engineer opening on the payments team and wanted to write "
    "directly. At EPAM I wrote Go integration tests for the Fares pricing "
    "service, which cut the manual regression pass from 3 hours to 25 minutes. "
    "Most of that work was deciding which flaky cases were real. Would you be "
    "open to a 15 minute call about what the team is measuring right now?"
)
CLEAN_CLAIM = {
    "phrase": "wrote Go integration tests for the Fares pricing service",
    "evidence_kind": "bullet",
    "evidence_id": "bullet-go",
}


def reply(
    body: str = CLEAN_BODY,
    *,
    subject: str = "backend role on payments, quick question",
    claims: list[dict[str, str]] | None = None,
    shared_context_ids: list[str] | None = None,
    note: str = "",
) -> str:
    return json.dumps(
        {
            "subject": subject,
            "body": body,
            "claims": [CLEAN_CLAIM] if claims is None else claims,
            "shared_context_ids": shared_context_ids or [],
            "note": note,
        }
    )


def _client(
    monkeypatch: pytest.MonkeyPatch, replies: list[str]
) -> tuple[Any, list[dict[str, Any]]]:
    """A fake Anthropic client that reads down a script of replies."""
    calls: list[dict[str, Any]] = []

    class FakeMessages(StreamingFakeMessages):
        async def create(self, **kwargs: Any) -> Any:
            calls.append(kwargs)
            text = replies[min(len(calls) - 1, len(replies) - 1)]
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text=text)],
                stop_reason="end_turn",
                usage=SimpleNamespace(output_tokens=300),
            )

    monkeypatch.setattr(
        outreach,
        "get_settings",
        lambda: SimpleNamespace(
            anthropic_api_key="test",
            anthropic_base_url="https://example.invalid",
            anthropic_model_tailor="manifest/auto",
            manifest_tier_sonnet="job-os-sonnet",
        ),
    )
    return SimpleNamespace(messages=FakeMessages()), calls


async def _draft(
    monkeypatch: pytest.MonkeyPatch,
    replies: list[str],
    *,
    variant: OutreachVariant = OutreachVariant.COLD_HIRING_MANAGER,
    target: OutreachTarget = TARGET,
    prior: list[PriorContact] | None = None,
    facts: list[VerifiedFact] | None = None,
    **kwargs: Any,
):
    client, calls = _client(monkeypatch, replies)
    draft = await run_outreach_draft(
        variant=variant,
        target=target,
        job=JOB,
        facts=FACTS if facts is None else facts,
        bullets=BULLETS,
        prior=prior,
        client=client,
        **kwargs,
    )
    return draft, calls


# ---------------------------------------------------------------------------
# The happy path, and what it is required to carry.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_clean_draft_comes_back_with_provenance_and_a_follow_up_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    draft, calls = await _draft(monkeypatch, [reply(note="No team detail in the posting.")])

    assert len(calls) == 1
    assert draft.body == CLEAN_BODY
    assert draft.word_count < outreach.WORD_CAPS[OutreachVariant.COLD_HIRING_MANAGER]
    assert [row.evidence_id for row in draft.provenance] == ["bullet-go"]
    assert "Go integration tests" in draft.provenance[0].evidence_text
    assert draft.follow_up.suggested_at is not None
    assert draft.follow_up.suggested_at.weekday() < 5
    assert draft.note == "No team detail in the posting."
    assert draft.warnings == []


@pytest.mark.asyncio
async def test_the_request_carries_the_house_rules_and_the_evidence_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _draft_result, calls = await _draft(monkeypatch, [reply()])

    system = calls[0]["system"]
    assert "resume quality gate" in system  # the shared career-ops rules
    assert "NEVER assert anything the two people have in common" in system
    prompt = calls[0]["messages"][0]["content"]
    assert "bullet-go" in prompt
    assert "fact-epam" in prompt
    # With no common ground, the ledger says so in as many words rather than
    # being quietly absent.
    assert "EMPTY. Claim nothing." in prompt
    assert calls[0]["extra_headers"] == {"x-manifest-tier": "job-os-sonnet"}


# ---------------------------------------------------------------------------
# Fabricated common ground. The failure this whole module exists to prevent.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_alumni_message_without_a_verified_shared_school_never_reaches_the_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refused before a token is spent. The variant IS the claim, so there is
    nothing to draft and nothing worth asking a model about."""
    client, calls = _client(monkeypatch, [reply()])

    with pytest.raises(OutreachDraftRejected) as raised:
        await run_outreach_draft(
            variant=OutreachVariant.ALUMNI,
            target=TARGET,
            job=JOB,
            facts=FACTS,
            bullets=BULLETS,
            client=client,
        )

    assert calls == []
    assert "alumni_variant_without_verified_shared_school" in str(raised.value)


@pytest.mark.asyncio
async def test_an_alumni_message_is_allowed_when_both_sides_hold_the_school(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = (
        "I also studied at Northeastern, which is why I am writing rather than "
        "only applying. At EPAM I wrote Go integration tests for the Fares "
        "pricing service. Would you be open to a short call about the payments "
        "team?"
    )
    draft, _calls = await _draft(
        monkeypatch,
        [reply(body, shared_context_ids=["same_school:fact-khoury"])],
        variant=OutreachVariant.ALUMNI,
        target=OutreachTarget(
            full_name="Priya Raman",
            company_name="Stripe",
            relationship="alumni",
            shared_school="Northeastern University",
        ),
    )

    assert [entry.kind for entry in draft.shared_context_used] == ["same_school"]
    assert "Northeastern" in draft.body


@pytest.mark.asyncio
async def test_a_fabricated_shared_employer_forces_a_repair_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fabricated = (
        "I saw we both worked at Stripe, so I wanted to write directly. At EPAM I "
        "wrote Go integration tests for the Fares pricing service. Would you be "
        "open to a short call?"
    )
    draft, calls = await _draft(monkeypatch, [reply(fabricated), reply()])

    assert len(calls) == 2
    repair = calls[1]["messages"][-1]["content"]
    assert "unbacked_shared_claim" in repair
    assert "delete that clause entirely" in repair
    assert "Stripe" not in draft.body
    assert draft.warnings == []


@pytest.mark.asyncio
async def test_a_model_that_will_not_let_go_of_the_claim_has_the_sentence_cut(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The last resort, and the only fix Python can make alone. Removing an
    additive claim can only leave the message more honest than it was."""
    fabricated = (
        "I saw we both worked at Stripe, so I wanted to write directly. At EPAM I "
        "wrote Go integration tests for the Fares pricing service. Would you be "
        "open to a short call?"
    )
    draft, calls = await _draft(monkeypatch, [reply(fabricated), reply(fabricated)])

    assert len(calls) == 2
    assert "both worked at Stripe" not in draft.body
    assert "Go integration tests" in draft.body
    assert draft.provenance  # the honest half kept its citation
    assert any("verified profile does not back it" in note for note in draft.warnings)


@pytest.mark.asyncio
async def test_a_message_that_is_only_a_fabricated_claim_is_refused_outright(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    only_a_lie = reply(
        "I saw we both worked at Stripe, so I wanted to reach out about the team.",
        claims=[],
    )
    client, _calls = _client(monkeypatch, [only_a_lie, only_a_lie])

    with pytest.raises(OutreachDraftRejected):
        await run_outreach_draft(
            variant=OutreachVariant.COLD_HIRING_MANAGER,
            target=TARGET,
            job=JOB,
            facts=FACTS,
            bullets=BULLETS,
            client=client,
        )


@pytest.mark.asyncio
async def test_a_shared_school_the_user_asserts_alone_licenses_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The user is sure the recipient went to Stanford. The vault holds no
    Stanford degree of the user's own, so there is no common ground to state."""
    body = (
        "I also studied at Stanford. At EPAM I wrote Go integration tests for the "
        "Fares pricing service. Would you be open to a short call?"
    )
    client, calls = _client(monkeypatch, [reply(body), reply(body)])

    draft = await run_outreach_draft(
        variant=OutreachVariant.COLD_HIRING_MANAGER,
        target=OutreachTarget(
            full_name="Priya Raman", company_name="Stripe", shared_school="Stanford"
        ),
        job=JOB,
        facts=FACTS,
        bullets=BULLETS,
        client=client,
    )

    assert "Stanford" not in draft.body
    assert draft.shared_context_used == []
    prompt = calls[0]["messages"][0]["content"]
    assert "EMPTY. Claim nothing." in prompt


# ---------------------------------------------------------------------------
# Unverified evidence.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_only_the_facts_handed_in_exist_for_the_writer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A `verified=False` row never enters the payload, so citing one is citing
    an id that does not exist. Twice over, and the draft is refused."""
    citing_a_draft_row = reply(
        claims=[
            {
                "phrase": "wrote Go integration tests for the Fares pricing service",
                "evidence_kind": "bullet",
                "evidence_id": "bullet-unverified",
            }
        ]
    )
    client, calls = _client(monkeypatch, [citing_a_draft_row, citing_a_draft_row])

    with pytest.raises(OutreachDraftRejected) as raised:
        await run_outreach_draft(
            variant=OutreachVariant.COLD_HIRING_MANAGER,
            target=TARGET,
            job=JOB,
            facts=FACTS,
            bullets=BULLETS,
            client=client,
        )

    assert "unknown_evidence_id(bullet-unverified)" in str(raised.value)
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_a_vault_with_nothing_verified_in_it_produces_no_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _calls = _client(monkeypatch, [reply(), reply()])

    with pytest.raises(OutreachDraftRejected):
        await run_outreach_draft(
            variant=OutreachVariant.COLD_HIRING_MANAGER,
            target=TARGET,
            job=JOB,
            facts=[],
            bullets=[],
            client=client,
        )


# ---------------------------------------------------------------------------
# Writing rules, end to end.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_em_dash_from_the_model_never_reaches_the_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dashed = CLEAN_BODY.replace(
        "and wanted to write directly", "— and wanted to write directly"
    )
    draft, calls = await _draft(
        monkeypatch, [reply(dashed, subject="payments role — quick question")]
    )

    assert len(calls) == 1  # fixed at assembly, not sent back for a rewrite
    assert "—" not in draft.body
    assert "—" not in draft.subject


@pytest.mark.asyncio
async def test_a_banned_word_survives_neither_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    banned = CLEAN_BODY.replace("I wrote Go", "I leveraged Go")
    client, calls = _client(monkeypatch, [reply(banned, claims=[]), reply(banned, claims=[])])

    with pytest.raises(OutreachDraftRejected) as raised:
        await run_outreach_draft(
            variant=OutreachVariant.COLD_HIRING_MANAGER,
            target=TARGET,
            job=JOB,
            facts=FACTS,
            bullets=BULLETS,
            client=client,
        )

    assert "banned_wording(leveraged)" in str(raised.value)
    assert "leveraged" in calls[1]["messages"][-1]["content"]


@pytest.mark.asyncio
async def test_a_message_over_the_cap_is_refused_rather_than_trimmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    long_body = CLEAN_BODY + " " + " ".join(["padding"] * 120)
    client, _calls = _client(
        monkeypatch, [reply(long_body, claims=[]), reply(long_body, claims=[])]
    )

    with pytest.raises(OutreachDraftRejected) as raised:
        await run_outreach_draft(
            variant=OutreachVariant.COLD_HIRING_MANAGER,
            target=TARGET,
            job=JOB,
            facts=FACTS,
            bullets=BULLETS,
            client=client,
        )

    assert "too_long" in str(raised.value)


@pytest.mark.asyncio
async def test_the_follow_up_variant_is_held_to_the_shortest_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert outreach.WORD_CAPS[OutreachVariant.POST_APPLICATION_FOLLOWUP] == 90
    body = " ".join(["word"] * 95)
    client, _calls = _client(monkeypatch, [reply(body, claims=[]), reply(body, claims=[])])

    with pytest.raises(OutreachDraftRejected) as raised:
        await run_outreach_draft(
            variant=OutreachVariant.POST_APPLICATION_FOLLOWUP,
            target=TARGET,
            job=JOB,
            facts=FACTS,
            bullets=BULLETS,
            client=client,
        )

    assert "cap 90" in str(raised.value)


# ---------------------------------------------------------------------------
# Prior contact.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_what_was_already_sent_is_put_in_front_of_the_writer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent_at = datetime.now(UTC) - timedelta(days=9)
    draft, calls = await _draft(
        monkeypatch,
        [reply()],
        prior=[PriorContact(sent_at=sent_at, variant="referral_ask", channel="email")],
    )

    prompt = calls[0]["messages"][0]["content"]
    assert "ALREADY SENT to this person" in prompt
    assert sent_at.date().isoformat() in prompt
    assert any("already went to this person" in note for note in draft.warnings)
    # Second message out, so the next nudge is the last one on offer.
    assert draft.follow_up.is_final


# ---------------------------------------------------------------------------
# A model that does not answer in JSON.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_chatty_reply_is_asked_once_more_and_then_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    draft, calls = await _draft(
        monkeypatch, ["Sure, here is a draft you might like.", reply()]
    )

    assert calls[1]["messages"][-1]["content"] == JSON_ONLY_RETRY
    assert draft.body == CLEAN_BODY


@pytest.mark.asyncio
async def test_two_unusable_replies_fail_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _calls = _client(monkeypatch, ["not json", "still not json"])

    with pytest.raises(OutreachDraftRejected) as raised:
        await run_outreach_draft(
            variant=OutreachVariant.COLD_HIRING_MANAGER,
            target=TARGET,
            job=JOB,
            facts=FACTS,
            bullets=BULLETS,
            client=client,
        )

    assert "invalid_json_after_retry" in str(raised.value)
