"""How old a posting really is, said out loud.

The single most common complaint about the market leader is that it shows jobs as
posted an hour ago when they are weeks old. That happens because aggregators
surface whatever date the board currently reports, and boards reset that date
every time a recruiter republishes a listing. The displayed number is real; it is
just not the number the reader thinks it is.

This module refuses to make that trade. Two dates go in:

  posted_at      what the source claims. Never treated as fact. Boards report a
                 repost date here, and some report the crawl date.
  first_seen_at  the earliest time we have a record of this role, from our own
                 sent log or jobs table. Ours, so we know what it means.

and the label that comes out always says which one it used. When the source claims
a date materially newer than our own first sighting, the label leads with our date
and names the discrepancy, because that gap IS the signal: a role that was on the
market three weeks ago and is being advertised as new today is a repost, and
knowing that is worth more to an applicant than a small number.

Nothing here is a guess dressed as a measurement. Where the only date available
came from a crawl, the label says estimated.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

#: How much newer the source's date has to be than our first sighting before we
#: call it a repost. Three days, not zero: crawl order, timezone handling on the
#: board's side, and our own polling interval all move a date by hours, and
#: labelling that as a repost would cry wolf on almost every listing.
REPOST_THRESHOLD = timedelta(days=3)

#: Clock skew we tolerate before treating a future date as unusable. Boards do
#: publish dates a few hours ahead.
FUTURE_SKEW_ALLOWANCE = timedelta(hours=12)


@dataclass(frozen=True, slots=True)
class Freshness:
    #: One short line for the job row, e.g. "Posted about 3 hours ago (estimated)".
    headline: str
    #: The honesty note, when there is something to disclose. None when the two
    #: dates agree and there is nothing to add.
    caveat: str | None
    #: True whenever the age rests on a date we did not observe ourselves, which
    #: is every source-supplied date.
    is_estimated: bool
    is_repost: bool
    #: Age in hours against the date the headline actually used, for sorting.
    #: None when no usable date exists at all.
    age_hours: float | None

    @property
    def summary(self) -> str:
        """Headline and caveat as one sentence, for the plain text part."""
        return f"{self.headline} {self.caveat}" if self.caveat else self.headline


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def humanize_age(delta: timedelta) -> str:
    """A coarse, honest age. Deliberately vague at the top end.

    "about 2 months" rather than "63 days": at that range the extra digits imply
    a precision the underlying date does not have.
    """
    seconds = max(delta.total_seconds(), 0)
    minutes = seconds / 60
    if minutes < 60:
        return "less than an hour"
    hours = minutes / 60
    if hours < 24:
        count = int(hours)
        return "about an hour" if count <= 1 else f"about {count} hours"
    days = hours / 24
    if days < 14:
        count = int(days)
        return "1 day" if count <= 1 else f"{count} days"
    if days < 60:
        weeks = int(days / 7)
        return f"about {weeks} weeks"
    months = int(days / 30)
    return f"about {months} months"


def assess_freshness(
    *,
    posted_at: datetime | None,
    first_seen_at: datetime | None,
    now: datetime,
) -> Freshness:
    """Build the label. Pure, so the wording is pinned by tests."""
    now = _as_utc(now) or datetime.now(UTC)
    posted = _as_utc(posted_at)
    first_seen = _as_utc(first_seen_at)

    # A date in the future is not a date. Drop it rather than render a negative
    # age or clamp it to "just now", which would be the dishonest option.
    if posted is not None and posted - now > FUTURE_SKEW_ALLOWANCE:
        posted = None
        future_note = (
            "The source gave a posting date in the future, so we are not using it."
        )
    else:
        future_note = None

    if posted is None and first_seen is None:
        return Freshness(
            headline="Age unknown",
            caveat=future_note
            or "Neither the source nor our own records carry a date for this one.",
            is_estimated=True,
            is_repost=False,
            age_hours=None,
        )

    if posted is None:
        assert first_seen is not None  # narrowed by the branch above
        age = now - first_seen
        caveat = (
            "The source published no date, so this is when we first saw it, "
            "not when it went live."
        )
        return Freshness(
            headline=f"First seen by us {humanize_age(age)} ago (estimated)",
            caveat=f"{future_note} {caveat}" if future_note else caveat,
            is_estimated=True,
            is_repost=False,
            age_hours=age.total_seconds() / 3600,
        )

    if first_seen is not None and posted - first_seen > REPOST_THRESHOLD:
        # The interesting case. Lead with our date, which is the older and the
        # one we can stand behind, and say plainly what the source claims.
        age = now - first_seen
        return Freshness(
            headline=f"First seen by us {humanize_age(age)} ago",
            caveat=(
                f"The source now lists it as posted {humanize_age(now - posted)} ago. "
                "That is a repost date, not a new role."
            ),
            is_estimated=True,
            is_repost=True,
            age_hours=age.total_seconds() / 3600,
        )

    age = now - posted
    return Freshness(
        headline=f"Posted {humanize_age(age)} ago (estimated)",
        caveat="Dates come from the job board and we treat them as approximate.",
        is_estimated=True,
        is_repost=False,
        age_hours=age.total_seconds() / 3600,
    )
