"""The checks that stand between a drafted message and a sent one.

Every one of these runs on the assembled body rather than on what the model
claimed about it, and every one is a plain function, so the guard that matters
most here is testable without a model, a database or a network.

The failure this file is really about is a fabricated shared connection. "I saw
we both worked at Stripe" sent to someone who can check it in one click is not a
bad sentence, it is an unrecoverable one, and the tests below are written from
the position that the model WILL eventually write it and Python has to be what
stops it.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://job_os:job_os@localhost/job_os"
)

from job_os.services.outreach import (  # noqa: E402
    MAX_FOLLOW_UPS,
    WORD_CAPS,
    DraftedClaim,
    OutreachDraftOutput,
    OutreachTarget,
    OutreachVariant,
    PriorContact,
    VerifiedBullet,
    VerifiedFact,
    add_business_days,
    double_message_block,
    drop_unbacked_sentences,
    follow_up_plan,
    org_matches,
    prior_contacts,
    review_draft,
    shared_context,
    unbacked_shared_claims,
)

EPAM = VerifiedFact(
    id="fact-epam",
    kind="experience",
    title="Software Test Automation Engineer",
    org="EPAM Systems",
    payload={"client": "Fares"},
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

# A body that passes every check, used as the baseline the failure cases mutate.
# One role sentence, one piece of evidence, one cheap ask, and nothing else.
GOOD_BODY = (
    "I saw the backend engineer opening on the payments team and wanted to write "
    "directly. At EPAM I wrote Go integration tests for the Fares pricing "
    "service, which cut the manual regression pass from 3 hours to 25 minutes. "
    "Most of that work was deciding which flaky cases were real. Would you be "
    "open to a 15 minute call about what the team is measuring right now?"
)
GOOD_CLAIM = DraftedClaim(
    phrase="wrote Go integration tests for the Fares pricing service",
    evidence_kind="bullet",
    evidence_id="bullet-go",
)


def _output(body: str = GOOD_BODY, **overrides) -> OutreachDraftOutput:
    fields = {
        "subject": "backend role on payments, quick question",
        "body": body,
        "claims": [GOOD_CLAIM],
        "shared_context_ids": [],
    }
    fields.update(overrides)
    return OutreachDraftOutput(**fields)


def _review(
    output: OutreachDraftOutput,
    *,
    variant=OutreachVariant.COLD_HIRING_MANAGER,
    allowed=None,
):
    return review_draft(
        output,
        variant=variant,
        facts=FACTS,
        bullets=BULLETS,
        allowed_context=allowed or [],
    )


def _flat(review) -> list[str]:
    return [flag for flags in review.flags.values() for flag in flags]


# ---------------------------------------------------------------------------
# The baseline. If this ever fails, every negative test below proves nothing.
# ---------------------------------------------------------------------------


def test_a_grounded_message_passes_every_check() -> None:
    review = _review(_output())
    assert review.flags == {}
    assert review.word_count < WORD_CAPS[OutreachVariant.COLD_HIRING_MANAGER]
    assert [row.evidence_id for row in review.provenance] == ["bullet-go"]


# ---------------------------------------------------------------------------
# Provenance.
# ---------------------------------------------------------------------------


def test_every_provenance_row_carries_the_text_that_backs_it() -> None:
    review = _review(_output())
    row = review.provenance[0]
    assert row.evidence_kind == "bullet"
    assert "Go integration tests" in row.evidence_text
    # The phrase has to be findable in the message that ships, or the row proves
    # nothing about what was actually said.
    assert row.phrase.casefold() in review.body.casefold()


def test_a_body_with_no_citations_at_all_is_refused() -> None:
    review = _review(_output(claims=[]))
    assert "no_provenance" in _flat(review)


def test_an_unverified_fact_id_cannot_be_cited() -> None:
    """The vault hands over verified rows only, so an id outside that set is by
    definition a fact the user has not confirmed. It is dropped, not trusted."""
    review = _review(
        _output(
            claims=[
                DraftedClaim(
                    phrase="wrote Go integration tests",
                    evidence_kind="bullet",
                    evidence_id="bullet-unverified-draft",
                )
            ]
        )
    )
    assert any(
        flag.startswith("unknown_evidence_id(bullet-unverified-draft)")
        for flag in _flat(review)
    )
    assert review.provenance == []


def test_a_citation_whose_phrase_is_not_in_the_body_is_refused() -> None:
    review = _review(
        _output(
            claims=[
                DraftedClaim(
                    phrase="rebuilt the billing ledger",
                    evidence_kind="bullet",
                    evidence_id="bullet-go",
                )
            ]
        )
    )
    assert any(flag.startswith("phantom_provenance") for flag in _flat(review))


# ---------------------------------------------------------------------------
# The shared-context ledger. The reason this feature has guards at all.
# ---------------------------------------------------------------------------


def test_a_shared_school_needs_evidence_on_both_sides() -> None:
    both = shared_context(
        facts=FACTS,
        target=OutreachTarget(full_name="Priya Raman", shared_school="Northeastern University"),
    )
    assert [entry.kind for entry in both] == ["same_school"]
    assert both[0].fact_id == "fact-khoury"


def test_a_school_the_user_asserts_but_cannot_back_produces_nothing() -> None:
    """The user believing the recipient went to Stanford is one half of a claim.
    Without a verified Stanford fact of the user's own there is no common ground,
    however confident they were when they typed it."""
    assert (
        shared_context(
            facts=FACTS,
            target=OutreachTarget(full_name="Priya Raman", shared_school="Stanford"),
        )
        == []
    )


def test_a_shared_employer_the_vault_does_not_hold_produces_nothing() -> None:
    assert (
        shared_context(
            facts=FACTS,
            target=OutreachTarget(full_name="Priya Raman", shared_employer="Stripe"),
        )
        == []
    )


def test_a_named_referrer_stands_on_its_own() -> None:
    """The exception, and it is narrow: the user is reporting a conversation
    from their own life, and the field has to be filled in for it to exist."""
    found = shared_context(
        facts=FACTS,
        target=OutreachTarget(full_name="Priya Raman", referred_by="Dan Alvarez"),
    )
    assert [entry.kind for entry in found] == ["mutual_contact"]


def test_an_unsupported_shared_employer_claim_is_caught_in_the_body() -> None:
    """The guard that runs whatever the model cited, or did not cite."""
    body = (
        "I saw we both worked at Stripe, so I wanted to write directly about the "
        "backend engineer opening on the payments team."
    )
    review = _review(_output(body=body, claims=[]))
    assert any(flag.startswith("unbacked_shared_claim") for flag in _flat(review))
    assert review.has_unbacked_shared_claim


@pytest.mark.parametrize(
    "sentence",
    [
        "I also worked at Stripe on the payments side.",
        "As a fellow Northeastern grad I wanted to write.",
        "A mutual connection suggested I get in touch.",
        "We overlapped at EPAM before you moved.",
        "Since you were at Stripe you will know the problem.",
        "My former colleague mentioned your team.",
        "You and I both came out of test automation.",
        "I noticed your time at Stripe was on the payments team.",
    ],
)
def test_every_shape_of_common_ground_claim_is_caught_with_an_empty_ledger(
    sentence: str,
) -> None:
    assert unbacked_shared_claims(sentence, [])


def test_a_backed_school_claim_is_allowed_through() -> None:
    allowed = shared_context(
        facts=FACTS,
        target=OutreachTarget(full_name="Priya Raman", shared_school="Northeastern"),
    )
    assert unbacked_shared_claims("I also studied at Northeastern.", allowed) == []


def test_a_backed_school_does_not_license_an_unbacked_employer() -> None:
    """One piece of real common ground is not a licence for a second, invented
    one. This is the failure mode where a true alumni opener smuggles a false
    shared employer through behind it."""
    allowed = shared_context(
        facts=FACTS,
        target=OutreachTarget(full_name="Priya Raman", shared_school="Northeastern"),
    )
    assert unbacked_shared_claims("I also worked at Stripe.", allowed)


def test_citing_a_shared_context_id_that_does_not_exist_is_refused() -> None:
    review = _review(_output(shared_context_ids=["same_employer:invented"]))
    assert any(
        flag.startswith("unknown_shared_context_id") for flag in _flat(review)
    )


def test_dropping_a_sentence_removes_the_claim_and_keeps_the_rest() -> None:
    body = (
        "I saw we both worked at Stripe. At EPAM I wrote Go integration tests "
        "for the Fares pricing service. Would you be open to a short call?"
    )
    trimmed = drop_unbacked_sentences(body, [])
    assert "both worked at Stripe" not in trimmed
    assert "Go integration tests" in trimmed
    assert unbacked_shared_claims(trimmed, []) == []


def test_org_matching_survives_how_a_school_is_actually_written() -> None:
    assert org_matches("Northeastern University, Khoury College", "Northeastern")
    assert org_matches("Northeastern University - Khoury College", "Khoury College")
    assert org_matches("EPAM Systems", "epam systems")


@pytest.mark.parametrize(
    ("left", "right"),
    [
        # The one that matters: a substring match here would license telling a
        # Stripe engineer they used to be colleagues.
        ("Stripe", "Striped Systems Inc"),
        ("IIT", "Institute of Technology"),
        ("Meta", "Metabase"),
        ("Northeastern University", "Northwestern University"),
    ],
)
def test_two_different_places_never_match(left: str, right: str) -> None:
    assert not org_matches(left, right)


def test_a_near_miss_employer_cannot_earn_a_ledger_entry() -> None:
    """The same bug seen from where it would have done the damage."""
    striped = VerifiedFact(
        id="fact-striped", kind="experience", title="Engineer", org="Striped Systems Inc"
    )
    assert (
        shared_context(
            facts=[striped],
            target=OutreachTarget(full_name="Priya Raman", shared_employer="Stripe"),
        )
        == []
    )


@pytest.mark.parametrize(
    "sentence",
    [
        "Most of the flaky cases were real.",
        "I know that part of the codebase well.",
        "The migration was wed to the old schema.",
    ],
)
def test_ordinary_words_are_not_mistaken_for_first_person_plural(sentence: str) -> None:
    """`we'?re` also matches "were", and `we'?ll` also matches "well". A guard
    that rejects honest sentences is a guard the user learns to route around."""
    review = _review(_output(body=f"{GOOD_BODY} {sentence}", claims=[]))
    assert "first_person_plural" not in _flat(review)


@pytest.mark.parametrize("phrase", ["We both", "we're", "we’ve", "our team", "between us"])
def test_real_first_person_plural_is_still_caught(phrase: str) -> None:
    review = _review(_output(body=f"{phrase} worked on the same problem.", claims=[]))
    assert "first_person_plural" in _flat(review)


# ---------------------------------------------------------------------------
# Writing rules.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("variant", list(OutreachVariant))
def test_the_word_cap_is_a_ceiling_and_not_a_target(variant: OutreachVariant) -> None:
    cap = WORD_CAPS[variant]
    at_the_cap = " ".join(["word"] * cap)
    review = _review(_output(body=at_the_cap, claims=[]), variant=variant)
    assert any(flag.startswith("too_long") for flag in _flat(review))


def test_a_cold_message_is_capped_under_one_hundred_and_twenty_words() -> None:
    assert WORD_CAPS[OutreachVariant.COLD_HIRING_MANAGER] == 120
    body = " ".join(["word"] * 119)
    review = _review(_output(body=body, claims=[]))
    assert not any(flag.startswith("too_long") for flag in _flat(review))


@pytest.mark.parametrize(
    "word",
    [
        "leveraged",
        "utilized",
        "spearheaded",
        "cutting-edge",
        "robust",
        "seamlessly",
        "facilitated",
        "enabled",
        "end-to-end",
    ],
)
def test_every_banned_word_is_rejected(word: str) -> None:
    body = GOOD_BODY.replace("wrote Go integration tests", f"{word} Go integration tests")
    review = _review(_output(body=body, claims=[]))
    assert any("banned_wording" in flag and word in flag for flag in _flat(review))


def test_an_em_dash_is_replaced_rather_than_shipped() -> None:
    body = GOOD_BODY.replace(
        "and wanted to write directly", "— and wanted to write directly"
    )
    review = _review(_output(body=body))
    assert "—" not in review.body
    assert "dash" not in _flat(review)


@pytest.mark.parametrize("separator", ["—", "–", "--"])
def test_no_banned_separator_survives_into_the_subject(separator: str) -> None:
    review = _review(_output(subject=f"payments role {separator} quick question"))
    assert separator not in review.subject
    assert "dash" not in review.flags.get("subject", [])


def test_first_person_plural_is_rejected() -> None:
    body = GOOD_BODY.replace("Would you be open", "Could we find time")
    review = _review(_output(body=body, claims=[]))
    assert "first_person_plural" in _flat(review)


@pytest.mark.parametrize(
    "opener",
    [
        "I hope this email finds you well.",
        "I came across your profile and wanted to connect.",
        "I would love the opportunity to pick your brain.",
        "I am writing to express my strong interest in this role.",
    ],
)
def test_template_openers_are_rejected(opener: str) -> None:
    review = _review(_output(body=f"{opener} {GOOD_BODY}", claims=[]))
    assert any(flag.startswith("template_phrasing") for flag in _flat(review))


def test_a_subject_longer_than_an_inbox_shows_is_rejected() -> None:
    review = _review(
        _output(subject="a subject line that is far too long to be read in a list")
    )
    assert any(flag.startswith("subject_too_long") for flag in _flat(review))


# ---------------------------------------------------------------------------
# Claims about the candidate's own work.
# ---------------------------------------------------------------------------


def test_a_number_no_cited_evidence_carries_is_rejected() -> None:
    body = GOOD_BODY.replace("from 3 hours to 25 minutes", "by 94 percent")
    review = _review(_output(body=body))
    assert any(flag.startswith("unsupported_number") for flag in _flat(review))


def test_the_length_of_the_meeting_being_asked_for_is_not_a_metric() -> None:
    """"Would 15 minutes next week work" describes the ask, not the work, and
    demanding evidence for it would reject good drafts."""
    review = _review(_output())
    assert not any(flag.startswith("unsupported_number") for flag in _flat(review))


def test_a_technology_the_candidate_cannot_back_is_rejected() -> None:
    body = GOOD_BODY.replace("Go integration tests", "Kubernetes operators")
    review = _review(
        _output(
            body=body,
            claims=[
                DraftedClaim(
                    phrase="Kubernetes operators",
                    evidence_kind="bullet",
                    evidence_id="bullet-go",
                )
            ],
        )
    )
    assert any(
        flag.startswith("unsupported_technology(kubernetes)") for flag in _flat(review)
    )


def test_the_job_description_is_never_a_source_for_what_the_sender_has_done() -> None:
    """The posting asking for Kafka does not make Kafka something he has used.
    The technology check reads the candidate's evidence and nothing else."""
    body = GOOD_BODY.replace("Go integration tests", "Kafka consumers")
    review = _review(_output(body=body, claims=[GOOD_CLAIM]))
    assert any(flag.startswith("unsupported_technology") for flag in _flat(review))


def test_a_skill_the_user_does_not_write_never_reaches_a_message() -> None:
    body = GOOD_BODY.replace("Go integration tests", "TypeScript integration tests")
    review = _review(_output(body=body, claims=[]))
    assert any(flag.startswith("unprintable_skill") for flag in _flat(review))


def test_provisional_work_cannot_be_described_as_shipped() -> None:
    demo = VerifiedBullet(
        id="bullet-demo",
        fact_id="fact-epam",
        text="Built a prototype grading agent, demoed to the client and pending approval.",
    )
    body = (
        "I saw the backend engineer opening on the payments team. At EPAM I built "
        "a grading agent that shipped to the client. Would you be open to a short call?"
    )
    review = review_draft(
        OutreachDraftOutput(
            subject="backend role on payments",
            body=body,
            claims=[
                DraftedClaim(
                    phrase="built a grading agent",
                    evidence_kind="bullet",
                    evidence_id="bullet-demo",
                )
            ],
        ),
        variant=OutreachVariant.COLD_HIRING_MANAGER,
        facts=FACTS,
        bullets=[BULLET, demo],
        allowed_context=[],
    )
    assert "upgraded_status" in _flat(review)


# ---------------------------------------------------------------------------
# Follow-up timing and double messaging.
# ---------------------------------------------------------------------------


def test_a_follow_up_lands_on_a_working_day() -> None:
    friday = datetime(2026, 8, 14, 9, 0, tzinfo=UTC)
    assert add_business_days(friday, 1).weekday() == 0  # the following Monday
    assert add_business_days(friday, 5).date().isoformat() == "2026-08-21"


def test_the_follow_up_plan_stops_rather_than_suggesting_a_third_nudge() -> None:
    sent = datetime(2026, 8, 12, 9, 0, tzinfo=UTC)
    plan = follow_up_plan(
        variant=OutreachVariant.COLD_HIRING_MANAGER,
        sent_at=sent,
        prior_sends=MAX_FOLLOW_UPS + 1,
    )
    assert plan.suggested_at is None
    assert plan.is_final
    assert "Stop here" in plan.label


def test_the_first_follow_up_suggests_a_date_in_working_days() -> None:
    sent = datetime(2026, 8, 12, 9, 0, tzinfo=UTC)
    plan = follow_up_plan(variant=OutreachVariant.REFERRAL_ASK, sent_at=sent, prior_sends=1)
    assert plan.suggested_at == add_business_days(sent, 6)
    assert not plan.is_final


def test_no_prior_message_means_nothing_to_block() -> None:
    assert double_message_block(prior=[], now=datetime.now(UTC)) is None


def test_messaging_the_same_person_twice_in_a_week_is_blocked() -> None:
    now = datetime(2026, 8, 12, 9, 0, tzinfo=UTC)
    prior = [
        PriorContact(sent_at=now - timedelta(days=2), variant="referral_ask", channel="email")
    ]
    reason = double_message_block(prior=prior, now=now)
    assert reason is not None
    assert "referral ask" in reason
    assert "2026-08-10" in reason


def test_the_wait_expires() -> None:
    now = datetime(2026, 8, 12, 9, 0, tzinfo=UTC)
    prior = [
        PriorContact(sent_at=now - timedelta(days=6), variant="referral_ask", channel="email")
    ]
    assert double_message_block(prior=prior, now=now) is None


def test_a_fourth_message_is_blocked_however_long_the_gap() -> None:
    now = datetime(2026, 8, 12, 9, 0, tzinfo=UTC)
    prior = [
        PriorContact(
            sent_at=now - timedelta(days=40 - index * 10),
            variant="cold_hiring_manager",
            channel="email",
        )
        for index in range(MAX_FOLLOW_UPS + 1)
    ]
    reason = double_message_block(prior=prior, now=now)
    assert reason is not None
    assert "is the limit" in reason


# ---------------------------------------------------------------------------
# Reading history back out of the shared application event log.
# ---------------------------------------------------------------------------


class _Event:
    """The shape `application_events` rows present, without the database."""

    def __init__(self, kind: str, payload: dict, occurred_at: datetime) -> None:
        self.kind = kind
        self.payload = payload
        self.occurred_at = occurred_at


def test_only_sends_to_this_one_person_count_against_them() -> None:
    now = datetime(2026, 8, 12, 9, 0, tzinfo=UTC)
    events = [
        _Event("outreach_sent", {"contact_id": "a", "variant": "referral_ask"}, now),
        _Event("outreach_sent", {"contact_id": "b", "variant": "referral_ask"}, now),
        # A draft is not a send. Drafting is free and repeatable; only the user
        # saying they sent it counts.
        _Event("outreach_drafted", {"contact_id": "a", "variant": "referral_ask"}, now),
        _Event("status_change", {}, now),
    ]
    found = prior_contacts(events, contact_id="a")
    assert len(found) == 1
    assert found[0].variant == "referral_ask"


def test_prior_messages_come_back_oldest_first() -> None:
    now = datetime(2026, 8, 12, 9, 0, tzinfo=UTC)
    events = [
        _Event("outreach_sent", {"contact_id": "a"}, now),
        _Event("outreach_sent", {"contact_id": "a"}, now - timedelta(days=9)),
    ]
    found = prior_contacts(events, contact_id="a")
    assert [sent.sent_at for sent in found] == sorted(sent.sent_at for sent in found)
