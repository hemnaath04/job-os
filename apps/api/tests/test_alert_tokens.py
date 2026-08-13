"""Unsubscribe tokens have to survive being handled by strangers.

The link goes out in mail we do not control, gets prefetched by scanners, and
comes back through an endpoint with no session behind it. So the properties worth
pinning are: a token we made verifies, anything else does not, and a missing
secret stops the feature rather than quietly signing with nothing.
"""
from __future__ import annotations

import pytest

from job_os.services.alert_tokens import (
    UnsubscribeClaim,
    UnsubscribeScope,
    UnsubscribeSecretMissingError,
    UnsubscribeTokenError,
    make_token,
    parse_token,
    unsubscribe_url,
)
from job_os.settings import Settings

DB_URL = "postgresql+asyncpg://job_os:job_os@localhost/job_os"
SUBSCRIPTION_ID = "3f2504e0-4f89-11d3-9a0c-0305e82c3301"
USER_ID = "8a1b09c0-4f89-11d3-9a0c-0305e82c3302"


def settings(signing_value: str | None = "test-unsubscribe-secret") -> Settings:
    return Settings(database_url=DB_URL, alert_unsubscribe_secret=signing_value)


@pytest.mark.parametrize(
    ("scope", "subject"),
    [
        (UnsubscribeScope.SUBSCRIPTION, SUBSCRIPTION_ID),
        (UnsubscribeScope.ALL, USER_ID),
    ],
)
def test_a_token_we_issued_parses_back_to_the_same_claim(
    scope: UnsubscribeScope, subject: str
) -> None:
    from uuid import UUID

    config = settings()
    claim = UnsubscribeClaim(scope=scope, subject_id=UUID(subject))

    parsed = parse_token(make_token(claim, settings=config), settings=config)

    assert parsed.scope is scope
    assert parsed.subject_id == UUID(subject)


def test_the_two_scopes_do_not_produce_interchangeable_tokens() -> None:
    """Same subject id, different scope, must not verify as each other.

    Worth stating explicitly because the scope is the difference between turning
    off one alert and turning off all of a user's mail.
    """
    from uuid import UUID

    config = settings()
    subject = UUID(SUBSCRIPTION_ID)
    one = make_token(UnsubscribeClaim(UnsubscribeScope.SUBSCRIPTION, subject), settings=config)
    every = make_token(UnsubscribeClaim(UnsubscribeScope.ALL, subject), settings=config)

    assert one != every
    assert parse_token(one, settings=config).scope is UnsubscribeScope.SUBSCRIPTION
    assert parse_token(every, settings=config).scope is UnsubscribeScope.ALL


def test_a_token_signed_with_another_secret_is_rejected() -> None:
    from uuid import UUID

    issued = make_token(
        UnsubscribeClaim(UnsubscribeScope.ALL, UUID(USER_ID)), settings=settings("secret-a")
    )

    with pytest.raises(UnsubscribeTokenError):
        parse_token(issued, settings=settings("secret-b"))


@pytest.mark.parametrize(
    "token",
    [
        "",
        "not-a-token",
        "v1.only.three.",
        "v1.aaa.bbb",  # too few segments
        "v1.aaa.bbb.ccc.ddd",  # too many
        "v2.c3Vi.M2YyNTA0ZTAtNGY4OS0xMWQzLTlhMGMtMDMwNWU4MmMzMzAx.deadbeef",  # wrong version
    ],
)
def test_malformed_tokens_are_rejected(token: str) -> None:
    with pytest.raises(UnsubscribeTokenError):
        parse_token(token, settings=settings())


def test_flipping_one_character_of_the_signature_invalidates_the_token() -> None:
    from uuid import UUID

    config = settings()
    token = make_token(
        UnsubscribeClaim(UnsubscribeScope.SUBSCRIPTION, UUID(SUBSCRIPTION_ID)), settings=config
    )
    version, scope, subject, mac = token.split(".")
    flipped = "B" if mac[0] != "B" else "C"
    tampered = ".".join([version, scope, subject, flipped + mac[1:]])

    with pytest.raises(UnsubscribeTokenError):
        parse_token(tampered, settings=config)


def test_swapping_the_subject_while_keeping_the_signature_is_rejected() -> None:
    """The attack the MAC exists to stop: unsubscribe somebody else."""
    from uuid import UUID

    config = settings()
    mine = make_token(
        UnsubscribeClaim(UnsubscribeScope.ALL, UUID(USER_ID)), settings=config
    )
    version, scope, _subject, mac = mine.split(".")
    theirs = make_token(
        UnsubscribeClaim(UnsubscribeScope.ALL, UUID(SUBSCRIPTION_ID)), settings=config
    )
    _v, _s, other_subject, _m = theirs.split(".")

    with pytest.raises(UnsubscribeTokenError):
        parse_token(".".join([version, scope, other_subject, mac]), settings=config)


def test_a_missing_secret_raises_rather_than_signing_with_an_empty_key() -> None:
    """Failing loudly beats issuing a token every recipient could forge."""
    from uuid import UUID

    claim = UnsubscribeClaim(UnsubscribeScope.ALL, UUID(USER_ID))

    with pytest.raises(UnsubscribeSecretMissingError):
        make_token(claim, settings=settings(None))
    with pytest.raises(UnsubscribeSecretMissingError):
        make_token(claim, settings=settings("   "))


def test_the_url_is_absolute_and_carries_the_token_urlencoded() -> None:
    """CAN-SPAM opt-out is "visiting a single Internet Web page" (16 CFR 316.5),
    so the link has to be complete on its own, with no session and no second hop.
    """
    from urllib.parse import parse_qs, urlparse
    from uuid import UUID

    config = settings()
    claim = UnsubscribeClaim(UnsubscribeScope.SUBSCRIPTION, UUID(SUBSCRIPTION_ID))

    url = unsubscribe_url(claim, base_url="https://api.example.com/", settings=config)

    parsed = urlparse(url)
    assert parsed.scheme == "https"
    assert parsed.netloc == "api.example.com"
    # The trailing slash on the base must not produce a doubled slash.
    assert parsed.path == "/api/v1/alerts/unsubscribe"
    token = parse_qs(parsed.query)["token"][0]
    assert parse_token(token, settings=config) == claim
