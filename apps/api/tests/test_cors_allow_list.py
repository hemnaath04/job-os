"""The credentialed CORS allow-list must not trust localhost in production.

The defect: `allow_origins` was `["http://localhost:3000", *WEB_ORIGINS]` with
`allow_credentials=True`, unconditionally. A browser enforces CORS on the origin
*string*, not on who is actually listening on that port, so on a deployed API any
page loaded from `http://localhost:3000` on a user's machine -- the dev server of
an untrusted repo, or any local process that binds 3000 -- could call production
with the user's credentials attached and read the responses.

The fix trusts that origin in development only. Production names its real origins
through WEB_ORIGINS, which docs/DEPLOY.md already lists as a set Heroku config var.
"""
from __future__ import annotations

import pytest

from job_os.main import cors_origins
from job_os.settings import Settings

_RELEVANT_ENV = ("APP_ENV", "WEB_ORIGINS")


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Same reason as test_auth_fails_closed: `_env_file=None` suppresses the .env
    # file but not the process environment, and CI sets some of these.
    for name in _RELEVANT_ENV:
        monkeypatch.delenv(name, raising=False)


def settings_for(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "database_url": "postgresql+asyncpg://u:p@localhost/db",
        "_env_file": None,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_production_does_not_trust_localhost() -> None:
    monkey = pytest.MonkeyPatch()
    monkey.setenv("WEB_ORIGINS", "https://app.example.com")
    try:
        assert cors_origins(settings_for(app_env="production")) == ["https://app.example.com"]
    finally:
        monkey.undo()


def test_production_with_no_web_origins_allows_nothing() -> None:
    """Fail closed: an unset WEB_ORIGINS grants no origin rather than granting localhost."""
    assert cors_origins(settings_for(app_env="production")) == []


def test_development_trusts_localhost() -> None:
    assert cors_origins(settings_for(app_env="development")) == ["http://localhost:3000"]


def test_development_keeps_extra_origins_too() -> None:
    monkey = pytest.MonkeyPatch()
    monkey.setenv("WEB_ORIGINS", "https://preview.example.com")
    try:
        assert cors_origins(settings_for(app_env="development")) == [
            "http://localhost:3000",
            "https://preview.example.com",
        ]
    finally:
        monkey.undo()


def test_default_env_is_production_posture() -> None:
    """`app_env` defaults to production, so the default must also exclude localhost."""
    assert cors_origins(settings_for()) == []
