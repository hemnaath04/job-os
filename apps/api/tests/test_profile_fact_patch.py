"""PATCH /profile/facts/{id}, through the route, against a real session.

Same failure as `test_job_description_endpoint.py` and worth its own file
because it is a different router: a handler that UPDATEs a row and then hands
the ORM object to a `Timestamped` response model has to repopulate `updated_at`
before anything reads it. The flush expires that column, since only the
database knows its new value, and reading it back during serialisation is IO
in an async request, which raises MissingGreenlet.

Found by sweeping for the class after the enrich endpoint hit it in production.
This one had been latent: the verify toggle on the Profile page goes through
here.
"""
from __future__ import annotations

import uuid

from job_os.db.models import ProfileFact, User
from job_os.routers.profile import patch_fact
from job_os.schemas.profile import ProfileFactPatch, ProfileFactRead


async def _user_with_fact(session) -> tuple[User, ProfileFact]:
    user = User(
        clerk_id=f"user_{uuid.uuid4().hex}",
        email=f"{uuid.uuid4().hex}@example.com",
    )
    session.add(user)
    await session.flush()

    fact = ProfileFact(user_id=user.id, kind="skill", title="Python", verified=False)
    session.add(fact)
    await session.flush()
    return user, fact


async def test_a_patched_fact_can_be_serialised(db_session):
    user, fact = await _user_with_fact(db_session)

    returned = await patch_fact(
        fact.id,
        ProfileFactPatch(verified=True),
        user=user,
        session=db_session,
    )

    # What FastAPI's response_model does once the handler returns. Before the
    # refresh this raised, so the assertion is simply that every field reads.
    read = ProfileFactRead.model_validate(returned)

    assert read.verified is True
    assert read.updated_at is not None
    assert read.created_at is not None
    assert read.bullets == []
