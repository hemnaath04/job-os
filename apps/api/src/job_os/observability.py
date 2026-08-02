"""Structured logging and Sentry, shared by the API container and the agent function.

Two jobs, and the first one is why the second works at all.

1. Point structlog at stdlib logging. Every module here does
   `structlog.get_logger()`, but `structlog.configure()` was never called, so
   structlog used its default PrintLogger and wrote straight to stdout. That
   bypasses `logging` entirely, which is why `LOG_LEVEL` was silently ignored
   and why any log-shipping integration would have seen nothing. Sentry hooks
   stdlib, so this bridge is the difference between "errors arrive" and "errors
   and the log lines around them arrive".

2. Initialise Sentry, with user content stripped on the way out. The codebase
   deliberately logs previews of model output, job descriptions and resume text
   to make failures diagnosable. That is useful in your own stdout and quite
   different from shipping other people's resumes to a third party, so the
   scrubber below drops those payloads and keeps the surrounding context.

Both functions are safe to call more than once and safe to call with nothing
configured: no DSN means logging is still set up and Sentry stays off.
"""
from __future__ import annotations

import logging
import os
import sys
from typing import Any

import structlog

# Log fields that carry user or model content rather than diagnostics. The value
# is replaced, never the key, so the shape of the event still tells you what
# happened. Matched as substrings against the lower-cased key, which is what
# catches jd_raw, jd_clean, preview and retried_preview in one rule.
_REDACT_KEY_PARTS = (
    "preview",
    "raw",
    "body",
    "snapshot",
    "description",
    "jd_",
    "resume",
    "latex_source",
    "markdown",
    "bullet",
    "summary",
    "content",
    "email",
    "token",
    "secret",
    "password",
    "authorization",
    "api_key",
)

_REDACTED = "[redacted]"


def _should_redact(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in _REDACT_KEY_PARTS)


def _scrub(value: Any, depth: int = 0) -> Any:
    """Redact content-bearing fields anywhere in a nested structure."""
    if depth > 4:
        return value
    if isinstance(value, dict):
        return {
            k: (_REDACTED if _should_redact(str(k)) else _scrub(v, depth + 1))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_scrub(item, depth + 1) for item in value[:50]]
    return value


def configure_logging() -> None:
    """Route structlog through stdlib logging at the configured level."""
    level_name = (os.getenv("LOG_LEVEL") or "info").upper()
    level = getattr(logging, level_name, logging.INFO)

    # `format="%(message)s"` because the processor chain below has already
    # rendered the line; stdlib should not decorate it a second time.
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level, force=True)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.dev.ConsoleRenderer(colors=False),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def init_sentry(component: str) -> bool:
    """Start Sentry for this process. Returns whether it was enabled.

    `component` separates the API container from the agent function inside one
    Sentry project, so an issue can be traced to the runtime that raised it
    without needing two DSNs.
    """
    dsn = os.getenv("SENTRY_DSN")
    if not dsn:
        return False

    try:
        import sentry_sdk
    except ImportError:
        # The agent function ships its own dependency set. A missing SDK there
        # must not take the function down, and silence is the wrong outcome too.
        logging.getLogger(__name__).warning(
            "sentry_dsn set but sentry_sdk is not installed, skipping"
        )
        return False

    def before_send(event: dict[str, Any], _hint: dict[str, Any]) -> dict[str, Any]:
        for key in ("extra", "contexts", "request", "tags"):
            if key in event:
                event[key] = _scrub(event[key])
        return event

    def before_send_log(log: dict[str, Any], _hint: dict[str, Any]) -> dict[str, Any]:
        if "attributes" in log:
            log["attributes"] = _scrub(log["attributes"])
        return log

    sentry_sdk.init(
        dsn=dsn,
        environment=os.getenv("APP_ENV") or "production",
        release=os.getenv("SENTRY_RELEASE") or None,
        # Every log line, which is the point: a user reporting "it broke" should
        # be answerable from what is already recorded.
        enable_logs=True,
        # send_default_pii stays off. Sentry would otherwise attach request
        # bodies and headers, and the request bodies here are resumes.
        send_default_pii=False,
        # Tracing is sampled rather than off: enough to see a slow tailor, not
        # enough to pay for a trace on every request.
        traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE") or 0.1),
        before_send=before_send,
        before_send_log=before_send_log,
    )
    sentry_sdk.set_tag("component", component)
    return True


def setup_observability(component: str) -> None:
    """Configure logging, then Sentry on top of it. Order matters."""
    configure_logging()
    enabled = init_sentry(component)
    structlog.get_logger(__name__).info(
        "observability.ready", component=component, sentry=enabled
    )
