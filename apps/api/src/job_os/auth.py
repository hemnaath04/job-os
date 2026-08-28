"""Clerk JWT verification with a dev-mode fallback.

Behavior:
- If `CLERK_SECRET_KEY` is set, expect `Authorization: Bearer <jwt>` headers and
  verify via Clerk JWKs.
- If not set (local dev), resolve to a single user `dev@local` (created on demand).
  This lets the M1 scaffold run before Clerk is provisioned.

When a JWT's `kid` isn't in the cached JWKS, we transparently refetch before
failing — Clerk rotates signing keys without notice during the dev-mode
quickstart and a stale process-local cache otherwise locks you out until restart.
That refetch is rate-limited per JWKS url (see `_JWKS_REFETCH_COOLDOWN_SECONDS`):
`kid` is caller-written and read before any signature check, so an unthrottled
refetch is an unauthenticated outbound-request amplifier.
"""
from __future__ import annotations

from time import monotonic

import httpx
import jwt
import structlog
from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from job_os.db.models import User
from job_os.db.session import get_session
from job_os.settings import get_settings

log = structlog.get_logger(__name__)

_JWKS_CACHE: dict[str, dict] = {}

#: When each JWKS url was last refetched because of an unrecognised `kid`.
_JWKS_REFETCHED_AT: dict[str, float] = {}

#: Least time between two unknown-`kid` refetches of the same JWKS url.
#:
#: The refetch below is reachable by anyone who can reach the API, before any
#: signature is checked, and `kid` is a field the caller writes. Without a floor
#: on how often it can fire, a stream of tokens carrying random `kid`s turns into
#: one outbound request to Clerk per inbound request, each holding a connection
#: for up to the 5s timeout -- an unauthenticated request amplifier that ends in
#: Clerk rate-limiting the app, which fails auth for real users. One refetch per
#: minute keeps the rotation recovery this exists for (Clerk's session tokens
#: live 60s, so a genuinely rotated key costs at most a minute of 401s instead of
#: the "locked out until restart" this was written to prevent) while making the
#: outbound rate independent of the inbound one.
_JWKS_REFETCH_COOLDOWN_SECONDS = 60.0


async def _get_jwks(jwks_url: str, *, force: bool = False) -> dict:
    if force or jwks_url not in _JWKS_CACHE:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(jwks_url)
            resp.raise_for_status()
            _JWKS_CACHE[jwks_url] = resp.json()
    return _JWKS_CACHE[jwks_url]


def _refetch_allowed(jwks_url: str, now: float) -> bool:
    """Whether an unknown-`kid` refetch may fire, recording it when it may.

    Check and record are one step, and both happen before the caller awaits the
    fetch, so concurrent requests carrying the same unknown `kid` cannot each read
    a pre-refetch timestamp and all decide to go.
    """
    last = _JWKS_REFETCHED_AT.get(jwks_url)
    if last is not None and now - last < _JWKS_REFETCH_COOLDOWN_SECONDS:
        return False
    _JWKS_REFETCHED_AT[jwks_url] = now
    return True


async def _find_key(jwks_url: str, kid: str) -> dict | None:
    jwks = await _get_jwks(jwks_url)
    key = next((k for k in jwks["keys"] if k["kid"] == kid), None)
    if key is None:
        # Maybe Clerk rotated the key — refetch, at most once per cooldown.
        if not _refetch_allowed(jwks_url, monotonic()):
            log.warning("jwt.jwks_refetch_throttled", got_kid=kid)
            return None
        jwks = await _get_jwks(jwks_url, force=True)
        key = next((k for k in jwks["keys"] if k["kid"] == kid), None)
    return key


async def _verify_clerk_jwt(token: str, jwks_url: str, issuer: str) -> dict:
    try:
        unverified_header = jwt.get_unverified_header(token)
        unverified_payload = jwt.decode(token, options={"verify_signature": False})
    except jwt.PyJWTError as e:
        log.warning("jwt.invalid_header", error=str(e))
        raise HTTPException(status_code=401, detail=f"invalid jwt header: {e}") from e

    kid = unverified_header.get("kid")
    key = await _find_key(jwks_url, kid) if kid else None
    if key is None:
        available = [k.get("kid") for k in _JWKS_CACHE.get(jwks_url, {}).get("keys", [])]
        log.warning(
            "jwt.unknown_kid",
            got_kid=kid,
            available=available,
            iss=unverified_payload.get("iss"),
            azp=unverified_payload.get("azp"),
        )
        # The available kids stay in the log line above and out of the response.
        # This branch answers an unauthenticated caller, and echoing the key set
        # back told anyone probing which JWKS the deployment is on and when it
        # last rotated. The log has what an operator debugging this needs.
        raise HTTPException(status_code=401, detail="unknown signing key")

    public_key = jwt.algorithms.RSAAlgorithm.from_jwk(key)
    try:
        # Clerk's session tokens default to a 60-second lifetime; on a
        # cold-started backend container a few seconds of clock drift is
        # enough to make a fresh-from-the-browser token read as already
        # expired. A 60-second leeway swallows that drift while still
        # rejecting anything actually stale by more than a minute.
        # `issuer` is checked by PyJWT itself (verify_iss is on by default): a
        # mismatch raises InvalidIssuerError and a token with no `iss` at all
        # raises MissingRequiredClaimError. Both subclass PyJWTError, so they
        # land in the handler below and answer the same generic 401 as any other
        # verification failure -- and neither message contains the expected
        # issuer, so nothing about the deployment's configuration is echoed back.
        #
        # This matters even though the JWKS url already pins the Clerk instance:
        # `verify_aud` is off, so without this the only thing tying a token to
        # THIS deployment was that Clerk had signed it at all.
        return jwt.decode(
            token,
            public_key,
            algorithms=[key.get("alg", "RS256")],
            issuer=issuer,
            options={"verify_aud": False},
            leeway=60,
        )
    except jwt.PyJWTError as e:
        log.warning("jwt.verify_failed", error=str(e))
        raise HTTPException(status_code=401, detail=f"jwt verify failed: {e}") from e


async def _get_or_create_user(
    session: AsyncSession, *, clerk_id: str, email: str, display_name: str | None = None
) -> User:
    result = await session.execute(select(User).where(User.clerk_id == clerk_id))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(clerk_id=clerk_id, email=email, display_name=display_name)
        session.add(user)
        await session.flush()
    return user


async def get_current_user(
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> User:
    settings = get_settings()

    if not settings.clerk_secret_key or not settings.clerk_jwks_url:
        # Fail closed. Unconfigured authentication is an unknown, and an unknown is
        # not a pass. Minting the shared `dev-local` user needs BOTH
        # APP_ENV=development and ALLOW_ANONYMOUS_DEV_USER=true; anything less is a
        # 503, including the previous default of neither being set.
        if not settings.dev_user_enabled:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Authentication is not configured for this deployment.",
            )
        return await _get_or_create_user(
            session, clerk_id="dev-local", email="dev@local", display_name="Dev User"
        )

    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="missing bearer token"
        )

    token = authorization.split(" ", 1)[1]
    claims = await _verify_clerk_jwt(token, settings.clerk_jwks_url, settings.clerk_issuer)

    clerk_id = claims.get("sub")
    email = (
        claims.get("email")
        or claims.get("primary_email_address")
        or claims.get("email_address")
    )
    if not clerk_id:
        raise HTTPException(status_code=401, detail="jwt missing sub")
    # Clerk session tokens omit email — fall back to a synthetic local address
    # so the User row can be created on first sight; resolve the real one later.
    if not email:
        email = f"{clerk_id}@clerk.local"

    return await _get_or_create_user(
        session, clerk_id=clerk_id, email=email, display_name=claims.get("name")
    )
