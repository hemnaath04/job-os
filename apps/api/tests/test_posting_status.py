"""Telling a closed posting from a posting we merely could not read.

Four rows in one workspace are not job descriptions. Each arrived the same way:
a link was pasted, a card appeared, and the card never scored. Two of the pages
say outright that the job is gone, which is an answer the user can act on and
was being reported as a parse failure.

Every string below is taken from a page that actually returned it, and the
false-positive tests are the ones that matter: a rule that hides a real job is
worse than the junk it removes. Both of them are cases a first version of this
module got wrong, kept here so a future tightening cannot reintroduce them.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://job_os:job_os@localhost/job_os"
)

from job_os.services.posting_status import (  # noqa: E402
    classify,
    is_usable,
    reason_for,
)

# Verbatim, from the rows in production.
DISNEY = (
    "[Skip to main content] # Job Not Found We are sorry this job post no "
    "longer exists. Luckily, we have other jobs you might also be interested "
    "in. [Search jobs] Close # Watch Our Jobs An asterisk indicates a required "
    "field. Interested In Select a job category from the list of options."
)
ANTHROPIC = (
    "The job you are looking for is no longer open. [Anthropic Logo] # Open "
    "Roles Create a Job Alert Level-up your career by having opportunities at "
    "Anthropic sent directly to your inbox."
)
MY_GREENHOUSE = (
    "[Greenhouse logo] _Apply_ for what's next. Search smarter, apply faster "
    "and take control of your job search with MyGreenhouse. Enter your email "
    "address to continue. Send security code Looking for your organization's "
    "Greenhouse account? [Sign in] This site uses cookies"
)
TESLA = (
    "Powered and protected by ![Akamai](https://www.akamai.com/logo.svg) "
    "[Privacy](https://www.akamai.com/privacy)"
)

# The two real postings a first version of this flagged. Both are genuine, both
# are short, and neither uses the word "responsibilities".
SHORT_REAL_PASTE = (
    "Software Engineer Intern, ML Infrastructure (Summer 2027). Build and "
    "maintain training infrastructure. Python, Kubernetes, distributed "
    "systems. Experience with large-scale data pipelines preferred."
)
DATADOG_TERSE = (
    "Software Engineering Intern (Winter). Join a team building observability "
    "tooling. You will write Go and Python, ship to production, and work "
    "alongside senior engineers on the platform."
)


@pytest.mark.parametrize(
    ("page", "expected"),
    [
        (DISNEY, "expired"),
        (ANTHROPIC, "expired"),
        (MY_GREENHOUSE, "sign_in_required"),
        (TESLA, "blocked"),
    ],
)
def test_the_four_pages_that_were_never_job_descriptions(page: str, expected: str) -> None:
    assert classify(page) == expected
    assert not is_usable(classify(page))


@pytest.mark.parametrize("page", [SHORT_REAL_PASTE, DATADOG_TERSE])
def test_a_short_but_real_posting_is_left_alone(page: str) -> None:
    """The false positive worth protecting against.

    A first version used a 700-character floor. These are 369 and 400
    characters in production and were both flagged as empty, which would have
    hidden two jobs the user had actually saved. There is no length rule here
    now: a genuinely contentless page already parses to `parse_incomplete`,
    which is the honest report for it.
    """
    assert classify(page) == "ok"


def test_a_real_posting_may_mention_signing_in_to_apply() -> None:
    """The reason the wall cases need a second signal.

    "Sign in to apply" is on a great many live postings, so the phrase alone
    cannot mean the page is a wall.
    """
    page = (
        "Senior Backend Engineer. Responsibilities include owning our payments "
        "service. Requirements: five years of Python. Sign in to apply."
    )
    assert classify(page) == "ok"


def test_a_real_posting_may_be_served_through_cloudflare() -> None:
    """Same shape, other wall. A footer is not a block."""
    page = (
        "Platform Engineer. What you will do: run our Kubernetes fleet. "
        "Minimum qualifications: Go, Terraform. Performance and security by "
        "Cloudflare."
    )
    assert classify(page) == "ok"


def test_expiry_is_claimed_at_any_length() -> None:
    """Disney's arrives with 17KB of site furniture around it.

    A length gate on this one would have let the biggest of the four through,
    which is why expiry is checked before anything else and without one.
    """
    padded = DISNEY + " lorem ipsum " * 4000
    assert classify(padded) == "expired"


def test_a_closed_posting_reads_differently_from_an_unreadable_one() -> None:
    """The distinction the module exists for.

    "This posting has closed" and "we could not read this posting" send
    somebody to different places, and both used to arrive as the second.
    """
    assert "closed" in (reason_for("expired") or "")
    assert "sign-in" in (reason_for("sign_in_required") or "")
    assert reason_for("ok") is None


def test_the_title_is_read_too() -> None:
    """Some sites put the whole answer in the title and nothing in the body."""
    assert classify("", title="Custom Job Error - Job Not Found") == "expired"


@pytest.mark.parametrize("value", [None, "", "   "])
def test_nothing_at_all_is_not_claimed_as_closed(value: str | None) -> None:
    """An empty fetch is a fetch failure, not a statement about the job.

    Claiming "this posting has closed" from silence would be inventing the one
    thing this module is supposed to only ever report verbatim.
    """
    assert classify(value) == "ok"
