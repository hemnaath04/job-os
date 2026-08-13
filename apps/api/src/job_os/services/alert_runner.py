"""The digest run: find what is due, build it, send it, record it.

Called from `scripts/run_job_alerts.py` (the cron entrypoint) and from the
"send me a preview" route. Nothing in here reads the clock or the config
directly, so both callers can drive it deterministically.

Two safety properties, both enforced here rather than left to the caller:

* A dry run writes nothing. Not the digest row, not the sent log, not
  `last_sent_at`. That matters more than it looks: a dry run that recorded sends
  would silently poison the dedupe ledger and the first real digest would arrive
  empty, which is the worst possible way to discover the bug.
* Sending requires three separate things to be true: `alerts_enabled`, a
  configured provider, and an explicit `dry_run=False` from the caller. Any one
  of them missing and the run reports what it would have done.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from job_os.db.models import (
    AlertDigest,
    AlertDigestStatus,
    AlertSend,
    AlertSubscription,
    Job,
    SavedSearch,
    User,
)
from job_os.integrations.email import (
    EmailTransport,
    EmailTransportError,
    build_transport,
    redact_email,
)
from job_os.schemas.discovery import DiscoverySearchRequest
from job_os.services.alert_digest import (
    CandidateJob,
    Digest,
    build_digest,
    content_key,
    render_html,
    render_text,
    to_email_message,
)
from job_os.services.alert_schedule import SchedulePolicy, is_due
from job_os.services.alert_tokens import (
    UnsubscribeClaim,
    UnsubscribeScope,
    unsubscribe_url,
)
from job_os.settings import Settings, get_settings

log = structlog.get_logger(__name__)


class AlertsNotConfiguredError(RuntimeError):
    """Something required for a real send is missing.

    Raised only on the sending path. A dry run works without a provider or a
    postal address, because rendering is the point of a dry run.
    """


@dataclass(slots=True)
class SubscriptionOutcome:
    subscription_id: UUID
    search_name: str
    #: One of: sent, would_send, skipped_not_due, skipped_empty, failed.
    outcome: str
    reason: str = ""
    candidates: int = 0
    job_count: int = 0
    deduped_count: int = 0
    repost_count: int = 0
    #: Populated on a dry run so the caller can print or file it.
    rendered_text: str | None = None
    rendered_html: str | None = None
    subject: str | None = None
    error: str | None = None
    #: The composed digest, on a dry run only. Carried so the preview endpoint can
    #: report the rows without running the search a second time. Not set on a real
    #: send, where the rows are already in alert_sends.
    digest: Digest | None = None


@dataclass(slots=True)
class RunReport:
    started_at: datetime
    dry_run: bool
    outcomes: list[SubscriptionOutcome] = field(default_factory=list)

    @property
    def sent(self) -> int:
        return sum(1 for o in self.outcomes if o.outcome == "sent")

    @property
    def would_send(self) -> int:
        return sum(1 for o in self.outcomes if o.outcome == "would_send")

    @property
    def failed(self) -> int:
        return sum(1 for o in self.outcomes if o.outcome == "failed")

    def summary_line(self) -> str:
        verb = "would send" if self.dry_run else "sent"
        count = self.would_send if self.dry_run else self.sent
        return (
            f"{len(self.outcomes)} subscription(s) checked, {verb} {count}, "
            f"{self.failed} failed"
        )


def require_send_config(settings: Settings) -> None:
    """Everything that has to be true before a real email may go out."""
    if not settings.alerts_enabled:
        raise AlertsNotConfiguredError(
            "ALERTS_ENABLED is false. The digest run will not send while it is off."
        )
    if not settings.alert_unsubscribe_secret:
        raise AlertsNotConfiguredError(
            "ALERT_UNSUBSCRIBE_SECRET is not set, so unsubscribe links would be forgeable."
        )
    if not settings.alert_postal_address:
        raise AlertsNotConfiguredError(
            "ALERT_POSTAL_ADDRESS is not set. CAN-SPAM requires a valid physical "
            "postal address in every commercial message."
        )
    if not settings.alert_link_base_url:
        raise AlertsNotConfiguredError(
            "ALERT_LINK_BASE_URL is not set, so the unsubscribe link would be relative."
        )
    if settings.email_provider == "console":
        raise AlertsNotConfiguredError(
            "EMAIL_PROVIDER is console, which delivers nothing. Set it to resend to send."
        )


async def load_due_subscriptions(
    session: AsyncSession,
    *,
    now: datetime,
    settings: Settings,
    subscription_id: UUID | None = None,
    force: bool = False,
) -> list[tuple[AlertSubscription, SavedSearch, User, str]]:
    """Active subscriptions with their search and user, plus a due reason.

    Returns the not-due ones too, tagged with why, so the report can explain a
    quiet run instead of showing an empty list.
    """
    query = (
        select(AlertSubscription, SavedSearch, User)
        .join(SavedSearch, SavedSearch.id == AlertSubscription.saved_search_id)
        .join(User, User.id == AlertSubscription.user_id)
        .where(AlertSubscription.active.is_(True))
        .order_by(AlertSubscription.created_at)
    )
    if subscription_id is not None:
        query = query.where(AlertSubscription.id == subscription_id)

    rows = (await session.execute(query)).all()
    out: list[tuple[AlertSubscription, SavedSearch, User, str]] = []
    for subscription, saved, user in rows:
        if force:
            out.append((subscription, saved, user, "forced"))
            continue
        decision = is_due(
            SchedulePolicy.from_subscription(subscription),
            now,
            immediate_min_interval_minutes=settings.alert_immediate_min_interval_minutes,
        )
        # A leading "!" marks a not-due reason, so one list carries both and the
        # report can say why a subscription stayed quiet.
        tag = decision.reason if decision.due else f"!{decision.reason}"
        out.append((subscription, saved, user, tag))
    return out


async def _load_sent_keys(session: AsyncSession, user_id: UUID) -> tuple[set[str], set[str]]:
    rows = await session.execute(
        select(AlertSend.source_key, AlertSend.content_key).where(AlertSend.user_id == user_id)
    )
    source_keys: set[str] = set()
    content_keys: set[str] = set()
    for source_key_value, content_key_value in rows.all():
        source_keys.add(source_key_value)
        content_keys.add(content_key_value)
    return source_keys, content_keys


async def _load_known_first_seen(
    session: AsyncSession, user_id: UUID, candidates: Sequence[CandidateJob]
) -> dict[str, datetime]:
    """Earliest known sighting per content_key, from the sent log and from jobs.

    This is what turns a repost into a labelled repost. Both sources are our own
    observations, so a date out of here can be stated as fact rather than as the
    board's claim.

    The sent log is scoped to the user, because that is where it lives. The
    `jobs` lookup is narrowed by title rather than scanning the table: a content
    key cannot be computed in SQL, so the titles in this batch are the cheapest
    filter that cannot miss a match.
    """
    wanted = {c.content_key for c in candidates}
    if not wanted:
        return {}

    earliest: dict[str, datetime] = {}

    def offer(key: str, value: datetime | None) -> None:
        if value is None or key not in wanted:
            return
        aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        current = earliest.get(key)
        if current is None or aware < current:
            earliest[key] = aware

    sent_rows = await session.execute(
        select(AlertSend.content_key, AlertSend.first_seen_at, AlertSend.created_at).where(
            AlertSend.user_id == user_id, AlertSend.content_key.in_(wanted)
        )
    )
    for key, first_seen, created in sent_rows.all():
        offer(key, first_seen or created)

    # Imported jobs carry a first_seen_at written when we ingested them. Matched
    # on content rather than on id, so a repost under a new source_id still finds
    # the original listing's date.
    titles = {c.title for c in candidates if c.title}
    if titles:
        job_rows = await session.execute(select(Job).where(Job.title.in_(titles)))
        for job in job_rows.unique().scalars().all():
            key = content_key(
                company_name=job.company.name if job.company else None,
                title=job.title,
                location=job.location,
            )
            offer(key, job.first_seen_at)

    return earliest


async def run_alerts(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    dry_run: bool = True,
    subscription_id: UUID | None = None,
    force: bool = False,
    transport: EmailTransport | None = None,
    settings: Settings | None = None,
) -> RunReport:
    settings = settings or get_settings()
    now = now or datetime.now(UTC)
    report = RunReport(started_at=now, dry_run=dry_run)

    if not dry_run:
        require_send_config(settings)
        transport = transport or build_transport(settings)

    base_url = settings.alert_link_base_url or "https://alerts.invalid"
    postal_address = settings.alert_postal_address or "[ALERT_POSTAL_ADDRESS is not set]"

    due = await load_due_subscriptions(
        session,
        now=now,
        settings=settings,
        subscription_id=subscription_id,
        force=force,
    )

    for subscription, saved, user, reason in due:
        if reason.startswith("!"):
            report.outcomes.append(
                SubscriptionOutcome(
                    subscription_id=subscription.id,
                    search_name=saved.name,
                    outcome="skipped_not_due",
                    reason=reason[1:],
                )
            )
            continue

        outcome = await _run_one(
            session,
            subscription=subscription,
            saved=saved,
            user=user,
            reason=reason,
            now=now,
            dry_run=dry_run,
            transport=transport,
            settings=settings,
            base_url=base_url,
            postal_address=postal_address,
        )
        report.outcomes.append(outcome)

    log.info(
        "alerts.run_complete",
        dry_run=dry_run,
        checked=len(report.outcomes),
        sent=report.sent,
        would_send=report.would_send,
        failed=report.failed,
    )
    return report


def _unsubscribe_link(
    scope: UnsubscribeScope, subject_id: UUID, *, base_url: str, settings: Settings
) -> str:
    """A signed unsubscribe link, or a visibly fake one on an unconfigured dry run.

    The placeholder is deliberately unmistakable. A dry run has to be able to
    render the email on a machine with no secret configured, and the alternative
    to a loud placeholder is either crashing the preview or emitting something
    that looks like a working link and is not.
    """
    if not settings.alert_unsubscribe_secret:
        return f"{base_url.rstrip('/')}/api/v1/alerts/unsubscribe?token=DRY-RUN-UNSIGNED-TOKEN"
    return unsubscribe_url(
        UnsubscribeClaim(scope, subject_id), base_url=base_url, settings=settings
    )


async def _run_one(
    session: AsyncSession,
    *,
    subscription: AlertSubscription,
    saved: SavedSearch,
    user: User,
    reason: str,
    now: datetime,
    dry_run: bool,
    transport: EmailTransport | None,
    settings: Settings,
    base_url: str,
    postal_address: str,
) -> SubscriptionOutcome:
    # Imported here rather than at module scope. `_run_search` is the single
    # fan-out implementation shared with the interactive endpoint; duplicating it
    # would let the alert path drift from what a user sees when they click Run,
    # which is exactly the kind of divergence that produces "the email showed me
    # something the app does not".
    from job_os.routers.discovery import _run_search

    outcome = SubscriptionOutcome(
        subscription_id=subscription.id, search_name=saved.name, outcome="failed", reason=reason
    )

    try:
        query = DiscoverySearchRequest.model_validate(saved.query or {})
        response = await _run_search(query, session)
    except Exception as e:  # noqa: BLE001 - one bad subscription must not end the run
        outcome.error = str(e)
        log.warning(
            "alerts.search_failed", subscription_id=str(subscription.id), error=str(e)
        )
        return outcome

    candidates = [CandidateJob.from_discovery_result(r) for r in response.results]
    outcome.candidates = len(candidates)

    sent_source_keys, sent_content_keys = await _load_sent_keys(session, user.id)
    known_first_seen = await _load_known_first_seen(session, user.id, candidates)

    digest = build_digest(
        subscription_id=subscription.id,
        user_id=user.id,
        recipient=user.email,
        search_name=saved.name,
        cadence=subscription.cadence.value,
        candidates=candidates,
        already_sent_source_keys=sent_source_keys,
        already_sent_content_keys=sent_content_keys,
        known_first_seen=known_first_seen,
        unsubscribe_url=_unsubscribe_link(
            UnsubscribeScope.SUBSCRIPTION, subscription.id, base_url=base_url, settings=settings
        ),
        unsubscribe_all_url=_unsubscribe_link(
            UnsubscribeScope.ALL, user.id, base_url=base_url, settings=settings
        ),
        postal_address=postal_address,
        now=now,
        max_jobs=settings.alert_max_jobs_per_digest,
    )

    if digest is None:
        # Nothing new. Record the check so a subscription that keeps finding
        # nothing is visibly running rather than looking abandoned, and send no
        # mail: an "0 new roles" email is the fastest way to be marked as spam.
        outcome.outcome = "skipped_empty"
        outcome.deduped_count = len(candidates)
        if not dry_run:
            subscription.last_checked_at = now
            await session.flush()
        return outcome

    outcome.job_count = len(digest.jobs)
    outcome.deduped_count = digest.deduped_count
    outcome.repost_count = digest.repost_count
    outcome.subject = digest.subject

    if dry_run:
        outcome.outcome = "would_send"
        outcome.rendered_text = render_text(digest)
        outcome.rendered_html = render_html(digest)
        outcome.digest = digest
        return outcome

    assert transport is not None  # guaranteed by run_alerts when dry_run is False
    message = to_email_message(digest, reply_to=settings.email_reply_to)
    try:
        result = await transport.send(message)
    except EmailTransportError as e:
        outcome.error = str(e)
        session.add(
            AlertDigest(
                subscription_id=subscription.id,
                user_id=user.id,
                status=AlertDigestStatus.FAILED,
                subject=digest.subject,
                job_count=len(digest.jobs),
                deduped_count=digest.deduped_count,
                provider=getattr(transport, "name", None),
                error=str(e)[:2000],
            )
        )
        subscription.last_checked_at = now
        await session.flush()
        log.warning(
            "alerts.send_failed",
            subscription_id=str(subscription.id),
            to=redact_email(user.email),
            error=str(e),
        )
        return outcome

    # Provider accepted. Only now does the sent log grow, so a failed send leaves
    # the jobs eligible for the next run rather than swallowing them.
    digest_row = AlertDigest(
        subscription_id=subscription.id,
        user_id=user.id,
        status=AlertDigestStatus.SENT,
        subject=digest.subject,
        job_count=len(digest.jobs),
        deduped_count=digest.deduped_count,
        provider=result.provider,
        provider_message_id=result.message_id,
    )
    session.add(digest_row)
    await session.flush()
    _record_sends(session, digest, digest_row_id=digest_row.id)

    subscription.last_sent_at = now
    subscription.last_checked_at = now
    subscription.last_sent_job_count = len(digest.jobs)
    await session.flush()

    outcome.outcome = "sent"
    log.info(
        "alerts.sent",
        subscription_id=str(subscription.id),
        to=redact_email(user.email),
        job_count=len(digest.jobs),
        deduped=digest.deduped_count,
    )
    return outcome


def _record_sends(session: AsyncSession, digest: Digest, *, digest_row_id: UUID) -> None:
    for job in digest.jobs:
        session.add(
            AlertSend(
                user_id=digest.user_id,
                subscription_id=digest.subscription_id,
                digest_id=digest_row_id,
                source=job.source,
                source_id=job.source_id,
                source_key=job.source_key,
                content_key=job.content_key,
                title=job.title,
                company_name=job.company,
                posted_at=job.posted_at,
                # What we believe now. When we have no earlier sighting, this run
                # IS the first sighting, and recording that is what lets the next
                # repost be caught.
                first_seen_at=job.first_seen_at or digest.generated_at,
            )
        )
