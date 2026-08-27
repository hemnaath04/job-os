"""A BedRocked bullet reached the page reading "a 0, 100 dig-readiness score".

The fact says "a 0-100 dig-readiness score" with an EN DASH, and the rule that
replaces dashes with real punctuation replaced it. The docstring on that rule
already named "0-100" as safe, but its example is a HYPHEN, so the case it
believed it handled was never the case that occurs.

A visible error in a number, produced by the rule that exists to tidy
punctuation.
"""
from __future__ import annotations

from job_os.services.resume_writing import normalize_dashes


def test_a_numeric_range_keeps_its_dash() -> None:
    """His BedRocked bullet, verbatim, with the en dash it is actually stored with."""
    assert normalize_dashes("Built a 0–100 dig-readiness score") == (
        "Built a 0-100 dig-readiness score"
    )


def test_an_em_dash_in_prose_is_still_replaced() -> None:
    """The rule still does its job; it just stops doing it to numbers."""
    assert normalize_dashes("BedRocked — Civic Sewer Platform") == (
        "BedRocked, Civic Sewer Platform"
    )


def test_a_range_written_with_a_hyphen_is_untouched_as_before() -> None:
    assert normalize_dashes("a 0-100 score") == "a 0-100 score"


def test_a_dash_between_a_number_and_a_word_is_still_punctuation() -> None:
    """Only digit-dash-digit is a range."""
    assert normalize_dashes("2,404 segments — shipped in a day") == (
        "2,404 segments, shipped in a day"
    )
