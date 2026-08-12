"""Freshness labels, which are the one thing this feature promises not to fake.

The competitor's most common complaint is a job shown as posted an hour ago that
has been on the market for weeks, because the board reset its date on a repost.
These tests pin the wording, not just the booleans, since the wording is the
product: a `is_repost=True` that renders as "Posted about 1 hour ago" would pass a
looser test and still tell the reader the wrong thing.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from job_os.services.alert_freshness import (
    REPOST_THRESHOLD,
    assess_freshness,
    humanize_age,
)

NOW = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)


def ago(**kwargs: float) -> datetime:
    return NOW - timedelta(**kwargs)


def test_a_source_date_is_always_labelled_as_an_estimate() -> None:
    """Every board-supplied date is a claim, so no label may present one as fact."""
    result = assess_freshness(posted_at=ago(hours=3), first_seen_at=ago(hours=3), now=NOW)

    assert result.headline == "Posted about 3 hours ago (estimated)"
    assert result.is_estimated is True
    assert result.is_repost is False
    assert result.caveat is not None


def test_a_repost_leads_with_our_own_date_and_names_the_gap() -> None:
    """The case the whole module exists for.

    The board says an hour. We saw the same role three weeks ago. The reader gets
    our number first and is told plainly what the board is claiming.
    """
    result = assess_freshness(posted_at=ago(hours=1), first_seen_at=ago(days=21), now=NOW)

    assert result.is_repost is True
    assert result.headline == "First seen by us about 3 weeks ago"
    assert "repost date, not a new role" in result.caveat
    assert "about an hour ago" in result.caveat
    # Sorting has to agree with the words: three weeks, not one hour.
    assert result.age_hours == pytest.approx(21 * 24)


def test_a_small_disagreement_between_the_dates_is_not_called_a_repost() -> None:
    """Crawl lag and board-side timezones move a date by hours.

    Calling that a repost would put a warning on nearly every listing and teach
    the reader to ignore the warning that matters.
    """
    just_under = REPOST_THRESHOLD - timedelta(hours=1)
    result = assess_freshness(
        posted_at=NOW - timedelta(hours=2),
        first_seen_at=NOW - timedelta(hours=2) - just_under,
        now=NOW,
    )

    assert result.is_repost is False
    assert result.headline.startswith("Posted")


def test_the_repost_threshold_is_a_boundary_not_a_range() -> None:
    posted = NOW - timedelta(hours=1)
    just_over = assess_freshness(
        posted_at=posted,
        first_seen_at=posted - REPOST_THRESHOLD - timedelta(minutes=1),
        now=NOW,
    )
    exactly_at = assess_freshness(
        posted_at=posted, first_seen_at=posted - REPOST_THRESHOLD, now=NOW
    )

    assert just_over.is_repost is True
    assert exactly_at.is_repost is False


def test_no_source_date_says_first_seen_and_does_not_imply_a_posting_date() -> None:
    result = assess_freshness(posted_at=None, first_seen_at=ago(days=2), now=NOW)

    assert result.headline == "First seen by us 2 days ago (estimated)"
    assert "not when it went live" in result.caveat
    assert result.is_estimated is True


def test_no_dates_at_all_says_unknown_rather_than_guessing() -> None:
    result = assess_freshness(posted_at=None, first_seen_at=None, now=NOW)

    assert result.headline == "Age unknown"
    assert result.age_hours is None
    assert result.is_estimated is True


def test_a_date_in_the_future_is_discarded_rather_than_shown_as_brand_new() -> None:
    """Clamping a future date to "just now" would be the dishonest option."""
    result = assess_freshness(
        posted_at=NOW + timedelta(days=2), first_seen_at=ago(days=5), now=NOW
    )

    assert "future" in result.caveat
    assert result.headline == "First seen by us 5 days ago (estimated)"
    assert result.is_repost is False


def test_a_slightly_future_date_is_tolerated_as_clock_skew() -> None:
    """Boards do publish a few hours ahead. That is skew, not a bad date."""
    result = assess_freshness(
        posted_at=NOW + timedelta(hours=2), first_seen_at=None, now=NOW
    )

    assert "future" not in (result.caveat or "")
    assert result.headline.startswith("Posted")


def test_a_naive_datetime_is_read_as_utc() -> None:
    aware = assess_freshness(posted_at=ago(hours=5), first_seen_at=None, now=NOW)
    naive = assess_freshness(
        posted_at=datetime(2026, 8, 12, 7, 0), first_seen_at=None, now=NOW
    )

    assert naive.headline == aware.headline


@pytest.mark.parametrize(
    ("delta", "expected"),
    [
        (timedelta(minutes=5), "less than an hour"),
        (timedelta(minutes=59), "less than an hour"),
        (timedelta(hours=1), "about an hour"),
        (timedelta(hours=3), "about 3 hours"),
        (timedelta(hours=23), "about 23 hours"),
        (timedelta(days=1), "1 day"),
        (timedelta(days=6), "6 days"),
        (timedelta(days=13), "13 days"),
        # Past a fortnight the underlying date is not precise enough to justify
        # a day count, so the wording gets deliberately coarser.
        (timedelta(days=21), "about 3 weeks"),
        (timedelta(days=59), "about 8 weeks"),
        (timedelta(days=90), "about 3 months"),
        (timedelta(seconds=-30), "less than an hour"),
    ],
)
def test_ages_are_stated_as_coarsely_as_the_underlying_date_deserves(
    delta: timedelta, expected: str
) -> None:
    assert humanize_age(delta) == expected


def test_the_summary_joins_the_headline_and_the_caveat_for_plain_text() -> None:
    result = assess_freshness(posted_at=ago(hours=1), first_seen_at=ago(days=30), now=NOW)

    assert result.summary.startswith(result.headline)
    assert result.caveat in result.summary


def test_no_freshness_copy_contains_an_em_dash() -> None:
    cases = [
        assess_freshness(posted_at=ago(hours=1), first_seen_at=ago(days=30), now=NOW),
        assess_freshness(posted_at=None, first_seen_at=ago(days=2), now=NOW),
        assess_freshness(posted_at=None, first_seen_at=None, now=NOW),
        assess_freshness(posted_at=NOW + timedelta(days=9), first_seen_at=None, now=NOW),
    ]

    for result in cases:
        assert "—" not in result.summary
