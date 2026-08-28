"""An unknown `kid` must not turn into one outbound JWKS fetch per inbound request.

The defect: `_find_key` refetched the JWKS whenever a token's `kid` was not in the
cache, with nothing limiting how often that could fire. `kid` is written by the
caller and read before any signature is verified, so a stream of tokens carrying
random `kid`s produced one request to Clerk per request to us, each holding a
connection for up to the 5s timeout. The end state is Clerk rate-limiting the app,
which fails authentication for real users -- an unauthenticated request amplifier.

The fix keeps the rotation recovery (that is what the refetch is for) but puts a
floor on how often it may fire, so the outbound rate no longer follows the inbound
one.
"""
from __future__ import annotations

import pytest

from job_os import auth

_JWKS_URL = "https://clerk.example.test/.well-known/jwks.json"


@pytest.fixture(autouse=True)
def _clear_module_state() -> None:
    # Both caches are module globals, so a test that leaves one populated changes
    # the answer for the next one.
    auth._JWKS_CACHE.clear()
    auth._JWKS_REFETCHED_AT.clear()


def test_first_unknown_kid_is_allowed_through() -> None:
    assert auth._refetch_allowed(_JWKS_URL, 1000.0) is True


def test_second_unknown_kid_inside_the_cooldown_is_refused() -> None:
    assert auth._refetch_allowed(_JWKS_URL, 1000.0) is True
    assert auth._refetch_allowed(_JWKS_URL, 1000.5) is False
    assert auth._refetch_allowed(_JWKS_URL, 1059.9) is False


def test_the_cooldown_expires_so_a_real_rotation_still_recovers() -> None:
    assert auth._refetch_allowed(_JWKS_URL, 1000.0) is True
    assert auth._refetch_allowed(_JWKS_URL, 1060.0) is True


def test_cooldown_is_per_jwks_url() -> None:
    other = "https://other.example.test/.well-known/jwks.json"
    assert auth._refetch_allowed(_JWKS_URL, 1000.0) is True
    assert auth._refetch_allowed(other, 1000.0) is True


async def test_a_flood_of_unknown_kids_makes_one_refetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The regression this exists for: 50 bogus tokens, not 50 calls to Clerk."""
    calls: list[bool] = []

    async def fake_get_jwks(jwks_url: str, *, force: bool = False) -> dict:
        calls.append(force)
        return {"keys": [{"kid": "real-key"}]}

    monkeypatch.setattr(auth, "_get_jwks", fake_get_jwks)
    monkeypatch.setattr(auth, "monotonic", lambda: 1000.0)

    for _ in range(50):
        assert await auth._find_key(_JWKS_URL, "bogus-kid") is None

    assert calls.count(True) == 1, "a forced refetch fired more than once in the cooldown"


async def test_a_known_kid_never_triggers_a_refetch(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[bool] = []

    async def fake_get_jwks(jwks_url: str, *, force: bool = False) -> dict:
        calls.append(force)
        return {"keys": [{"kid": "real-key"}]}

    monkeypatch.setattr(auth, "_get_jwks", fake_get_jwks)

    assert await auth._find_key(_JWKS_URL, "real-key") == {"kid": "real-key"}
    assert calls == [False]


async def test_a_rotated_key_is_still_picked_up(monkeypatch: pytest.MonkeyPatch) -> None:
    """The behaviour the refetch exists for has to survive the throttle."""

    async def fake_get_jwks(jwks_url: str, *, force: bool = False) -> dict:
        if force:
            return {"keys": [{"kid": "rotated-in"}]}
        return {"keys": [{"kid": "stale"}]}

    monkeypatch.setattr(auth, "_get_jwks", fake_get_jwks)
    monkeypatch.setattr(auth, "monotonic", lambda: 1000.0)

    assert await auth._find_key(_JWKS_URL, "rotated-in") == {"kid": "rotated-in"}
