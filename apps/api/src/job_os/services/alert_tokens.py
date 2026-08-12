"""Unsubscribe tokens: signed, stateless, and usable with no session.

CAN-SPAM requires that a recipient can opt out without paying a fee, without
handing over anything beyond an email address, and without doing more than
sending a reply or visiting a single page
(https://www.ftc.gov/business-guidance/resources/can-spam-act-compliance-guide-business).
"Visiting a single page" rules out a sign-in wall, so the link has to authorise
itself. It also has to keep working for at least 30 days after the message goes
out, which rules out a short expiry.

Signed rather than stored, for two reasons. A stored random token is one more
write on the send path and one more row to lose; and a signature lets the token
carry its own scope, so the same mechanism covers "stop this one alert" and "stop
all of them" without a second table. The tradeoff is that revoking a leaked token
means rotating ALERT_UNSUBSCRIBE_SECRET, which invalidates every outstanding
link. That is acceptable for an unsubscribe link, whose worst-case misuse is
turning off mail the recipient can turn back on.

Format: `v1.<scope>.<subject>.<mac>`, all base64url, no padding. The version
prefix is there so a future change to the MAC input does not have to guess at
what an old token meant.
"""
from __future__ import annotations

import base64
import hmac
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from urllib.parse import quote
from uuid import UUID

from job_os.settings import Settings, get_settings

#: Format version, not a credential. S105 pattern-matches the name.
TOKEN_VERSION = "v1"  # noqa: S105


class UnsubscribeScope(StrEnum):
    #: Turn off one alert subscription.
    SUBSCRIPTION = "sub"
    #: Turn off every alert the user has. The "stop all job alerts" link.
    ALL = "all"


class UnsubscribeTokenError(ValueError):
    """The token is malformed, mis-signed, or built for a different secret."""


class UnsubscribeSecretMissingError(RuntimeError):
    """ALERT_UNSUBSCRIBE_SECRET is not configured.

    Raised rather than falling back to a derived or empty key. A forgeable
    unsubscribe token is worse than a digest that refuses to render, because the
    forgeable one fails silently and in the user's mailbox.
    """


@dataclass(frozen=True, slots=True)
class UnsubscribeClaim:
    scope: UnsubscribeScope
    #: The subscription id for SUBSCRIPTION scope, the user id for ALL.
    subject_id: UUID


def _secret(settings: Settings | None = None) -> bytes:
    settings = settings or get_settings()
    raw = (settings.alert_unsubscribe_secret or "").strip()
    if not raw:
        raise UnsubscribeSecretMissingError(
            "ALERT_UNSUBSCRIBE_SECRET is not configured, so unsubscribe links "
            "cannot be signed and no digest may be sent"
        )
    return raw.encode("utf-8")


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(value + padding)
    except (ValueError, TypeError) as e:
        raise UnsubscribeTokenError("token segment is not base64url") from e


def _mac(scope: str, subject: str, *, settings: Settings | None = None) -> str:
    # Length-prefixed rather than concatenated. "sub" + "abc" and "su" + "babc"
    # hash to the same thing under naive concatenation, and while neither is
    # reachable with fixed-length UUIDs today, a future scope with a variable
    # subject would quietly make it reachable.
    payload = f"{TOKEN_VERSION}|{len(scope)}:{scope}|{len(subject)}:{subject}".encode()
    digest = hmac.new(_secret(settings), payload, sha256).digest()
    return _b64(digest)


def make_token(claim: UnsubscribeClaim, *, settings: Settings | None = None) -> str:
    scope = claim.scope.value
    subject = str(claim.subject_id)
    mac = _mac(scope, subject, settings=settings)
    return ".".join([TOKEN_VERSION, _b64(scope.encode()), _b64(subject.encode()), mac])


def parse_token(token: str, *, settings: Settings | None = None) -> UnsubscribeClaim:
    """Verify and decode a token. Raises `UnsubscribeTokenError` on any problem.

    Every failure path raises the same error type with a generic message. An
    endpoint that reported "bad signature" separately from "unknown scope" would
    be telling an attacker which half of the token to keep.
    """
    parts = token.strip().split(".")
    if len(parts) != 4:
        raise UnsubscribeTokenError("invalid unsubscribe token")
    version, scope_b64, subject_b64, mac = parts
    if version != TOKEN_VERSION:
        raise UnsubscribeTokenError("invalid unsubscribe token")

    try:
        scope_raw = _unb64(scope_b64).decode("utf-8")
        subject_raw = _unb64(subject_b64).decode("utf-8")
    except UnicodeDecodeError as e:
        raise UnsubscribeTokenError("invalid unsubscribe token") from e

    expected = _mac(scope_raw, subject_raw, settings=settings)
    if not hmac.compare_digest(expected, mac):
        raise UnsubscribeTokenError("invalid unsubscribe token")

    try:
        scope = UnsubscribeScope(scope_raw)
    except ValueError as e:
        raise UnsubscribeTokenError("invalid unsubscribe token") from e
    try:
        subject_id = UUID(subject_raw)
    except ValueError as e:
        raise UnsubscribeTokenError("invalid unsubscribe token") from e

    return UnsubscribeClaim(scope=scope, subject_id=subject_id)


def unsubscribe_url(
    claim: UnsubscribeClaim, *, base_url: str, settings: Settings | None = None
) -> str:
    """The absolute link that goes in the email.

    Points at the API origin, not the web app: the web app's routes are behind
    Clerk, and a link that redirects to a sign-in page is not a one-click
    unsubscribe.
    """
    token = make_token(claim, settings=settings)
    return f"{base_url.rstrip('/')}/api/v1/alerts/unsubscribe?token={quote(token, safe='')}"
