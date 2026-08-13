"""Outbound email, behind a transport the rest of the app cannot see through.

Before this module the repo had no email capability at all, so the shape of the
interface is a choice rather than an inheritance. Two rules drove it:

1. Nothing above `EmailTransport` knows which provider is in use. The digest
   builder composes an `EmailMessage` and hands it over; swapping Resend for
   Postmark is a new class and one settings value, not a change to the caller.
2. The default transport sends nothing. `ConsoleTransport` writes the rendered
   message to a stream and records it, which is what the dry run and the tests
   use. A misconfigured deploy therefore prints mail rather than delivering it,
   which is the failure you want in this direction.

Secrets: the API key is read from settings, passed to the transport, and never
logged. `_redact_error` additionally scrubs it out of provider error text before
that text reaches a log line or an exception message, because an HTTP client
that echoes the request back on failure is a real pattern and one we should not
have to trust. Recipients are logged through `redact_email`, never in full and
never as a list.
"""
from __future__ import annotations

import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol, TextIO, runtime_checkable

import httpx
import structlog

from job_os.settings import Settings, get_settings

log = structlog.get_logger(__name__)

#: Provider calls are given a short leash. A digest run is a batch job, so a
#: hung socket costs the whole run, and Resend answers in well under a second.
DEFAULT_TIMEOUT_SECONDS = 15.0


class EmailTransportError(RuntimeError):
    """The provider refused or failed to accept the message."""


class EmailNotConfiguredError(EmailTransportError):
    """The selected provider is missing a key or a from address.

    Separate from the generic error so a caller can tell "this deploy was never
    set up" apart from "the provider is having a bad day", and report the first
    as a configuration problem instead of retrying it.
    """


@dataclass(frozen=True, slots=True)
class EmailMessage:
    """One message, already rendered. Both bodies are required.

    HTML-only mail is filtered more aggressively and is unreadable in a plain
    text client, and a text part is cheap to produce when you are generating the
    HTML anyway, so the type does not offer the option of skipping it.
    """

    to: str
    subject: str
    text: str
    html: str
    #: Extra headers. Used for List-Unsubscribe and List-Unsubscribe-Post; see
    #: services/alert_digest.py.
    headers: Mapping[str, str] = field(default_factory=dict)
    reply_to: str | None = None

    def __post_init__(self) -> None:
        if not self.to.strip():
            raise ValueError("EmailMessage.to is empty")
        if not self.subject.strip():
            raise ValueError("EmailMessage.subject is empty")
        if not self.text.strip():
            raise ValueError("EmailMessage.text is empty")
        if not self.html.strip():
            raise ValueError("EmailMessage.html is empty")


@dataclass(frozen=True, slots=True)
class EmailSendResult:
    provider: str
    #: Provider-side id, when the provider returns one. Stored on the digest row
    #: so a bounce or a complaint can be traced back to a send.
    message_id: str | None
    accepted: bool


@runtime_checkable
class EmailTransport(Protocol):
    """What the digest sender depends on. Deliberately one method wide."""

    name: str

    async def send(self, message: EmailMessage) -> EmailSendResult: ...


def redact_email(address: str) -> str:
    """A form of an address that is safe to log.

    Keeps the first character of the local part and the whole domain, which is
    enough to correlate a log line with a user you already have on screen and
    not enough to harvest.
    """
    address = address.strip()
    local, _, domain = address.partition("@")
    if not domain:
        return "[invalid-address]"
    head = local[:1] or "?"
    return f"{head}***@{domain}"


def _redact_error(text: str, secret: str | None) -> str:
    if secret and secret in text:
        return text.replace(secret, "[redacted]")
    return text


class ConsoleTransport:
    """Renders to a stream and records what it "sent". Sends no mail.

    This is the transport the dry run uses, and the one tests assert against, so
    it keeps the messages rather than only printing them.
    """

    name = "console"

    def __init__(self, *, sender: str, stream: TextIO | None = None, quiet: bool = False) -> None:
        self.sender = sender
        self._stream = stream if stream is not None else sys.stdout
        self._quiet = quiet
        self.sent: list[EmailMessage] = []

    async def send(self, message: EmailMessage) -> EmailSendResult:
        self.sent.append(message)
        if not self._quiet:
            self._stream.write(_console_render(message, sender=self.sender))
            self._stream.flush()
        return EmailSendResult(
            provider=self.name, message_id=f"console-{len(self.sent)}", accepted=True
        )


def _console_render(message: EmailMessage, *, sender: str) -> str:
    rule = "=" * 72
    header_lines = "\n".join(f"{k}: {v}" for k, v in message.headers.items())
    return "\n".join(
        [
            rule,
            f"From: {sender}",
            f"To: {message.to}",
            f"Subject: {message.subject}",
            *( [f"Reply-To: {message.reply_to}"] if message.reply_to else [] ),
            *([header_lines] if header_lines else []),
            "-" * 72,
            message.text,
            "-" * 72,
            f"[html part: {len(message.html)} chars]",
            rule,
            "",
        ]
    )


class ResendTransport:
    """Resend's HTTP API (https://resend.com/docs/api-reference/emails/send-email).

    Chosen over SendGrid and Mailgun because it is the only one of the three
    with a standing free tier a personal project can live on: 3,000 emails a
    month and 100 a day as of August 2026. SendGrid retired its free plan for
    new accounts and Mailgun ended its GitHub Student Pack offer, so neither is
    reachable here without a card. See docs/ALERTS.md for the citations.

    Resend is HTTP only, no SMTP relay, which is why this is a client rather
    than an smtplib wrapper.
    """

    name = "resend"

    def __init__(
        self,
        *,
        api_key: str,
        sender: str,
        api_base: str = "https://api.resend.com",
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key:
            raise EmailNotConfiguredError("RESEND_API_KEY is not configured")
        if not sender:
            raise EmailNotConfiguredError("EMAIL_FROM is not configured")
        self._api_key = api_key
        self.sender = sender
        self._api_base = api_base.rstrip("/")
        self._timeout = timeout
        self._client = client

    async def send(self, message: EmailMessage) -> EmailSendResult:
        payload: dict[str, object] = {
            "from": self.sender,
            "to": [message.to],
            "subject": message.subject,
            "html": message.html,
            "text": message.text,
        }
        if message.headers:
            payload["headers"] = dict(message.headers)
        if message.reply_to:
            payload["reply_to"] = message.reply_to

        url = f"{self._api_base}/emails"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        try:
            if self._client is not None:
                response = await self._client.post(url, json=payload, headers=headers)
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(url, json=payload, headers=headers)
        except httpx.HTTPError as e:
            raise EmailTransportError(
                f"resend request failed: {_redact_error(str(e), self._api_key)}"
            ) from e

        if response.status_code >= 400:
            body = _redact_error(response.text[:500], self._api_key)
            log.warning(
                "email.resend.rejected",
                status=response.status_code,
                to=redact_email(message.to),
            )
            raise EmailTransportError(f"resend returned {response.status_code}: {body}")

        message_id: str | None = None
        try:
            data = response.json()
        except ValueError:
            data = None
        if isinstance(data, dict):
            raw_id = data.get("id")
            message_id = str(raw_id) if raw_id is not None else None

        log.info("email.resend.accepted", to=redact_email(message.to), message_id=message_id)
        return EmailSendResult(provider=self.name, message_id=message_id, accepted=True)


def build_transport(
    settings: Settings | None = None, *, stream: TextIO | None = None
) -> EmailTransport:
    """The configured transport, or the one that sends nothing.

    Raises `EmailNotConfiguredError` for a provider that is selected but not
    usable, rather than silently degrading to the console. Degrading would mean
    a deploy that believes it is emailing users is writing to a log nobody
    reads, which is the failure mode this whole module is arranged to avoid.
    """
    settings = settings or get_settings()
    sender = (settings.email_from or "").strip()

    if settings.email_provider == "resend":
        if not settings.resend_api_key:
            raise EmailNotConfiguredError(
                "EMAIL_PROVIDER=resend but RESEND_API_KEY is not set"
            )
        if not sender:
            raise EmailNotConfiguredError("EMAIL_PROVIDER=resend but EMAIL_FROM is not set")
        return ResendTransport(
            api_key=settings.resend_api_key,
            sender=sender,
            api_base=settings.resend_api_base,
        )

    return ConsoleTransport(sender=sender or "job.os <alerts@localhost>", stream=stream)
