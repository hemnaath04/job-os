"""A Clerk-signed token from somewhere else must not authenticate here.

`verify_aud` is off, so before this the only thing tying a session token to this
deployment was that Clerk had signed it at all. The JWKS url pins the instance,
which covers a token minted by a *different* Clerk instance -- but nothing checked
the claim the token itself makes about where it came from, and `iss` is the field
that says so. Finding #4 of the audit; the issuer is now required to match
`settings.clerk_issuer`.

`azp` is deliberately left unvalidated: the MCP OAuth clients present varied `azp`
values, so pinning one client id would lock them out.
"""
from __future__ import annotations

import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

from job_os import auth

ISSUER = "https://clerk.jobs.hemnaath.tech"
JWKS_URL = "https://clerk.jobs.hemnaath.tech/.well-known/jwks.json"
KID = "test-kid"


@pytest.fixture
def signing_key(monkeypatch: pytest.MonkeyPatch) -> rsa.RSAPrivateKey:
    """A real RSA key, with its public half served as the JWKS this code fetches.

    Signed for real rather than stubbed: the point is that a token which passes
    every *signature* check is still refused on the issuer alone.
    """
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    public_jwk["kid"] = KID
    public_jwk["alg"] = "RS256"

    async def fake_get_jwks(jwks_url: str, *, force: bool = False) -> dict:
        return {"keys": [public_jwk]}

    monkeypatch.setattr(auth, "_get_jwks", fake_get_jwks)
    return key


def mint(key: rsa.RSAPrivateKey, **claims: object) -> str:
    payload: dict[str, object] = {
        "sub": "user_abc",
        "exp": int(time.time()) + 300,
        **claims,
    }
    return jwt.encode(payload, key, algorithm="RS256", headers={"kid": KID})


async def test_the_configured_issuer_is_accepted(signing_key: rsa.RSAPrivateKey) -> None:
    claims = await auth._verify_clerk_jwt(mint(signing_key, iss=ISSUER), JWKS_URL, ISSUER)
    assert claims["sub"] == "user_abc"


async def test_a_token_from_another_issuer_is_refused(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    """Correctly signed by the key this deployment trusts, wrong `iss`. Still 401."""
    token = mint(signing_key, iss="https://clerk.attacker.example")
    with pytest.raises(HTTPException) as caught:
        await auth._verify_clerk_jwt(token, JWKS_URL, ISSUER)
    assert caught.value.status_code == 401


async def test_a_token_with_no_issuer_at_all_is_refused(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    with pytest.raises(HTTPException) as caught:
        await auth._verify_clerk_jwt(mint(signing_key), JWKS_URL, ISSUER)
    assert caught.value.status_code == 401


async def test_the_refusal_does_not_echo_the_expected_issuer(
    signing_key: rsa.RSAPrivateKey,
) -> None:
    """A 401 must not tell an unauthenticated caller what to put in `iss`."""
    token = mint(signing_key, iss="https://clerk.attacker.example")
    with pytest.raises(HTTPException) as caught:
        await auth._verify_clerk_jwt(token, JWKS_URL, ISSUER)
    assert ISSUER not in str(caught.value.detail)


async def test_azp_is_not_validated(signing_key: rsa.RSAPrivateKey) -> None:
    """Explicitly pinned: the MCP clients vary `azp`, so it must stay unchecked."""
    token = mint(signing_key, iss=ISSUER, azp="some-other-client-id")
    claims = await auth._verify_clerk_jwt(token, JWKS_URL, ISSUER)
    assert claims["azp"] == "some-other-client-id"


def test_the_default_issuer_is_the_deployment_one() -> None:
    from job_os.settings import Settings

    settings = Settings(  # type: ignore[call-arg]
        database_url="postgresql+asyncpg://u:p@localhost/db",
        _env_file=None,
    )
    assert settings.clerk_issuer == ISSUER
