"""Importing a job twice must not fail because the first import worked.

Sentry 96dd0d61, POST /api/v1/jobs/from-url: `MultipleResultsFound` out of
`upsert_company`. Adding a job from a URL guesses the employer from the link
slug and calls with no domain, because nothing has been fetched yet. The
background parse finishes later, learns the real domain, and calls again. The
uniqueness index is on `(lower(name), coalesce(domain, ''))`, so `(Acme, NULL)`
and `(Acme, acme.com)` are two legal rows, and production holds exactly that
pair for GlossGenius and for Workiva.

The lookup only constrained the domain when one was given, so the next
domain-less call matched both and raised. These are the two halves worth
pinning: the read never raises, and the pair stops forming in the first place.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://job_os:job_os@localhost/job_os"
)

from sqlalchemy import func, select  # noqa: E402

from job_os.db.models import Company  # noqa: E402
from job_os.services.companies import upsert_company  # noqa: E402


async def _rows(session, name: str) -> list[Company]:
    result = await session.execute(
        select(Company).where(func.lower(Company.name) == name.lower())
    )
    return list(result.scalars().all())


@pytest.mark.asyncio
async def test_the_import_sequence_that_raised_in_production(db_session) -> None:
    """URL slug first with no domain, then the parse with one, then again.

    The third call is the one that reached Sentry. It is a plain repeat of the
    first, and it failed only because the second had succeeded.
    """
    guessed = await upsert_company(db_session, name="Sentrytest", domain=None)
    parsed = await upsert_company(db_session, name="Sentrytest", domain="sentrytest.com")

    # No exception is the headline, and it is not the whole claim: the two
    # calls have to have meant the same employer.
    again = await upsert_company(db_session, name="Sentrytest", domain=None)

    assert parsed.id == guessed.id, "the domain should fill in the row we already had"
    assert again.id == guessed.id
    assert len(await _rows(db_session, "Sentrytest")) == 1


@pytest.mark.asyncio
async def test_a_pair_that_already_exists_is_read_deterministically(db_session) -> None:
    """The rows in production cannot be un-created, so reading them must work.

    Both are inserted directly here, the way the old code left them, and the
    domain-less lookup has to return one of them rather than raise, and the
    same one every time.
    """
    db_session.add_all(
        [
            Company(name="Legacypair", domain=None),
            Company(name="Legacypair", domain="legacypair.com"),
        ]
    )
    await db_session.flush()

    first = await upsert_company(db_session, name="Legacypair", domain=None)
    second = await upsert_company(db_session, name="Legacypair", domain=None)

    assert first.id == second.id
    # The better-known row wins: a company with a domain is the same company
    # with more established about it.
    assert first.domain == "legacypair.com"
    assert len(await _rows(db_session, "Legacypair")) == 2, "neither row is deleted"


@pytest.mark.asyncio
async def test_two_domains_under_one_name_stay_two_companies(db_session) -> None:
    """The merge is for a missing domain, not for a different one.

    Two employers really can share a name, and the index already treats them as
    distinct. Filling in a blank must not collapse them.
    """
    first = await upsert_company(db_session, name="Ambiguous", domain="ambiguous.io")
    second = await upsert_company(db_session, name="Ambiguous", domain="ambiguous.dev")

    assert first.id != second.id
    assert len(await _rows(db_session, "Ambiguous")) == 2


@pytest.mark.asyncio
async def test_the_name_is_matched_without_regard_to_case_or_padding(db_session) -> None:
    created = await upsert_company(db_session, name="  CaseCheck  ", domain=None)
    found = await upsert_company(db_session, name="casecheck", domain=None)

    assert found.id == created.id
    assert created.name == "CaseCheck", "stored trimmed, matched case-insensitively"
