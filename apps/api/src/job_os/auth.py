"""Clerk JWT verification with a dev-mode fallback.

Behavior:
- If `CLERK_SECRET_KEY` is set, expect `Authorization: Bearer <jwt>` headers and
  verify via Clerk JWKs.
- If not set (local dev), resolve to a single user `dev@local` (created on demand).
  This lets the M1 scaffold run before Clerk is provisioned.

When a JWT's `kid` isn't in the cached JWKS, we transparently refetch once
before failing — Clerk rotates signing keys without notice during the dev-mode
quickstart and a stale process-local cache otherwise locks you out until restart.
"""
from __future__ import annotations

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


async def _get_jwks(jwks_url: str, *, force: bool = False) -> dict:
    if force or jwks_url not in _JWKS_CACHE:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(jwks_url)
            resp.raise_for_status()
            _JWKS_CACHE[jwks_url] = resp.json()
    return _JWKS_CACHE[jwks_url]


async def _find_key(jwks_url: str, kid: str) -> dict | None:
    jwks = await _get_jwks(jwks_url)
    key = next((k for k in jwks["keys"] if k["kid"] == kid), None)
    if key is None:
        # Maybe Clerk rotated the key — refetch once.
        jwks = await _get_jwks(jwks_url, force=True)
        key = next((k for k in jwks["keys"] if k["kid"] == kid), None)
    return key


async def _verify_clerk_jwt(token: str, jwks_url: str) -> dict:
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
        raise HTTPException(
            status_code=401,
            detail=f"unknown signing key — kid={kid}, available={available}",
        )

    public_key = jwt.algorithms.RSAAlgorithm.from_jwk(key)
    try:
        # Clerk's session tokens default to a 60-second lifetime; on a
        # cold-started Render container a few seconds of clock drift is
        # enough to make a fresh-from-the-browser token read as already
        # expired. A 60-second leeway swallows that drift while still
        # rejecting anything actually stale by more than a minute.
        return jwt.decode(
            token,
            public_key,
            algorithms=[key.get("alg", "RS256")],
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
    claims = await _verify_clerk_jwt(token, settings.clerk_jwks_url)

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
