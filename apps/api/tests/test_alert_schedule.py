"""Cadence boundaries, in the user's clock rather than the server's.

Everything here drives `is_due` with an explicit `now`, which is the reason it
takes one. The cases that matter are the edges: the hour it becomes due, the
second send on the same day, the weekday, quiet hours wrapping midnight, and the
two days a year when a fixed UTC offset would drift an hour.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from job_os.db.models.alert import AlertCadence
from job_os.services.alert_schedule import (
    SchedulePolicy,
    in_quiet_hours,
    is_due,
    resolve_zone,
)


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


def daily(**overrides: object) -> SchedulePolicy:
    base: dict[str, object] = {
        "cadence": AlertCadence.DAILY,
        "timezone": "UTC",
        "send_hour_local": 8,
    }
    base.update(overrides)
    return SchedulePolicy(**base)  # type: ignore[arg-type]


# ---- daily ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("now", "expected_due", "expected_reason"),
    [
        ("2026-08-12T07:59:00", False, "before_send_hour"),
        # The boundary. The top of the send hour is due, not the end of it.
        ("2026-08-12T08:00:00", True, "daily"),
        ("2026-08-12T08:01:00", True, "daily"),
        ("2026-08-12T23:59:00", True, "daily"),
    ],
)
def test_a_daily_alert_becomes_due_at_the_top_of_its_send_hour(
    now: str, expected_due: bool, expected_reason: str
) -> None:
    decision = is_due(daily(), utc(now))

    assert decision.due is expected_due
    assert decision.reason == expected_reason


def test_a_daily_alert_does_not_send_twice_on_the_same_local_day() -> None:
    policy = daily(last_sent_at=utc("2026-08-12T08:00:00"))

    assert is_due(policy, utc("2026-08-12T20:00:00")).reason == "already_sent_today"


def test_a_daily_alert_is_due_again_the_next_local_day() -> None:
    policy = daily(last_sent_at=utc("2026-08-12T08:00:00"))

    decision = is_due(policy, utc("2026-08-13T08:00:00"))

    assert decision.due is True


def test_a_send_that_slipped_late_still_leaves_the_next_day_due_on_time() -> None:
    """Yesterday's digest going out at 23:30 must not push today's past 08:00.

    The check is on the local calendar date, not on elapsed hours, precisely so a
    late run cannot walk the send time forward a little each day.
    """
    policy = daily(last_sent_at=utc("2026-08-12T23:30:00"))

    assert is_due(policy, utc("2026-08-13T08:00:00")).due is True


def test_an_inactive_subscription_is_never_due() -> None:
    policy = daily(active=False)

    decision = is_due(policy, utc("2026-08-12T12:00:00"))

    assert decision.due is False
    assert decision.reason == "inactive"


# ---- timezones and daylight saving ------------------------------------------


def test_eight_local_means_eight_local_on_both_sides_of_a_dst_change() -> None:
    """The reason the column stores an IANA name and not a UTC offset.

    New York is UTC-4 in August and UTC-5 in January. A subscription asking for
    08:00 local has to fire at 12:00 UTC in summer and 13:00 UTC in winter, and a
    stored offset would get one of the two wrong for half the year.
    """
    policy = daily(timezone="America/New_York")

    # Summer: 12:00 UTC is 08:00 EDT.
    assert is_due(policy, utc("2026-08-12T12:00:00")).due is True
    # Winter: 12:00 UTC is only 07:00 EST, an hour early.
    assert is_due(policy, utc("2026-01-14T12:00:00")).reason == "before_send_hour"
    # Winter: 13:00 UTC is 08:00 EST.
    assert is_due(policy, utc("2026-01-14T13:00:00")).due is True


def test_the_local_day_is_the_users_day_not_the_servers() -> None:
    """Sent 22:00 local Tokyo is 13:00 UTC the same day, still "today" in Tokyo."""
    policy = daily(
        timezone="Asia/Tokyo",
        send_hour_local=9,
        last_sent_at=utc("2026-08-12T00:30:00"),  # 09:30 JST on the 12th
    )

    # 14:00 UTC on the 12th is 23:00 JST on the 12th. Same local day, no resend.
    assert is_due(policy, utc("2026-08-12T14:00:00")).reason == "already_sent_today"
    # 16:00 UTC on the 12th is 01:00 JST on the 13th, a new local day, but before
    # the 09:00 send hour.
    assert is_due(policy, utc("2026-08-12T16:00:00")).reason == "before_send_hour"
    # 01:00 UTC on the 13th is 10:00 JST on the 13th.
    assert is_due(policy, utc("2026-08-13T01:00:00")).due is True


def test_an_unusable_timezone_falls_back_to_utc_instead_of_raising() -> None:
    """A bad zone string should cost an hour, not somebody's alerts."""
    assert resolve_zone("Mars/Olympus_Mons").key == "UTC"
    assert resolve_zone(None).key == "UTC"
    assert resolve_zone("").key == "UTC"

    policy = daily(timezone="Mars/Olympus_Mons")
    assert is_due(policy, utc("2026-08-12T08:00:00")).due is True


# ---- weekly -----------------------------------------------------------------


def test_a_weekly_alert_only_fires_on_its_chosen_weekday() -> None:
    # 2026-08-12 is a Wednesday, weekday() == 2.
    assert utc("2026-08-12T08:00:00").weekday() == 2
    policy = SchedulePolicy(cadence=AlertCadence.WEEKLY, send_hour_local=8, send_weekday=2)

    assert is_due(policy, utc("2026-08-12T08:00:00")).due is True
    assert is_due(policy, utc("2026-08-13T08:00:00")).reason == "wrong_weekday"
    assert is_due(policy, utc("2026-08-11T08:00:00")).reason == "wrong_weekday"


def test_a_weekly_alert_still_respects_its_send_hour() -> None:
    policy = SchedulePolicy(cadence=AlertCadence.WEEKLY, send_hour_local=8, send_weekday=2)

    assert is_due(policy, utc("2026-08-12T07:00:00")).reason == "before_send_hour"


def test_a_weekly_alert_does_not_fire_twice_on_its_day() -> None:
    policy = SchedulePolicy(
        cadence=AlertCadence.WEEKLY,
        send_hour_local=8,
        send_weekday=2,
        last_sent_at=utc("2026-08-12T08:00:00"),
    )

    assert is_due(policy, utc("2026-08-12T18:00:00")).reason == "already_sent_today"
    # Next week, same weekday, due again.
    assert is_due(policy, utc("2026-08-19T08:00:00")).due is True


# ---- immediate and quiet hours ----------------------------------------------


@pytest.mark.parametrize(
    ("hour", "expected"),
    [
        (21, False),
        (22, True),  # start is inclusive
        (23, True),
        (0, True),
        (6, True),
        (7, False),  # end is exclusive
        (8, False),
    ],
)
def test_quiet_hours_wrap_midnight_as_a_half_open_window(hour: int, expected: bool) -> None:
    local = datetime(2026, 8, 12, hour, tzinfo=UTC)

    assert in_quiet_hours(local, start_hour=22, end_hour=7) is expected


def test_equal_quiet_hour_bounds_mean_no_quiet_hours_not_total_silence() -> None:
    for hour in range(24):
        local = datetime(2026, 8, 12, hour, tzinfo=UTC)
        assert in_quiet_hours(local, start_hour=0, end_hour=0) is False


def test_an_immediate_alert_is_held_during_quiet_hours() -> None:
    policy = SchedulePolicy(cadence=AlertCadence.IMMEDIATE)

    assert is_due(policy, utc("2026-08-12T23:00:00")).reason == "quiet_hours"
    assert is_due(policy, utc("2026-08-12T12:00:00")).due is True


def test_an_immediate_alert_honours_a_floor_between_sends() -> None:
    """Without this a source that republishes a batch becomes a mail loop."""
    policy = SchedulePolicy(
        cadence=AlertCadence.IMMEDIATE, last_sent_at=utc("2026-08-12T12:00:00")
    )

    assert is_due(policy, utc("2026-08-12T12:30:00")).reason == "min_interval"
    assert is_due(policy, utc("2026-08-12T13:00:00")).due is True
    # And the floor is configurable, not baked in.
    assert (
        is_due(policy, utc("2026-08-12T12:30:00"), immediate_min_interval_minutes=15).due
        is True
    )


def test_quiet_hours_do_not_gate_a_daily_digest() -> None:
    """Someone who picks 23:00 must not silently receive nothing forever.

    Quiet hours exist to stop unscheduled mail arriving at night. A daily digest
    is scheduled, by the user, at an hour they typed.
    """
    policy = daily(send_hour_local=23, quiet_hours_start_local=22, quiet_hours_end_local=7)

    assert is_due(policy, utc("2026-08-12T23:00:00")).due is True


def test_a_naive_last_sent_at_is_read_as_utc_rather_than_shifting() -> None:
    policy = daily(last_sent_at=datetime(2026, 8, 12, 8, 0))

    assert is_due(policy, utc("2026-08-12T20:00:00")).reason == "already_sent_today"


def test_the_policy_can_be_lifted_off_anything_with_the_right_attributes() -> None:
    """`from_subscription` is duck-typed so the boundary logic needs no database."""

    class Row:
        cadence = AlertCadence.WEEKLY
        timezone = "Europe/Berlin"
        send_hour_local = 9
        send_weekday = 4
        quiet_hours_start_local = 21
        quiet_hours_end_local = 6
        last_sent_at = None
        active = True

    policy = SchedulePolicy.from_subscription(Row())

    assert policy.cadence is AlertCadence.WEEKLY
    assert policy.timezone == "Europe/Berlin"
    assert policy.send_weekday == 4
    assert policy.last_sent_at is None


def test_immediate_min_interval_is_measured_from_the_last_send_not_the_last_check() -> None:
    policy = SchedulePolicy(
        cadence=AlertCadence.IMMEDIATE,
        last_sent_at=utc("2026-08-12T12:00:00") - timedelta(minutes=59),
    )

    assert is_due(policy, utc("2026-08-12T12:00:00")).reason == "min_interval"
