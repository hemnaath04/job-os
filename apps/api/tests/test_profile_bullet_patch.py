"""PATCH /profile/bullets/{id}: the one edit the vault could not make.

Adding a bullet and deleting one both worked. Changing a word did not, so
fixing a typo meant deleting the bullet and retyping it, which threw away the
original. It also left the tailor arguing with facts nobody could edit: eleven
of this user's fifteen bullets are over the resume's word cap and seven of
fifteen open with the same verb, the resume inherits both, and no rule can fix
either, because shortening a claim means deciding which part of it to drop.

Through the route against a real session, like `test_profile_fact_patch.py`,
because a handler that UPDATEs a row and hands the ORM object to a
`Timestamped` response model has to repopulate `updated_at` first.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException

from job_os.db.models import FactBullet, ProfileFact, User
from job_os.routers.profile import delete_bullet, patch_bullet
from job_os.schemas.profile import FactBulletPatch, FactBulletRead

CLAIMFARM = (
    "Built an AI agent that turns a farmer's crop photo into a filed insurance "
    "claim in under a minute: a vision model grades damage, weather corroborates "
    "it, embeddings retrieve similar claims, and an LLM drafts a localized "
    "confirmation in 10 languages, behind a 6-signal fraud check."
)


async def _user_with_bullet(session, text: str = CLAIMFARM) -> tuple[User, FactBullet]:
    user = User(
        clerk_id=f"user_{uuid.uuid4().hex}",
        email=f"{uuid.uuid4().hex}@example.com",
    )
    session.add(user)
    await session.flush()
    fact = ProfileFact(user_id=user.id, kind="project", title="ClaimFarm", verified=True)
    session.add(fact)
    await session.flush()
    bullet = FactBullet(fact_id=fact.id, text=text, metric_verified=True)
    session.add(bullet)
    await session.flush()
    return user, bullet


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """The handler re-embeds, and a unit test has no business calling out."""
    calls: list[str] = []

    async def fake_embed_one(text: str) -> list[float]:
        calls.append(text)
        # The column is fixed at 1536, so a short vector fails in the driver
        # rather than in the assertion, and hides what the test is about.
        return [0.0] * 1536

    import job_os.services.embeddings as embeddings

    monkeypatch.setattr(embeddings, "embed_one", fake_embed_one)
    return calls


async def test_a_patched_bullet_can_be_serialised(db_session):
    """The MissingGreenlet trap `patch_fact` documents, on the bullet route."""
    user, bullet = await _user_with_bullet(db_session)

    returned = await patch_bullet(
        bullet.id,
        FactBulletPatch(text="Built an AI agent that files a crop-insurance claim."),
        user=user,
        session=db_session,
    )

    read = FactBulletRead.model_validate(returned)
    assert read.text == "Built an AI agent that files a crop-insurance claim."
    assert read.updated_at is not None
    assert read.created_at is not None


async def test_shortening_his_own_bullet_is_what_this_is_for(db_session):
    user, bullet = await _user_with_bullet(db_session)
    assert len(bullet.text.split()) > 30

    shorter = (
        "Built an AI agent that turns a crop photo into a filed insurance claim: "
        "a vision model grades damage and an LLM drafts the confirmation."
    )
    returned = await patch_bullet(
        bullet.id, FactBulletPatch(text=shorter), user=user, session=db_session
    )
    assert len(returned.text.split()) <= 30


async def test_an_edit_that_does_not_touch_the_wording_does_not_re_embed(
    db_session, _no_network
):
    """The embedding is a network call, not something to spend on a checkbox."""
    user, bullet = await _user_with_bullet(db_session)

    await patch_bullet(
        bullet.id,
        FactBulletPatch(metric_verified=False),
        user=user,
        session=db_session,
    )

    assert _no_network == []


async def test_new_wording_is_re_embedded(db_session, _no_network):
    """Retrieval reads the embedding, so stale text there is a silent wrong answer."""
    user, bullet = await _user_with_bullet(db_session)

    await patch_bullet(
        bullet.id, FactBulletPatch(text="Built something else."), user=user, session=db_session
    )

    assert _no_network == ["Built something else."]


async def test_an_unmentioned_field_is_left_alone(db_session):
    """`exclude_unset`, so editing wording cannot quietly unverify a metric."""
    user, bullet = await _user_with_bullet(db_session)
    assert bullet.metric_verified is True

    returned = await patch_bullet(
        bullet.id, FactBulletPatch(text="Built a shorter thing."), user=user, session=db_session
    )

    assert returned.metric_verified is True


async def test_another_persons_bullet_is_not_found(db_session):
    """Ownership is joined through the fact, the same way the delete route does."""
    _owner, bullet = await _user_with_bullet(db_session)
    stranger = User(
        clerk_id=f"user_{uuid.uuid4().hex}",
        email=f"{uuid.uuid4().hex}@example.com",
    )
    db_session.add(stranger)
    await db_session.flush()

    with pytest.raises(HTTPException) as raised:
        await patch_bullet(
            bullet.id, FactBulletPatch(text="Mine now."), user=stranger, session=db_session
        )
    assert raised.value.status_code == 404


async def test_deleting_still_checks_the_same_ownership(db_session):
    """The delete route now shares `_load_bullet`, so pin that it still guards."""
    _owner, bullet = await _user_with_bullet(db_session)
    stranger = User(
        clerk_id=f"user_{uuid.uuid4().hex}",
        email=f"{uuid.uuid4().hex}@example.com",
    )
    db_session.add(stranger)
    await db_session.flush()

    with pytest.raises(HTTPException) as raised:
        await delete_bullet(bullet.id, user=stranger, session=db_session)
    assert raised.value.status_code == 404
