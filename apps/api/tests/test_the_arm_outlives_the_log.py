"""An A/B arm recorded only in a log is an A/B arm you can lose.

The analyst effort is logged beside its timing, which is what makes a duration
attributable. Then Appwrite dropped one execution's logs entirely: three
consecutive runs, two kept 6593B and 3082B, the third returned zero bytes with
`logging: true`, a clean 200 and a real 263s duration. The run happened, the
timing is gone, and nothing in it says which arm it was.

Assigning that run by when the variable was flipped is the guesswork the label
exists to prevent, and it would have been wrong: run 1 was dispatched before the
flip, executed after it, and still recorded `gateway_default`.

So the arm also goes into `ats_report`, which is persisted on the resume version
row and survives whatever the log does.
"""
from __future__ import annotations

from job_os.services.tailor import _analyst_effort_label


def test_an_unset_effort_is_named_rather_than_left_null() -> None:
    """`None` in a report column reads as "not measured", which is a lie here."""
    assert _analyst_effort_label(None) == "gateway_default"


def test_an_explicit_arm_is_recorded_as_itself() -> None:
    assert _analyst_effort_label("medium") == "medium"


def test_unset_and_explicit_high_stay_distinguishable() -> None:
    """They may behave alike at the gateway. They are not the same experiment."""
    assert _analyst_effort_label(None) != _analyst_effort_label("high")
