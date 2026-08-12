"""The email transport, which is the only part of this feature that can reach a
stranger's inbox.

So the properties worth pinning are mostly about what it refuses to do: send
without being configured, degrade quietly to a no-op that a deploy would mistake
for delivery, or let an API key reach a log line.

Nothing here opens a socket. The Resend cases drive a real `ResendTransport`
through an `httpx.MockTransport`, so the request construction is under test but
no mail is sent.
"""
from __future__ import annotations

import io

import httpx
import pytest

from job_os.integrations.email import (
    ConsoleTransport,
    EmailMessage,
    EmailNotConfiguredError,
    EmailTransport,
    EmailTransportError,
    ResendTransport,
    build_transport,
    redact_email,
)
from job_os.settings import Settings

DB_URL = "postgresql+asyncpg://job_os:job_os@localhost/job_os"
API_KEY = "re_test_ThisIsTheSecretKeyValue"


def message(**overrides: object) -> EmailMessage:
    base: dict[str, object] = {
        "to": "person@example.com",
        "subject": "3 new roles for Backend roles in Boston",
        "text": "A plain text digest.",
        "html": "<html><body>A digest.</body></html>",
    }
    base.update(overrides)
    return EmailMessage(**base)  # type: ignore[arg-type]


# ---- the message type -------------------------------------------------------


@pytest.mark.parametrize("field", ["to", "subject", "text", "html"])
def test_a_message_missing_any_required_part_is_rejected_at_construction(
    field: str,
) -> None:
    """HTML-only mail is filtered harder and unreadable in a text client, so the
    type does not let a caller skip either part.
    """
    with pytest.raises(ValueError):
        message(**{field: "   "})


def test_a_console_transport_satisfies_the_transport_protocol() -> None:
    assert isinstance(ConsoleTransport(sender="a@example.com"), EmailTransport)


# ---- redaction --------------------------------------------------------------


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("person@example.com", "p***@example.com"),
        ("a@example.com", "a***@example.com"),
        ("@example.com", "?***@example.com"),
        ("not-an-address", "[invalid-address]"),
        ("", "[invalid-address]"),
    ],
)
def test_addresses_are_logged_in_a_form_that_cannot_be_harvested(
    address: str, expected: str
) -> None:
    assert redact_email(address) == expected


# ---- console transport ------------------------------------------------------


async def test_the_console_transport_sends_nothing_and_keeps_what_it_rendered() -> None:
    """The default transport, and the one a misconfigured deploy falls back to."""
    stream = io.StringIO()
    transport = ConsoleTransport(sender="job.os <alerts@example.com>", stream=stream)

    result = await transport.send(message())

    assert result.accepted is True
    assert result.provider == "console"
    assert len(transport.sent) == 1
    rendered = stream.getvalue()
    assert "To: person@example.com" in rendered
    assert "A plain text digest." in rendered
    # The HTML part is summarised rather than dumped, so a dry run stays readable.
    assert "[html part:" in rendered
    assert "<html>" not in rendered


async def test_the_console_transport_can_record_without_printing() -> None:
    stream = io.StringIO()
    transport = ConsoleTransport(sender="a@example.com", stream=stream, quiet=True)

    await transport.send(message())

    assert stream.getvalue() == ""
    assert len(transport.sent) == 1


async def test_the_console_transport_renders_the_unsubscribe_headers() -> None:
    """A dry run has to show that the one-click headers would go out."""
    stream = io.StringIO()
    transport = ConsoleTransport(sender="a@example.com", stream=stream)

    await transport.send(
        message(
            headers={
                "List-Unsubscribe": "<https://api.example.com/u?token=x>",
                "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
            }
        )
    )

    rendered = stream.getvalue()
    assert "List-Unsubscribe: <https://api.example.com/u?token=x>" in rendered
    assert "List-Unsubscribe-Post: List-Unsubscribe=One-Click" in rendered


# ---- resend transport -------------------------------------------------------


def resend_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_the_resend_request_carries_both_parts_and_the_extra_headers() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("Authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "abc-123"})

    transport = ResendTransport(
        api_key=API_KEY,
        sender="job.os <alerts@example.com>",
        client=resend_client(handler),
    )

    result = await transport.send(
        message(headers={"List-Unsubscribe": "<https://x.example/u>"}, reply_to="r@example.com")
    )

    assert result.accepted is True
    assert result.message_id == "abc-123"
    assert captured["url"] == "https://api.resend.com/emails"
    assert captured["auth"] == f"Bearer {API_KEY}"
    body = captured["body"]
    assert body["to"] == ["person@example.com"]
    assert body["text"] and body["html"]
    assert body["headers"]["List-Unsubscribe"] == "<https://x.example/u>"
    assert body["reply_to"] == "r@example.com"


async def test_a_rejected_send_raises_and_never_repeats_the_api_key() -> None:
    """A provider that echoes the request back on failure is a real pattern.

    The key must not ride out in an exception message that ends up in a log or a
    run report.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401, text=f'{{"message":"invalid key {API_KEY}","name":"validation_error"}}'
        )

    transport = ResendTransport(
        api_key=API_KEY, sender="a@example.com", client=resend_client(handler)
    )

    with pytest.raises(EmailTransportError) as excinfo:
        await transport.send(message())

    assert API_KEY not in str(excinfo.value)
    assert "[redacted]" in str(excinfo.value)
    assert "401" in str(excinfo.value)


async def test_a_transport_level_failure_is_reported_as_a_transport_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    transport = ResendTransport(
        api_key=API_KEY, sender="a@example.com", client=resend_client(handler)
    )

    with pytest.raises(EmailTransportError) as excinfo:
        await transport.send(message())

    assert API_KEY not in str(excinfo.value)


async def test_a_success_with_an_unreadable_body_is_still_a_success() -> None:
    """Accepted is accepted. A missing id costs traceability, not delivery."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    transport = ResendTransport(
        api_key=API_KEY, sender="a@example.com", client=resend_client(handler)
    )

    result = await transport.send(message())

    assert result.accepted is True
    assert result.message_id is None


def test_a_resend_transport_cannot_be_built_without_a_key_or_a_sender() -> None:
    with pytest.raises(EmailNotConfiguredError):
        ResendTransport(api_key="", sender="a@example.com")
    with pytest.raises(EmailNotConfiguredError):
        ResendTransport(api_key=API_KEY, sender="")


# ---- transport selection ----------------------------------------------------


def test_the_default_deploy_gets_the_transport_that_sends_nothing() -> None:
    transport = build_transport(Settings(database_url=DB_URL))

    assert isinstance(transport, ConsoleTransport)
    assert transport.name == "console"


def test_selecting_resend_without_a_key_fails_rather_than_degrading_to_console() -> None:
    """Degrading silently would mean a deploy that believes it is mailing users
    is writing to a log nobody reads.
    """
    with pytest.raises(EmailNotConfiguredError):
        build_transport(
            Settings(
                database_url=DB_URL,
                email_provider="resend",
                email_from="a@example.com",
                resend_api_key=None,
            )
        )


def test_selecting_resend_without_a_from_address_fails() -> None:
    with pytest.raises(EmailNotConfiguredError):
        build_transport(
            Settings(
                database_url=DB_URL,
                email_provider="resend",
                resend_api_key=API_KEY,
                email_from="   ",
            )
        )


def test_a_fully_configured_resend_deploy_builds_a_resend_transport() -> None:
    transport = build_transport(
        Settings(
            database_url=DB_URL,
            email_provider="resend",
            resend_api_key=API_KEY,
            email_from="job.os <alerts@example.com>",
        )
    )

    assert isinstance(transport, ResendTransport)
    assert transport.name == "resend"


def test_an_unknown_provider_is_rejected_at_startup_not_at_send_time() -> None:
    with pytest.raises(ValueError, match="EMAIL_PROVIDER"):
        Settings(database_url=DB_URL, email_provider="mailgun")


def test_the_provider_name_is_normalised() -> None:
    assert Settings(database_url=DB_URL, email_provider="  RESEND  ").email_provider == "resend"


def test_alerts_are_off_by_default() -> None:
    """Nothing about a fresh deploy should be able to mail anyone."""
    settings = Settings(database_url=DB_URL)

    assert settings.alerts_enabled is False
    assert settings.email_provider == "console"
    assert settings.alert_unsubscribe_secret is None
