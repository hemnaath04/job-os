"""Filling in a job that arrived thin, from a description pasted by hand.

A URL import can land with almost nothing on it: a title scraped off the page
heading, no location, no work type, no salary, and an empty parse. The job is
real and worth tracking, so deleting and re-adding it is the wrong repair. The
right one is to give the parser the text it never got.

The rules here are deliberately conservative, because this runs against a job
row that already exists and that the person has been tracking:

- A field is only ever FILLED IN, never overwritten. If a value is already
  there, this leaves it, whether it came from an earlier import or from the
  person themselves.
- Title and company are not touched at all. They are how the job is recognised
  in the list, so quietly renaming a tracked job would be a worse surprise than
  leaving an ugly title in place.
- The parse only replaces the stored one when it actually learned something. A
  parse can come back empty (the gateway has returned a 200 with no usable
  content before), and overwriting real structure with that would destroy
  signal rather than add it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# (attribute on the job, key in the parse, label a person reads).
# Salary appears twice on purpose: two columns, one thing to a reader.
BACKFILL_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("location", "location", "Location"),
    ("remote", "remote", "Work type"),
    ("level", "level", "Job type"),
    ("function", "function", "Function"),
    ("salary_min", "salary_min", "Salary"),
    ("salary_max", "salary_max", "Salary"),
)

# The lists a parse fills. Any one of them carrying something is the test for
# "this parse learned something", rather than checking the dict is non-empty:
# an incomplete parse still carries its keys, just with nothing in them.
PARSE_LISTS: tuple[str, ...] = (
    "required_skills",
    "preferred_skills",
    "technologies",
    "responsibilities",
    "qualifications",
    "keywords",
)


@dataclass
class Enrichment:
    """What to write, and what to tell the person was written."""

    updates: dict[str, Any] = field(default_factory=dict)
    filled: list[str] = field(default_factory=list)
    parse_replaced: bool = False


def parse_has_signal(parsed: dict[str, Any] | None) -> bool:
    """Whether a parse carries anything worth storing."""
    if not parsed:
        return False
    return any(parsed.get(key) for key in PARSE_LISTS)


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    return isinstance(value, str) and not value.strip()


def plan_enrichment(job: Any, parsed: dict[str, Any] | None, jd_text: str) -> Enrichment:
    """Work out the writes a pasted description earns on an existing job.

    Returns the plan rather than applying it so the decision can be tested
    without a database, and so the route can report exactly what changed
    instead of the caller having to diff two versions of the row.
    """
    plan = Enrichment()

    # The description itself is the point of the paste and is always worth
    # storing, even when nothing could be parsed out of it: it is what the
    # tailor reads, and it is what a second attempt at parsing would read.
    plan.updates["jd_raw"] = jd_text
    plan.updates["jd_clean"] = jd_text

    parsed = parsed or {}

    filled_salary = False
    for attribute, key, label in BACKFILL_FIELDS:
        if not _is_blank(getattr(job, attribute, None)):
            continue
        value = parsed.get(key)
        if value in (None, "", []):
            continue
        plan.updates[attribute] = value
        if label not in plan.filled:
            plan.filled.append(label)
        if attribute in ("salary_min", "salary_max"):
            filled_salary = True

    # Currency only rides along with a salary this call actually supplied.
    # On its own it says nothing, and the column is never empty to begin with
    # (it defaults to USD), so there is no blank here to fill.
    currency = parsed.get("salary_currency")
    if filled_salary and currency:
        plan.updates["salary_currency"] = currency

    if parse_has_signal(parsed):
        plan.updates["jd_parsed"] = parsed
        plan.parse_replaced = True

    return plan


def apply_enrichment(job: Any, plan: Enrichment) -> None:
    """Write a plan onto a job row."""
    for attribute, value in plan.updates.items():
        setattr(job, attribute, value)
