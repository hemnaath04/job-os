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

import os
from pathlib import Path

import pytest
from fastapi import HTTPException

from job_os.settings import Settings

#: Every setting this module reasons about. Cleared before constructing a Settings so
#: an ambient value cannot decide the outcome -- `_env_file=None` suppresses the .env
#: file but NOT the process environment, which is how the first version of this suite
#: passed locally and failed in CI (the api job sets APP_ENV, so the "what is the
#: default" test was really measuring "what is in the environment").
_RELEVANT_ENV = (
    "APP_ENV",
    "ALLOW_ANONYMOUS_DEV_USER",
    "CLERK_SECRET_KEY",
    "CLERK_JWKS_URL",
)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _RELEVANT_ENV:
        monkeypatch.delenv(name, raising=False)


def settings_for(**overrides: object) -> Settings:
    """A Settings built from explicit values only: no .env file, and the relevant
    process environment cleared by the autouse fixture above."""
    base: dict[str, object] = {
        "database_url": "postgresql+asyncpg://u:p@localhost/db",
        "_env_file": None,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


# --- the default -----------------------------------------------------------

def test_the_declared_default_is_production() -> None:
    """Asserted on the field declaration, so no environment can affect it."""
    assert Settings.model_fields["app_env"].default == "production"
    assert Settings.model_fields["allow_anonymous_dev_user"].default is False


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
    assert dev["redoc_url"] == "/redoc"
    assert dev["openapi_url"] == "/openapi.json"


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({"APP_ENV": "production"}, "None None None"),
        ({"APP_ENV": "development"}, "/docs /redoc /openapi.json"),
    ],
)
def test_the_app_really_does_not_mount_docs_in_production(
    env: dict[str, str], expected: str, tmp_path: Path
) -> None:
    """Asserts on the real `app` object under a controlled environment.

    In a subprocess, deliberately, and this took three attempts to get right --
    worth recording because each failure was a test that passed while the app was
    broken:

      1. comparing `app.*` against a fresh `docs_urls(get_settings())` failed,
         because `get_settings()` is lru_cached and the recomputed settings could
         differ from the ones used at import.
      2. comparing against the module-level `_DOCS` also failed, because the autouse
         env-isolation fixture deletes APP_ENV before the lazy import, so the module
         loaded with .env's APP_ENV=development -- whose URLs happen to equal
         FastAPI's own defaults, so a dropped argument was invisible.

    `app` is constructed at import time, so the only honest way to test what a given
    environment produces is to import it in that environment.
    """
    assert _run_app_import(tmp_path, env) == expected


def _isolated_package(tmp_path: Path) -> Path:
    """Copy the job_os package somewhere with no repo markers, and return the dir to
    put on PYTHONPATH.

    This is what makes the "APP_ENV absent" case testable. `settings._find_repo_root`
    walks up from settings.py's own `__file__` looking for pnpm-workspace.yaml, then
    for pyproject.toml + alembic.ini -- it does not consult cwd, so running the
    subprocess elsewhere does not stop it finding the developer's .env. Under a
    marker-less tmp dir the search falls through to the filesystem root, `env_file`
    becomes ("/.env", "/.env.local") which do not exist, and the settings are built
    from the process environment and the declared defaults alone.

    Deterministic in CI and locally, which is the point: the alternative was a
    skipif that only ran where there is no .env, and the absent case is precisely
    the guarantee worth testing -- a deploy target that FORGETS to set APP_ENV must
    get production behaviour. Heroku happens to set it. The next target might not.
    """
    import shutil

    source = Path(__file__).resolve().parents[1] / "src" / "job_os"
    dest = tmp_path / "pkg" / "job_os"
    shutil.copytree(
        source, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
    )
    for marker in ("pnpm-workspace.yaml", "pyproject.toml", "alembic.ini"):
        assert not (tmp_path / "pkg" / marker).exists(), f"{marker} would defeat this"
    return tmp_path / "pkg"


def _run_app_import(tmp_path: Path, env: dict[str, str]) -> str:
    import subprocess
    import sys

    script = (
        "from job_os.main import app; "
        "print(app.docs_url, app.redoc_url, app.openapi_url)"
    )
    # S603: the command is sys.executable and a literal script defined above. No
    # caller-supplied input reaches it.
    proc = subprocess.run(  # noqa: S603
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env={
            "PATH": os.environ["PATH"],
            "PYTHONPATH": str(_isolated_package(tmp_path)),
            "DATABASE_URL": "postgresql+asyncpg://u:p@localhost/db",
            **env,
        },
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip().splitlines()[-1]


def test_a_deploy_target_that_forgets_app_env_gets_production(tmp_path: Path) -> None:
    """THE case that matters: no APP_ENV set anywhere, no .env reachable.

    This is the entire point of inverting the default. If a deploy target omits
    APP_ENV it must behave as production -- docs unmounted, and (per the unit tests
    above) the dev user refused. Heroku sets APP_ENV today; nothing guarantees the
    next target will.
    """
    assert _run_app_import(tmp_path, {}) == "None None None"
