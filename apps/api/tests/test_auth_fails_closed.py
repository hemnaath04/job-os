"""C1: authentication must fail closed when it is not configured.

findings.md S1 "Auth is off by default, and the only Heroku declaration in the repo
sets nothing".

The defect: `app_env` defaulted to "development", and the 503 fail-safe in
`get_current_user` was guarded by `if not settings.is_dev`, so it could only fire
on a deployment that had explicitly set APP_ENV to something else. Omit both Clerk
variables and APP_ENV and the API served every route to anyone as one shared
`dev-local` account -- and an anonymous request *wrote* a users row.

Production was not affected (Heroku has APP_ENV=production and both Clerk vars),
so this was latent. But the default was the unsafe value, `heroku.yml` declares no
env at all, and `.env.example` shipped APP_ENV=development, so a fresh deploy or a
mistyped config var opened it.

The fix inverts the default and requires two deliberate opt-ins for the dev user.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from job_os.settings import Settings


def settings_for(**overrides: object) -> Settings:
    """A Settings built from explicit values only, ignoring the developer's .env."""
    base: dict[str, object] = {
        "database_url": "postgresql+asyncpg://u:p@localhost/db",
        "_env_file": None,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


# --- the default -----------------------------------------------------------

def test_app_env_defaults_to_production() -> None:
    """An unset APP_ENV must not select the mode that bypasses authentication."""
    assert settings_for().app_env == "production"


def test_is_dev_false_by_default() -> None:
    assert settings_for().is_dev is False


def test_dev_user_requires_both_the_env_and_the_explicit_opt_in() -> None:
    """APP_ENV=development alone is not enough. Two deliberate signals, because one
    of them is the kind of thing that gets set by accident in a dashboard.

    Note the two capabilities are kept on separate properties rather than folded
    into is_dev. `is_dev` stays "which environment is this", and the anonymous dev
    user hangs off `dev_user_enabled`. The other is_dev consumer
    (routers/profile.py:157, which gates reading an arbitrary path off the server's
    filesystem) is a different capability and should not be switched on by a flag
    named after the dev user. Both are closed by default now either way, because
    the default env flipped.
    """
    assert settings_for(app_env="development").dev_user_enabled is False
    assert settings_for(allow_anonymous_dev_user=True).dev_user_enabled is False
    assert (
        settings_for(app_env="development", allow_anonymous_dev_user=True).dev_user_enabled
        is True
    )


@pytest.mark.parametrize("value", ["production", "prod", "staging", "test", "", "Development"])
def test_only_exactly_development_can_enable_the_dev_user(value: str) -> None:
    """Case-sensitive and exact, so "Development" or "dev" cannot open the door."""
    assert settings_for(app_env=value, allow_anonymous_dev_user=True).dev_user_enabled is False


def test_server_path_import_is_also_closed_by_default() -> None:
    """routers/profile.py:157 lets a caller import a resume from an arbitrary path on
    the server's filesystem, gated only on is_dev. Under the old default that was
    reachable out of the box. Same fail-open default, same fix."""
    assert settings_for().is_dev is False
    assert settings_for(app_env="production").is_dev is False
    assert settings_for(app_env="development").is_dev is True


# --- the guard -------------------------------------------------------------

async def _call_get_current_user(settings: Settings, authorization: str | None = None):
    """Invoke the dependency with a settings object injected, no DB needed for the
    paths under test (both raise before touching the session)."""
    from job_os import auth

    original = auth.get_settings
    auth.get_settings = lambda: settings  # type: ignore[assignment]
    try:
        return await auth.get_current_user(authorization=authorization, session=None)  # type: ignore[arg-type]
    finally:
        auth.get_settings = original  # type: ignore[assignment]


async def test_missing_clerk_config_raises_503_by_default() -> None:
    """The fail-safe the author wrote must actually be reachable."""
    with pytest.raises(HTTPException) as exc:
        await _call_get_current_user(settings_for())
    assert exc.value.status_code == 503
    assert "not configured" in str(exc.value.detail).lower()


async def test_missing_clerk_config_still_503_with_app_env_development_alone() -> None:
    with pytest.raises(HTTPException) as exc:
        await _call_get_current_user(settings_for(app_env="development"))
    assert exc.value.status_code == 503


async def test_missing_clerk_config_still_503_with_the_opt_in_alone() -> None:
    with pytest.raises(HTTPException) as exc:
        await _call_get_current_user(settings_for(allow_anonymous_dev_user=True))
    assert exc.value.status_code == 503


async def test_configured_auth_rejects_a_request_with_no_bearer_token() -> None:
    """With Clerk configured, an anonymous caller gets 401 -- not a dev user."""
    configured = settings_for(
        clerk_secret_key="sk_test_dummy",
        clerk_jwks_url="https://example.invalid/.well-known/jwks.json",
    )
    with pytest.raises(HTTPException) as exc:
        await _call_get_current_user(configured, authorization=None)
    assert exc.value.status_code == 401


async def test_configured_auth_rejects_a_non_bearer_authorization_header() -> None:
    configured = settings_for(
        clerk_secret_key="sk_test_dummy",
        clerk_jwks_url="https://example.invalid/.well-known/jwks.json",
    )
    with pytest.raises(HTTPException) as exc:
        await _call_get_current_user(configured, authorization="Basic abc123")
    assert exc.value.status_code == 401


# --- the interactive docs surface -----------------------------------------

def test_docs_are_disabled_unless_dev() -> None:
    """findings.md S3: /docs, /redoc and /openapi.json answered 200 unauthenticated on
    the production API, handing anyone probing a complete map of the surface."""
    from job_os.main import docs_urls

    prod = docs_urls(settings_for())
    assert prod == {"docs_url": None, "redoc_url": None, "openapi_url": None}

    dev = docs_urls(settings_for(app_env="development", allow_anonymous_dev_user=True))
    assert dev["docs_url"] == "/docs"
    assert dev["openapi_url"] == "/openapi.json"
