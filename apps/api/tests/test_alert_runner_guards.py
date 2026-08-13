"""The conditions under which a real email is allowed to leave the building.

This feature is meant to ship switched off. `require_send_config` is the gate,
and it is checked on the sending path only, so a dry run still works on a laptop
with none of it configured.

Every one of these settings is a separate no, on purpose. A single ALERTS_ENABLED
flag would mean one environment variable stood between an unconfigured deploy and
mail going out over an unsigned unsubscribe link.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from job_os.services.alert_runner import (
    AlertsNotConfiguredError,
    RunReport,
    SubscriptionOutcome,
    require_send_config,
)
from job_os.settings import Settings

DB_URL = "postgresql+asyncpg://job_os:job_os@localhost/job_os"

SENDABLE = {
    "database_url": DB_URL,
    "alerts_enabled": True,
    "email_provider": "resend",
    "resend_api_key": "re_test_key",
    "email_from": "job.os <alerts@example.com>",
    "alert_unsubscribe_secret": "a-real-secret",
    "alert_postal_address": "job.os, 1 Example Street, Boston MA 02115, USA",
    "alert_link_base_url": "https://api.example.com",
}


def settings(**overrides: object) -> Settings:
    merged = {**SENDABLE, **overrides}
    return Settings(**merged)  # type: ignore[arg-type]


def test_a_fully_configured_deploy_is_allowed_to_send() -> None:
    require_send_config(settings())


def test_sending_is_refused_while_the_master_switch_is_off() -> None:
    with pytest.raises(AlertsNotConfiguredError, match="ALERTS_ENABLED"):
        require_send_config(settings(alerts_enabled=False))


def test_sending_is_refused_without_a_signing_secret() -> None:
    """An unsubscribe link everyone can forge is worse than no digest at all."""
    with pytest.raises(AlertsNotConfiguredError, match="ALERT_UNSUBSCRIBE_SECRET"):
        require_send_config(settings(alert_unsubscribe_secret=None))


def test_sending_is_refused_without_a_postal_address() -> None:
    """15 U.S.C. 7704(a)(5): a valid physical postal address in every commercial
    message. Not optional, so it cannot be a runtime warning.
    """
    with pytest.raises(AlertsNotConfiguredError, match="CAN-SPAM"):
        require_send_config(settings(alert_postal_address=None))


def test_sending_is_refused_without_a_public_base_url_for_the_link() -> None:
    with pytest.raises(AlertsNotConfiguredError, match="ALERT_LINK_BASE_URL"):
        require_send_config(settings(alert_link_base_url=None))


def test_the_console_provider_is_refused_on_the_sending_path() -> None:
    """It would accept every message and deliver none, which reads as success."""
    with pytest.raises(AlertsNotConfiguredError, match="console"):
        require_send_config(settings(email_provider="console"))


def test_the_default_settings_object_fails_every_gate() -> None:
    with pytest.raises(AlertsNotConfiguredError):
        require_send_config(Settings(database_url=DB_URL))


# ---- run report -------------------------------------------------------------


def report(*outcomes: SubscriptionOutcome, dry_run: bool = True) -> RunReport:
    return RunReport(
        started_at=datetime(2026, 8, 12, 12, 0, tzinfo=UTC),
        dry_run=dry_run,
        outcomes=list(outcomes),
    )


def outcome(kind: str, **overrides: object) -> SubscriptionOutcome:
    from uuid import uuid4

    base: dict[str, object] = {
        "subscription_id": uuid4(),
        "search_name": "Backend roles",
        "outcome": kind,
    }
    base.update(overrides)
    return SubscriptionOutcome(**base)  # type: ignore[arg-type]


def test_a_dry_run_reports_what_it_would_have_done_not_what_it_did() -> None:
    result = report(outcome("would_send"), outcome("would_send"), outcome("skipped_empty"))

    assert result.would_send == 2
    assert result.sent == 0
    assert "would send 2" in result.summary_line()
    assert "3 subscription(s) checked" in result.summary_line()


def test_a_live_run_counts_sends_and_failures_separately() -> None:
    result = report(
        outcome("sent"), outcome("failed"), outcome("skipped_not_due"), dry_run=False
    )

    assert result.sent == 1
    assert result.failed == 1
    assert "sent 1" in result.summary_line()
    assert "1 failed" in result.summary_line()
