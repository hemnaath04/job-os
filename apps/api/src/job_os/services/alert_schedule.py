"""When a subscription is due, in the user's own clock.

Every decision here is made in the subscription's timezone and then compared, so
"08:00 daily" means 08:00 where the user is on the day in question, including the
two days a year when that is not a fixed offset from UTC.

The cron that drives this runs on a fixed interval and each subscription decides
for itself whether its moment has arrived. That is why `is_due` takes `now` as an
argument instead of reading the clock: the whole of the boundary behaviour is
then testable without freezing time globally.

Boundaries, stated once so the tests and the code agree:

  daily     due when the local hour has reached send_hour_local AND nothing has
            been sent yet on that local calendar day.
  weekly    the same, plus the local weekday must equal send_weekday.
  immediate due when the local time is outside quiet hours and at least
            `immediate_min_interval_minutes` have passed since the last send.

Quiet hours apply to `immediate` only. A daily digest already carries an explicit
hour the user chose; letting quiet hours override it would mean someone who asked
for 23:00 gets nothing forever and no error anywhere says why.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import structlog

from job_os.db.models.alert import AlertCadence

log = structlog.get_logger(__name__)

DEFAULT_IMMEDIATE_MIN_INTERVAL_MINUTES = 60


@dataclass(frozen=True, slots=True)
class DueDecision:
    due: bool
    #: Short machine-readable why. Written to the run report so a subscription
    #: that never fires can be explained without adding logging later.
    reason: str


def resolve_zone(name: str | None) -> ZoneInfo:
    """The subscription's zone, or UTC if the name is unusable.

    Falls back rather than raising. A bad timezone string should cost the user a
    digest at the wrong hour, not their alerts entirely, and the warning is
    enough to find it.
    """
    if not name:
        return ZoneInfo("UTC")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        log.warning("alerts.timezone_unresolved", timezone=name)
        return ZoneInfo("UTC")


def _as_utc(value: datetime) -> datetime:
    """Naive datetimes are read as UTC.

    Postgres hands back aware values for `timestamptz`, so a naive one here came
    from a caller or a fixture. Assuming UTC matches what the database would have
    stored and is the only assumption that does not shift a timestamp.
    """
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def in_quiet_hours(local: datetime, *, start_hour: int, end_hour: int) -> bool:
    """Whether a local time falls in the half-open window [start, end).

    Handles the window that wraps midnight, which is the common case: quiet hours
    are almost always something like 22:00 to 07:00. start == end means no quiet
    hours at all, not a 24 hour blackout, because the alternative reading turns a
    plausible pair of equal values into total silence.
    """
    hour = local.hour
    if start_hour == end_hour:
        return False
    if start_hour < end_hour:
        return start_hour <= hour < end_hour
    return hour >= start_hour or hour < end_hour


@dataclass(frozen=True, slots=True)
class SchedulePolicy:
    """The scheduling fields of a subscription, lifted out of the ORM row.

    A plain value object so the boundary logic can be exercised without a
    database, and so `is_due` cannot accidentally reach for a field it should not
    be reading.
    """

    cadence: AlertCadence
    timezone: str = "UTC"
    send_hour_local: int = 8
    send_weekday: int = 0
    quiet_hours_start_local: int = 22
    quiet_hours_end_local: int = 7
    last_sent_at: datetime | None = None
    active: bool = True

    @classmethod
    def from_subscription(cls, subscription: object) -> SchedulePolicy:
        """Read the policy off an `AlertSubscription` row.

        Typed as `object` on purpose: importing the model here would make this
        module unusable from a test that has no database configured, and the
        attribute set is the whole contract.
        """
        return cls(
            # getattr rather than attribute access because the parameter is typed
            # `object`, which is what keeps this module importable without the
            # ORM. No default: a row with no cadence is a bug, and raising beats
            # quietly scheduling it as something the user did not choose.
            cadence=getattr(subscription, "cadence"),  # noqa: B009
            timezone=getattr(subscription, "timezone", "UTC") or "UTC",
            send_hour_local=getattr(subscription, "send_hour_local", 8),
            send_weekday=getattr(subscription, "send_weekday", 0),
            quiet_hours_start_local=getattr(subscription, "quiet_hours_start_local", 22),
            quiet_hours_end_local=getattr(subscription, "quiet_hours_end_local", 7),
            last_sent_at=getattr(subscription, "last_sent_at", None),
            active=bool(getattr(subscription, "active", True)),
        )


def is_due(
    policy: SchedulePolicy,
    now: datetime,
    *,
    immediate_min_interval_minutes: int = DEFAULT_IMMEDIATE_MIN_INTERVAL_MINUTES,
) -> DueDecision:
    if not policy.active:
        return DueDecision(False, "inactive")

    zone = resolve_zone(policy.timezone)
    now_utc = _as_utc(now)
    local = now_utc.astimezone(zone)
    last_sent = _as_utc(policy.last_sent_at) if policy.last_sent_at else None

    if policy.cadence is AlertCadence.IMMEDIATE:
        if in_quiet_hours(
            local,
            start_hour=policy.quiet_hours_start_local,
            end_hour=policy.quiet_hours_end_local,
        ):
            return DueDecision(False, "quiet_hours")
        if last_sent is not None:
            elapsed = now_utc - last_sent
            if elapsed < timedelta(minutes=immediate_min_interval_minutes):
                return DueDecision(False, "min_interval")
        return DueDecision(True, "immediate")

    if local.hour < policy.send_hour_local:
        return DueDecision(False, "before_send_hour")

    if policy.cadence is AlertCadence.WEEKLY and local.weekday() != policy.send_weekday:
        return DueDecision(False, "wrong_weekday")

    if last_sent is not None:
        last_local = last_sent.astimezone(zone)
        if policy.cadence is AlertCadence.DAILY:
            if last_local.date() == local.date():
                return DueDecision(False, "already_sent_today")
        # Weekly compares dates too, not "seven days ago". A run that slipped by
        # an hour last week must not push this week's send an hour later and
        # eventually off the chosen weekday altogether.
        elif last_local.date() == local.date():
            return DueDecision(False, "already_sent_today")

    return DueDecision(True, policy.cadence.value)
