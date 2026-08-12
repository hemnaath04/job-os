"""Job alerts: subscription CRUD, a dry-run preview, and public unsubscribe.

Every route here is authenticated except the unsubscribe pair, which cannot be.
CAN-SPAM says a recipient must be able to opt out without doing more than
visiting a single page and without providing anything beyond an email address
(https://www.ftc.gov/business-guidance/resources/can-spam-act-compliance-guide-business),
so a sign-in wall on the unsubscribe link would not comply. The link authorises
itself with an HMAC token instead; see services/alert_tokens.py.

GET and POST both honour it:

  POST  is the RFC 8058 one-click endpoint. Gmail and Yahoo POST here when the
        user hits their native unsubscribe control, and the RFC requires that it
        take effect with no confirmation step.
  GET   is the visible link in the email body. It also takes effect immediately,
        rather than showing a confirm button first, because a confirm button is a
        second page and the point is that one click is enough. The tradeoff is
        that a mail client which prefetches links can unsubscribe someone who
        never clicked, so the confirmation page leads with a resubscribe link
        using the same token. Losing one digest and getting an obvious way back
        is a better failure than making the opt-out harder for everyone.
"""
from __future__ import annotations

from datetime import UTC, datetime
from html import escape
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from job_os.auth import get_current_user
from job_os.db.models import (
    AlertCadence,
    AlertDigest,
    AlertSubscription,
    SavedSearch,
    User,
)
from job_os.db.session import get_session
from job_os.schemas.alerts import (
    AlertDigestRead,
    AlertPreviewJob,
    AlertPreviewResponse,
    AlertSubscriptionCreate,
    AlertSubscriptionRead,
    AlertSubscriptionUpdate,
    UnsubscribeResult,
)
from job_os.services.alert_tokens import (
    UnsubscribeScope,
    UnsubscribeSecretMissingError,
    UnsubscribeTokenError,
    parse_token,
)

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/alerts")


def _read(subscription: AlertSubscription, *, search_name: str) -> AlertSubscriptionRead:
    return AlertSubscriptionRead(
        id=subscription.id,
        saved_search_id=subscription.saved_search_id,
        saved_search_name=search_name,
        cadence=subscription.cadence.value,  # type: ignore[arg-type]
        timezone=subscription.timezone,
        send_hour_local=subscription.send_hour_local,
        send_weekday=subscription.send_weekday,
        quiet_hours_start_local=subscription.quiet_hours_start_local,
        quiet_hours_end_local=subscription.quiet_hours_end_local,
        active=subscription.active,
        unsubscribed_at=subscription.unsubscribed_at,
        last_sent_at=subscription.last_sent_at,
        last_checked_at=subscription.last_checked_at,
        last_sent_job_count=subscription.last_sent_job_count,
        created_at=subscription.created_at,
        updated_at=subscription.updated_at,
    )


@router.get("/subscriptions", response_model=list[AlertSubscriptionRead])
async def list_subscriptions(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[AlertSubscriptionRead]:
    rows = await session.execute(
        select(AlertSubscription, SavedSearch)
        .join(SavedSearch, SavedSearch.id == AlertSubscription.saved_search_id)
        .where(AlertSubscription.user_id == user.id)
        .order_by(AlertSubscription.created_at)
    )
    return [_read(sub, search_name=saved.name) for sub, saved in rows.all()]


@router.post("/subscriptions", response_model=AlertSubscriptionRead, status_code=201)
async def create_subscription(
    payload: AlertSubscriptionCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AlertSubscriptionRead:
    saved = await session.get(SavedSearch, payload.saved_search_id)
    if saved is None or saved.user_id != user.id:
        raise HTTPException(404, "saved search not found")

    existing = await session.execute(
        select(AlertSubscription).where(
            AlertSubscription.user_id == user.id,
            AlertSubscription.saved_search_id == payload.saved_search_id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(409, f"{saved.name!r} already has an alert")

    subscription = AlertSubscription(
        user_id=user.id,
        saved_search_id=payload.saved_search_id,
        cadence=AlertCadence(payload.cadence),
        timezone=payload.timezone,
        send_hour_local=payload.send_hour_local,
        send_weekday=payload.send_weekday,
        quiet_hours_start_local=payload.quiet_hours_start_local,
        quiet_hours_end_local=payload.quiet_hours_end_local,
    )
    session.add(subscription)
    await session.flush()
    return _read(subscription, search_name=saved.name)


@router.patch("/subscriptions/{subscription_id}", response_model=AlertSubscriptionRead)
async def update_subscription(
    subscription_id: UUID,
    payload: AlertSubscriptionUpdate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AlertSubscriptionRead:
    subscription = await session.get(AlertSubscription, subscription_id)
    if subscription is None or subscription.user_id != user.id:
        raise HTTPException(404, "alert not found")

    updates = payload.model_dump(exclude_none=True)
    if "cadence" in updates:
        subscription.cadence = AlertCadence(updates.pop("cadence"))
    if updates.get("active") is True and subscription.unsubscribed_at is not None:
        # Re-enabling through the app clears the unsubscribe record. The user is
        # signed in and asking for mail again, which is a fresh consent.
        subscription.unsubscribed_at = None
    for key, value in updates.items():
        setattr(subscription, key, value)
    await session.flush()

    saved = await session.get(SavedSearch, subscription.saved_search_id)
    return _read(subscription, search_name=saved.name if saved else "")


@router.delete("/subscriptions/{subscription_id}", status_code=204)
async def delete_subscription(
    subscription_id: UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    subscription = await session.get(AlertSubscription, subscription_id)
    if subscription is None or subscription.user_id != user.id:
        raise HTTPException(404, "alert not found")
    await session.delete(subscription)


@router.get("/digests", response_model=list[AlertDigestRead])
async def list_digests(
    limit: int = Query(default=20, ge=1, le=100),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[AlertDigest]:
    rows = await session.execute(
        select(AlertDigest)
        .where(AlertDigest.user_id == user.id)
        .order_by(AlertDigest.created_at.desc())
        .limit(limit)
    )
    return list(rows.scalars().all())


@router.post("/subscriptions/{subscription_id}/preview", response_model=AlertPreviewResponse)
async def preview_subscription(
    subscription_id: UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AlertPreviewResponse:
    """What the next digest would contain. Sends nothing and writes nothing.

    Runs the same code path as the cron, in dry-run mode, so the preview cannot
    disagree with the email. `force=True` because the user asking is the reason
    to run it, not the schedule.
    """
    from job_os.services.alert_runner import run_alerts

    subscription = await session.get(AlertSubscription, subscription_id)
    if subscription is None or subscription.user_id != user.id:
        raise HTTPException(404, "alert not found")

    report = await run_alerts(
        session, dry_run=True, subscription_id=subscription_id, force=True
    )
    if not report.outcomes:
        return AlertPreviewResponse(would_send=False, reason="no_subscription")

    outcome = report.outcomes[0]
    digest = outcome.digest
    if outcome.outcome != "would_send" or digest is None:
        return AlertPreviewResponse(
            would_send=False,
            candidates=outcome.candidates,
            deduped_count=outcome.deduped_count,
            reason=outcome.error or outcome.outcome,
        )

    return AlertPreviewResponse(
        would_send=True,
        subject=digest.subject,
        candidates=outcome.candidates,
        deduped_count=digest.deduped_count,
        repost_count=digest.repost_count,
        text_body=outcome.rendered_text,
        jobs=[
            AlertPreviewJob(
                title=job.title,
                company=job.company,
                location=job.location,
                url=job.url,
                source_label=job.source_label,
                salary=job.salary.text if job.salary else None,
                salary_from_posting_text=bool(job.salary and job.salary.from_posting_text),
                freshness=job.freshness.headline,
                freshness_caveat=job.freshness.caveat,
                is_repost=job.freshness.is_repost,
            )
            for job in digest.jobs
        ],
    )


# ---- Public unsubscribe -----------------------------------------------------


async def _honor_unsubscribe(session: AsyncSession, token: str) -> UnsubscribeResult:
    try:
        claim = parse_token(token)
    except UnsubscribeSecretMissingError as e:
        # Configuration, not a bad link. 503 so a monitor notices, and the user
        # is not told their valid link is invalid.
        raise HTTPException(503, "unsubscribe is not configured on this deployment") from e
    except UnsubscribeTokenError as e:
        raise HTTPException(400, "this unsubscribe link is not valid") from e

    if claim.scope is UnsubscribeScope.SUBSCRIPTION:
        query = select(AlertSubscription).where(AlertSubscription.id == claim.subject_id)
    else:
        query = select(AlertSubscription).where(AlertSubscription.user_id == claim.subject_id)

    rows = list((await session.execute(query)).scalars().all())
    if not rows:
        # The subscription is gone, which means the user is already not receiving
        # it. Report success: a 404 here reads as "your opt-out failed".
        return UnsubscribeResult(
            scope=claim.scope.value,  # type: ignore[arg-type]
            subscriptions_disabled=0,
            already_unsubscribed=True,
        )

    now = datetime.now(UTC)
    disabled = 0
    for subscription in rows:
        if not subscription.active and subscription.unsubscribed_at is not None:
            continue
        subscription.active = False
        subscription.unsubscribed_at = now
        disabled += 1
    await session.flush()

    log.info(
        "alerts.unsubscribed",
        scope=claim.scope.value,
        subject_id=str(claim.subject_id),
        disabled=disabled,
    )
    return UnsubscribeResult(
        scope=claim.scope.value,  # type: ignore[arg-type]
        subscriptions_disabled=disabled,
        already_unsubscribed=disabled == 0,
    )


@router.post("/unsubscribe", response_model=UnsubscribeResult)
async def unsubscribe_one_click(
    token: str = Query(min_length=8, max_length=512),
    session: AsyncSession = Depends(get_session),
) -> UnsubscribeResult:
    """RFC 8058 one-click endpoint. No auth, no confirmation, no body required."""
    return await _honor_unsubscribe(session, token)


@router.get("/unsubscribe", response_class=HTMLResponse)
async def unsubscribe_page(
    token: str = Query(min_length=8, max_length=512),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """The link in the email body. Takes effect, then confirms in plain HTML."""
    result = await _honor_unsubscribe(session, token)
    scope_line = (
        "You will not get this alert again."
        if result.scope == "sub"
        else "All of your job alerts are now off."
    )
    if result.already_unsubscribed:
        scope_line = "You were already unsubscribed. Nothing has changed."

    body = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Unsubscribed</title></head>
<body style="margin:0;padding:48px 20px;background-color:#f2f2f0;
font-family:Arial,Helvetica,sans-serif;color:#1a1a1a;">
<div style="max-width:520px;margin:0 auto;background-color:#ffffff;
border:1px solid #e2e2df;padding:28px;">
<h1 style="margin:0 0 12px 0;font-size:20px;">Unsubscribed</h1>
<p style="margin:0 0 16px 0;font-size:15px;line-height:1.5;">{escape(scope_line)}</p>
<p style="margin:0;font-size:13px;color:#5a5a5a;line-height:1.5;">
Did not mean to do that? Some mail apps follow links automatically.
{_resubscribe_hint()}
</p>
</div></body></html>"""
    # 200 with no-store: an unsubscribe confirmation that a proxy caches would
    # show the next person the same page without touching the database.
    return HTMLResponse(content=body, headers={"Cache-Control": "no-store"})


def _resubscribe_hint() -> str:
    """How to get mail back, for someone who unsubscribed by accident.

    A link into the app, never a token-bearing link. Re-enabling is the one
    direction that must require a session: a prefetched resubscribe URL would
    undo an opt-out we are legally required to honour, and the whole reason the
    unsubscribe side tolerates prefetching is that it errs toward less mail.

    Falls back to prose when the app origin is not configured, rather than
    emitting a relative link from the API origin that would 404.
    """
    from job_os.settings import get_settings

    base = (get_settings().alert_app_base_url or "").rstrip("/")
    if not base:
        return "Sign in to job.os and turn the alert back on under Alerts."
    url = escape(f"{base}/alerts")
    return f'<a href="{url}" style="color:#1a5fd0;">Turn alerts back on in job.os</a>.'
