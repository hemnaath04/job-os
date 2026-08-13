"""The provider write path and the loader that feeds the writer.

The manual provider is the whole of contact discovery today: the user finds a
name and an address themselves and pastes it in. That makes these tests less
exciting than the drafting guards and no less load bearing, because this is
where an address gets labelled with how much it can be trusted, and where the
key that stops the same person being stored twice is minted.
"""
from __future__ import annotations

import os
from typing import Any

import pytest

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://job_os:job_os@localhost/job_os"
)

from job_os.db.models.outreach import ContactRelationship, EmailSource  # noqa: E402
from job_os.services.contact_providers import (  # noqa: E402
    ContactProvider,
    ContactProviderError,
    ContactQuery,
    ManualContactProvider,
    get_contact_provider,
    manual_provider,
)
from job_os.services.outreach import load_verified_vault  # noqa: E402

# ---------------------------------------------------------------------------
# Accepting what the user pasted.
# ---------------------------------------------------------------------------


def test_a_pasted_contact_records_the_user_as_the_source() -> None:
    """Never claim a provider verified an address that no provider ever saw."""
    candidate = manual_provider().accept(
        full_name="  Priya   Raman ",
        title=" Engineering Manager ",
        email="priya@stripe.com",
        evidence_url="https://stripe.com/team",
        relationship=ContactRelationship.HIRING_MANAGER,
    )

    assert candidate.full_name == "Priya Raman"
    assert candidate.title == "Engineering Manager"
    assert candidate.source == "manual"
    assert candidate.email_source is EmailSource.USER_PROVIDED
    # No score, on purpose. A confidence number means somebody measured
    # something, and inventing 100 here is the same lie in the other direction.
    assert candidate.confidence is None
    assert candidate.evidence_url == "https://stripe.com/team"


def test_a_contact_with_no_address_is_still_worth_storing() -> None:
    """A name and a LinkedIn URL is a real state to be in. The draft can be
    written and sent by hand, and the tracking works the same either way."""
    candidate = manual_provider().accept(
        full_name="Priya Raman",
        linkedin_url="https://www.linkedin.com/in/priya-raman",
    )
    assert candidate.email is None
    assert candidate.email_source is None
    assert candidate.identity_key == "priya raman"


@pytest.mark.parametrize("name", ["", "   ", "\t\n"])
def test_a_contact_with_no_name_is_refused(name: str) -> None:
    with pytest.raises(ContactProviderError, match="name is required"):
        manual_provider().accept(full_name=name)


@pytest.mark.parametrize(
    "email",
    ["priya", "priya@stripe", "priya at stripe.com", "priya@@stripe.com", "priya @stripe.com"],
)
def test_an_address_that_would_bounce_is_refused_at_the_door(email: str) -> None:
    """The user finds out about a malformed address now, rather than after
    sending to it."""
    with pytest.raises(ContactProviderError, match="not a valid email"):
        manual_provider().accept(full_name="Priya Raman", email=email)


@pytest.mark.parametrize(
    "url",
    [
        "https://linkedin.com/company/stripe",
        "https://example.com/in/priya",
        "linkedin.com/in/priya",
    ],
)
def test_only_a_linkedin_person_url_is_accepted(url: str) -> None:
    with pytest.raises(ContactProviderError, match="linkedin.com/in/"):
        manual_provider().accept(full_name="Priya Raman", linkedin_url=url)


@pytest.mark.parametrize(
    "url",
    [
        "https://www.linkedin.com/in/priya-raman",
        "https://linkedin.com/in/priya-raman/",
        "http://uk.linkedin.com/in/priya_raman",
    ],
)
def test_the_shapes_a_person_url_actually_takes_are_allowed(url: str) -> None:
    assert manual_provider().accept(full_name="Priya Raman", linkedin_url=url).linkedin_url


def test_what_the_user_asserts_about_the_person_is_carried_but_not_blessed() -> None:
    """These three fields reach the ledger as an INPUT. On their own they license
    nothing: `shared_context` still has to find the other half in the vault."""
    candidate = manual_provider().accept(
        full_name="Priya Raman",
        shared_school=" Northeastern University ",
        shared_employer="EPAM Systems",
        referred_by="Dan Alvarez",
    )
    assert candidate.shared_school == "Northeastern University"
    assert candidate.shared_employer == "EPAM Systems"
    assert candidate.referred_by == "Dan Alvarez"


# ---------------------------------------------------------------------------
# Identity, which is what the double-message guard depends on.
# ---------------------------------------------------------------------------


def test_the_same_address_written_two_ways_is_one_person() -> None:
    first = manual_provider().accept(full_name="Priya Raman", email="Priya@Stripe.com")
    second = manual_provider().accept(full_name="P. Raman", email="priya@stripe.com  ")
    assert first.identity_key == second.identity_key == "priya@stripe.com"


def test_a_name_written_two_ways_is_one_person_when_there_is_no_address() -> None:
    first = manual_provider().accept(full_name="Priya  Raman")
    second = manual_provider().accept(full_name="priya raman")
    assert first.identity_key == second.identity_key


def test_two_different_people_do_not_collide() -> None:
    first = manual_provider().accept(full_name="Priya Raman", email="priya@stripe.com")
    second = manual_provider().accept(full_name="Dan Alvarez", email="dan@stripe.com")
    assert first.identity_key != second.identity_key


# ---------------------------------------------------------------------------
# The registry, and the seam a real vendor slots into.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_manual_provider_searches_for_nobody() -> None:
    """Honest rather than lazy. This provider does not search, the user does."""
    assert await ManualContactProvider().find(ContactQuery(company_name="Stripe")) == []


def test_the_manual_provider_satisfies_the_interface_a_vendor_would_implement() -> None:
    assert isinstance(manual_provider(), ContactProvider)


def test_an_unknown_provider_name_falls_back_rather_than_breaking_the_feature() -> None:
    """A typo in an environment variable should not take outreach down when a
    working default is sitting right there."""
    assert get_contact_provider("hunter-typo").id == "manual"


def test_the_default_provider_needs_no_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CONTACT_PROVIDER", raising=False)
    assert get_contact_provider().id == "manual"


# ---------------------------------------------------------------------------
# The loader. First line of the no-fabrication contract.
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> _Result:
        return self

    def all(self) -> list[Any]:
        return self._rows


class _RecordingSession:
    """Enough of AsyncSession to capture the statement that was run."""

    def __init__(self, rows: list[Any]) -> None:
        self.statements: list[Any] = []
        self._rows = rows

    async def execute(self, statement: Any) -> _Result:
        self.statements.append(statement)
        return _Result(self._rows)


@pytest.mark.asyncio
async def test_the_vault_loader_asks_the_database_for_verified_rows_only() -> None:
    """`verified=False` rows are drafts an agent proposed from a gap question and
    the user has never confirmed, so they may describe work that did not happen.
    The filter belongs in the query, not in a comment."""
    session = _RecordingSession([])

    facts, bullets = await load_verified_vault(session, "user-1")

    assert facts == []
    assert bullets == []
    where = str(session.statements[0])
    assert "profile_facts.verified" in where
    assert "profile_facts.user_id" in where


@pytest.mark.asyncio
async def test_no_verified_facts_means_no_second_query_for_bullets() -> None:
    """Nothing to attach bullets to, so the round trip is skipped."""
    session = _RecordingSession([])
    await load_verified_vault(session, "user-1")
    assert len(session.statements) == 1
