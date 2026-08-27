"""Importing the same resume twice must not double the vault.

This is the failure that actually happened, three times, and was never tested.

    2026-06-20   149 duplicate rows   three imports in one day
    2026-07-28   fix 1abeb5e "Stop re-importing a resume from duplicating its facts"
    2026-08-02     7 duplicate rows   AFTER the fix
    2026-08-20     3 duplicate rows   AFTER the fix
    2026-08-23   fix 86c18cb "Align import_json_resume's dedup key with fact_identity"
    2026-08-24     1 fact, created by hand, not an import

So there were two attempts, the first demonstrably failed twice, and the second
has never run against a real import. `test_profile_import_dedup.py` covers a
RETITLED reimport being recognised as the same job, which is a harder case and a
real one, but it never asserts that importing the SAME resume twice leaves the
vault the size it was.

The duplicates cost more than tidiness. They split his edits: three BedRocked
copies carried 6, 6 and 12 keywords, and the twelve he had just added to fix
that project's ranking lost the merge and were discarded (#62). They also
resurrect wording he corrected: two stale copies of an EPAM bullet say "Owned
and extended" where the current one says "Worked on and extended".
"""
from __future__ import annotations

import pytest
from sqlalchemy import func, select

from job_os.db.models import FactBullet, ProfileFact, User
from job_os.services.profile_import import import_json_resume


async def _make_user(session, suffix: str) -> User:
    user = User(clerk_id=f"clerk_{suffix}", email=f"{suffix}@example.com")
    session.add(user)
    await session.flush()
    return user


async def _counts(session, user: User) -> tuple[int, int]:
    facts = await session.scalar(
        select(func.count()).select_from(ProfileFact).where(ProfileFact.user_id == user.id)
    )
    bullets = await session.scalar(
        select(func.count())
        .select_from(FactBullet)
        .join(ProfileFact, FactBullet.fact_id == ProfileFact.id)
        .where(ProfileFact.user_id == user.id)
    )
    return facts or 0, bullets or 0


# Shaped like his real master: the kinds that actually duplicated.
MASTER = {
    "basics": {"name": "Ada Lovelace"},
    "work": [
        {
            "name": "EPAM Systems",
            "position": "Junior Software Test Automation Engineer",
            "startDate": "2024-07",
            "endDate": "2025-12",
            "highlights": [
                "Worked on and extended the Go test suite for the pricing engine",
                "Migrated legacy test suites to Cucumber and TestNG",
            ],
        }
    ],
    "education": [
        {
            "institution": "Northeastern University",
            "studyType": "Master of Science",
            "area": "Computer Science",
            "startDate": "2026-01",
            "endDate": "2028-05",
        }
    ],
    "projects": [
        {
            "name": "BedRocked",
            "keywords": ["Python", "FastAPI"],
            "highlights": ["Built a dig-readiness score for 2,404 sewer segments"],
        }
    ],
    "skills": [{"name": "Languages", "keywords": ["Python", "Go", "SQL"]}],
    "certificates": [{"name": "Machine Learning", "issuer": "Internshala"}],
}


@pytest.mark.asyncio
async def test_importing_the_same_resume_twice_does_not_double_the_vault(
    db_session,
) -> None:
    """The exact thing that happened on 2026-06-20, 08-02 and 08-20."""
    user = await _make_user(db_session, "twice")

    await import_json_resume(db_session, user=user, doc=MASTER)
    await db_session.flush()
    facts_once, bullets_once = await _counts(db_session, user)
    assert facts_once > 0, "the first import has to actually import something"

    await import_json_resume(db_session, user=user, doc=MASTER)
    await db_session.flush()
    facts_twice, bullets_twice = await _counts(db_session, user)

    assert facts_twice == facts_once, (
        f"re-importing the same resume created {facts_twice - facts_once} extra "
        "facts. This is the bug that produced 149 duplicate rows in one day."
    )
    assert bullets_twice == bullets_once, (
        f"re-importing created {bullets_twice - bullets_once} extra bullets."
    )


@pytest.mark.asyncio
async def test_three_imports_are_no_worse_than_two(db_session) -> None:
    """It was three imports in one day, not two. Idempotence has to hold."""
    user = await _make_user(db_session, "thrice")
    for _ in range(3):
        await import_json_resume(db_session, user=user, doc=MASTER)
        await db_session.flush()
    facts, _bullets = await _counts(db_session, user)

    user_once = await _make_user(db_session, "once")
    await import_json_resume(db_session, user=user_once, doc=MASTER)
    await db_session.flush()
    baseline, _ = await _counts(db_session, user_once)

    assert facts == baseline


@pytest.mark.asyncio
async def test_a_reimport_that_adds_something_still_adds_it(db_session) -> None:
    """Idempotence must not become "ignores the second import"."""
    user = await _make_user(db_session, "grew")
    await import_json_resume(db_session, user=user, doc=MASTER)
    await db_session.flush()
    before, _ = await _counts(db_session, user)

    grown = {
        **MASTER,
        "projects": [
            *MASTER["projects"],
            {"name": "ClaimFarm", "highlights": ["Built an agent that files a claim"]},
        ],
    }
    await import_json_resume(db_session, user=user, doc=grown)
    await db_session.flush()
    after, _ = await _counts(db_session, user)

    assert after == before + 1, "a genuinely new project has to arrive"


@pytest.mark.asyncio
async def test_a_project_retitled_only_by_punctuation_is_the_same_project(
    db_session,
) -> None:
    """His real vault holds both of these, as separate facts.

        BedRocked — Civic Sewer-Sequencing Platform
        BedRocked: Civic Sewer-Sequencing Platform

    An em dash became a colon between two exports of the same resume, and the
    import read them as two different projects. That is the mechanism most
    likely behind the duplicates that survived the July fix: the dedup key is
    the title, and the title moved.
    """
    user = await _make_user(db_session, "punct")
    dashed = {
        **MASTER,
        "projects": [
            {
                "name": "BedRocked — Civic Sewer-Sequencing Platform",
                "highlights": ["Built a dig-readiness score for 2,404 segments"],
            }
        ],
    }
    coloned = {
        **MASTER,
        "projects": [
            {
                "name": "BedRocked: Civic Sewer-Sequencing Platform",
                "highlights": ["Built a dig-readiness score for 2,404 segments"],
            }
        ],
    }
    await import_json_resume(db_session, user=user, doc=dashed)
    await db_session.flush()
    before, _ = await _counts(db_session, user)

    await import_json_resume(db_session, user=user, doc=coloned)
    await db_session.flush()
    after, _ = await _counts(db_session, user)

    assert after == before, (
        "the same project, punctuated differently, was imported as a second "
        "fact. This is how his vault came to hold both spellings of BedRocked, "
        "and it splits every edit made to either copy."
    )
