"""`import_json_resume`'s dedup guard used a different key than tailor-time
merging (`services/identity.fact_identity`): (kind, org, title) with no dates,
against fact_identity's (kind, org, start_date, end_date) for experience and
education. A reimport that reworded a title -- exactly what a second resume
upload routinely does -- slipped past this guard and created a second row,
which only ever got folded back together at render time, never in the store
itself. This is what produced the real EPAM/BedRocked/MS duplicates.
"""
from __future__ import annotations

import pytest

from job_os.db.models import User
from job_os.services.profile_import import import_json_resume


async def _make_user(session, suffix: str) -> User:
    user = User(clerk_id=f"clerk_{suffix}", email=f"{suffix}@example.com")
    session.add(user)
    await session.flush()
    return user


def _resume(position: str) -> dict:
    return {
        "basics": {"name": "Ada Lovelace"},
        "work": [
            {
                "name": "EPAM Systems",
                "position": position,
                "startDate": "2023-06",
                "endDate": "2024-08",
                "highlights": ["Automated the regression suite"],
            }
        ],
    }


@pytest.mark.asyncio
async def test_a_retitled_reimport_is_recognized_as_the_same_job(db_session) -> None:
    user = await _make_user(db_session, "dedup-retitle")

    first = await import_json_resume(
        db_session,
        user=user,
        doc=_resume("Junior Software Test Automation Engineer, Client: leading global rideshare platform (Fares team)"),
    )
    # The one contact fact (from `basics`) plus the one experience fact.
    assert first.facts_created == 2

    # Same employer, same dates, reworded title -- what a second upload of the
    # same resume produces. The old (kind, org, title) key missed this; the
    # date-keyed fact_identity used by tailor.py's merge does not.
    second = await import_json_resume(
        db_session,
        user=user,
        doc=_resume("Software Test Automation Engineer"),
    )
    assert second.facts_created == 0
    # Both the repeated contact fact and the retitled experience are caught.
    assert second.facts_skipped == 2


@pytest.mark.asyncio
async def test_a_genuinely_different_stint_at_the_same_employer_is_not_merged(
    db_session,
) -> None:
    user = await _make_user(db_session, "dedup-different-stint")

    await import_json_resume(db_session, user=user, doc=_resume("Intern"))

    doc = _resume("Software Test Automation Engineer")
    doc["work"][0]["startDate"] = "2024-09"
    doc["work"][0]["endDate"] = "2025-06"
    report = await import_json_resume(db_session, user=user, doc=doc)

    # Different date range at the same org is a different job, not a
    # duplicate -- only the repeated contact fact is caught.
    assert report.facts_created == 1
    assert report.facts_skipped == 1
