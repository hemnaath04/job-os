"""Covers the real bug this fixes: Heroku's router logs on `/jobs/from-url`
showed `service=30000ms` (H12, "Request timeout") with `connect=0ms` on every
failing request, meaning the dyno was healthy and reachable instantly, it
simply never answered inside Heroku's 30s hard ceiling. `fetch_url_markdown`
(own worst case well past 20s once its retries are counted) followed by
`parse_jd` (own documented worst case around 62s across its one retry) can
together run past 30s with nothing unusual happening, so the endpoint needs
its own deadline that fires first and returns something a caller can act on,
instead of Heroku's opaque `<title>Application Error</title>` page.

These tests never wait out a real slow call. They monkeypatch
`jobs._FROM_URL_DEADLINE_SECONDS` down to a few milliseconds and make the
fetch/parse coroutine hang on `asyncio.sleep` for far longer than that,
mirroring the fake-timeout convention `test_jd_parse.py` already uses
(monkeypatch the module-level callable, record instead of really sleeping).
`asyncio.wait_for` cancels that sleep at the deadline, so the test itself
finishes in well under a second even though the simulated call "would have"
taken seconds.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest
from fastapi import HTTPException

from job_os.db.models import User
from job_os.integrations import firecrawl
from job_os.routers import jobs as jobs_router
from job_os.schemas.jobs import JobFromUrl
from job_os.services import jd_parse


async def _make_user(db_session: Any, clerk_id: str) -> User:
    user = User(clerk_id=clerk_id, email=f"{clerk_id}@example.com")
    db_session.add(user)
    await db_session.flush()
    return user


@pytest.mark.asyncio
async def test_deadline_exceeded_returns_honest_504_fast(
    monkeypatch: pytest.MonkeyPatch, db_session: Any
) -> None:
    user = await _make_user(db_session, "clerk_deadline_exceeded")

    # A tiny deadline in place of the real ~27s one, so the test does not
    # need a call that actually runs anywhere near that long.
    monkeypatch.setattr(jobs_router, "_FROM_URL_DEADLINE_SECONDS", 0.05)

    async def hangs(url: str) -> firecrawl.FetchedPage:
        # Far longer than the patched deadline. `asyncio.wait_for` cancels
        # this sleep once the deadline fires, so the test never actually
        # waits the full 5s out.
        await asyncio.sleep(5.0)
        raise AssertionError("should have been cancelled by the deadline")

    monkeypatch.setattr(firecrawl, "fetch_url_markdown", hangs)

    started = time.monotonic()
    with pytest.raises(HTTPException) as exc_info:
        await jobs_router.create_from_url(
            JobFromUrl(url="https://example.com/slow-job"),
            _user=user,
            session=db_session,
        )
    elapsed = time.monotonic() - started

    assert exc_info.value.status_code == 504
    detail = exc_info.value.detail.lower()
    assert "too long" in detail
    assert "paste the description" in detail
    # Bounded, fast test time: proves the cap actually cut the wait short
    # rather than this test happening to pass by coincidence.
    assert elapsed < 2.0


@pytest.mark.asyncio
async def test_normal_speed_request_is_unaffected_by_the_new_cap(
    monkeypatch: pytest.MonkeyPatch, db_session: Any
) -> None:
    """The happy path this endpoint has clearly been serving correctly most
    of the time (per the Heroku logs showing plenty of real 201s) must keep
    working -- the deadline exists to catch the requests that were already
    going to fail, not to bounce ones that finish fine today."""
    user = await _make_user(db_session, "clerk_normal_speed")

    page = firecrawl.FetchedPage(
        url="https://example.com/job",
        markdown="We are hiring a Backend Engineer.",
        raw="<html>We are hiring a Backend Engineer.</html>",
        title="Backend Engineer",
        company_hint="Example",
    )

    async def fast_fetch(url: str) -> firecrawl.FetchedPage:
        return page

    async def fast_parse(jd_text: str, *, title_hint: str | None = None) -> dict:
        return {
            "title": "Backend Engineer",
            "company": "Example Co",
            "parse_incomplete": False,
        }

    monkeypatch.setattr(firecrawl, "fetch_url_markdown", fast_fetch)
    monkeypatch.setattr(jd_parse, "parse_jd", fast_parse)

    job = await jobs_router.create_from_url(
        JobFromUrl(url="https://example.com/job"),
        _user=user,
        session=db_session,
    )

    assert job.title == "Backend Engineer"
    assert job.jd_parsed["parse_incomplete"] is False
    assert job.source == "url"


@pytest.mark.asyncio
async def test_honest_parse_incomplete_within_budget_is_not_treated_as_a_timeout(
    monkeypatch: pytest.MonkeyPatch, db_session: Any
) -> None:
    """`parse_jd` has its own honest degraded path: it tries within its own
    retry budget and comes back with `parse_incomplete: True` rather than
    failing outright (see jd_parse.py's `_incomplete`). That is a different,
    both-honest outcome from this new fail-fast timeout, and must still
    create the job (with the honest incomplete marker) rather than being
    swept into the new 504, even though both involve `parse_incomplete`-
    shaped uncertainty."""
    user = await _make_user(db_session, "clerk_incomplete_but_on_time")

    page = firecrawl.FetchedPage(
        url="https://example.com/job",
        markdown="Some JD text that failed to parse cleanly.",
        raw="<html></html>",
        title="Some Role",
        company_hint="Example",
    )

    async def fast_fetch(url: str) -> firecrawl.FetchedPage:
        return page

    async def degrades_immediately(jd_text: str, *, title_hint: str | None = None) -> dict:
        # Simulates jd_parse.py's own `_incomplete(title_hint)` return, as if
        # both of its internal retry attempts had already failed -- but fast,
        # well inside the router's deadline, which is the point: this is a
        # legitimate, on-time answer, not a run that ran out of time budget.
        return {"parse_incomplete": True, "title": title_hint}

    monkeypatch.setattr(firecrawl, "fetch_url_markdown", fast_fetch)
    monkeypatch.setattr(jd_parse, "parse_jd", degrades_immediately)

    job = await jobs_router.create_from_url(
        JobFromUrl(url="https://example.com/job"),
        _user=user,
        session=db_session,
    )

    # A real job, created and returned normally -- not the new HTTPException(504).
    assert job.jd_parsed["parse_incomplete"] is True
    assert job.title == "Some Role"
