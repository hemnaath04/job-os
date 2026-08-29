"""Importing a job without waiting for the model.

Replaces test_from_url_deadline.py, whose whole subject was a synchronous
deadline that no longer exists. That file tuned how gracefully a fetch-and-parse
could fail inside Heroku's 30s router ceiling; the answer turned out to be that
it cannot fit at all, so the work moved off the request and these are the
properties that replace it.

The incident behind it: 2026-08-27, a Greenhouse import 504'd after 28.3s, the
person was sent to the paste tab as the documented fallback, and that spent 27s
reaching an empty parse and saved "Untitled". Neither path could produce a
structured job, which is what makes this a shape change rather than tuning.
"""
from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import pytest

from job_os.db.models import User
from job_os.integrations.firecrawl import FetchedPage
from job_os.routers import jobs as jobs_router
from job_os.schemas.jobs import JobFromText, JobFromUrl
from job_os.services import jd_ingest

PAGE = FetchedPage(
    url="https://job-boards.greenhouse.io/glossgenius/jobs/1",
    markdown="Senior Backend Engineer at GlossGenius. Python, Postgres.",
    raw="<html>Senior Backend Engineer</html>",
    title="Senior Backend Engineer",
    company_hint="GlossGenius",
)

PARSED: dict[str, Any] = {
    "title": "Senior Backend Engineer",
    "company": "GlossGenius",
    "location": "New York, NY",
    "required_skills": ["python", "postgres"],
    "keywords": ["backend"],
    "parse_incomplete": False,
}


async def _user(db_session, suffix: str) -> User:
    user = User(clerk_id=f"clerk_{suffix}", email=f"{suffix}@example.com")
    db_session.add(user)
    await db_session.flush()
    return user


@pytest.fixture
def no_scheduling(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Capture what the route would have started, instead of starting it.

    These tests drive complete_job_parse themselves. Letting the route fire a
    real asyncio task would leave one running against a session the test is
    about to roll back.
    """
    started: list[Any] = []

    # Takes the owner too now: the deferred parse writes the board card, and
    # the card table is multi-tenant, so it has to know whose board.
    def _record(job_id: Any, owner_id: Any = None, **_kwargs: Any) -> None:
        started.append(job_id)

    monkeypatch.setattr(jd_ingest, "schedule_job_parse", _record)
    return started


@pytest.mark.asyncio
async def test_a_url_import_answers_before_anything_is_fetched(
    monkeypatch: pytest.MonkeyPatch, db_session, no_scheduling
) -> None:
    """The property the 504 was about. No fetch, no parse, no waiting."""
    user = await _user(db_session, "url_fast")

    async def never_called(url: str) -> FetchedPage:
        raise AssertionError("the request path must not fetch")

    async def never_parsed(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("the request path must not parse")

    monkeypatch.setattr("job_os.integrations.firecrawl.fetch_url_markdown", never_called)
    monkeypatch.setattr("job_os.services.jd_parse.parse_jd", never_parsed)

    job = await jobs_router.create_from_url(
        JobFromUrl(url="https://job-boards.greenhouse.io/glossgenius/jobs/1"),
        _user=user,
        session=db_session,
    )

    assert job.jd_parsed == {"parse_pending": True}
    assert no_scheduling == [job.id], "the deferred parse was never queued"


@pytest.mark.asyncio
async def test_a_text_import_answers_before_parsing(
    monkeypatch: pytest.MonkeyPatch, db_session, no_scheduling
) -> None:
    user = await _user(db_session, "text_fast")

    async def never_parsed(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("the request path must not parse")

    monkeypatch.setattr("job_os.services.jd_parse.parse_jd", never_parsed)

    job = await jobs_router.create_from_text(
        JobFromText(jd_text="Senior Backend Engineer at GlossGenius.", company_hint="GlossGenius"),
        _user=user,
        session=db_session,
    )

    assert job.jd_parsed == {"parse_pending": True}
    # The text is the one thing a paste already has, so it is stored now
    # rather than waited for: it is what the tailor reads and what a second
    # parse would read.
    assert job.jd_clean == "Senior Backend Engineer at GlossGenius."
    assert no_scheduling == [job.id]


@pytest.mark.asyncio
async def test_the_deferred_parse_fills_the_row_in(
    monkeypatch: pytest.MonkeyPatch, db_session, background_session, no_scheduling
) -> None:
    user = await _user(db_session, "url_fills")

    async def fetch(url: str) -> FetchedPage:
        return PAGE

    async def parse(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return dict(PARSED)

    monkeypatch.setattr("job_os.integrations.firecrawl.fetch_url_markdown", fetch)
    monkeypatch.setattr("job_os.services.jd_parse.parse_jd", parse)

    job = await jobs_router.create_from_url(
        JobFromUrl(url="https://job-boards.greenhouse.io/glossgenius/jobs/1"),
        _user=user,
        session=db_session,
    )
    await jd_ingest.complete_job_parse(job.id)
    await db_session.refresh(job)

    assert job.title == "Senior Backend Engineer"
    assert job.location == "New York, NY"
    assert job.jd_clean == PAGE.markdown
    assert job.jd_parsed["required_skills"] == ["python", "postgres"]
    assert job.jd_parsed.get("parse_pending") in (None, False)


@pytest.mark.asyncio
async def test_a_parse_that_gives_up_is_recorded_as_incomplete_not_pending(
    monkeypatch: pytest.MonkeyPatch, db_session, background_session, no_scheduling
) -> None:
    """A row left at parse_pending forever is the one unreadable outcome.

    parse_jd reports its own failure honestly rather than raising, and that
    has to reach the row, or the interface goes on claiming an answer is
    still coming and the scorer has nothing to distinguish "could not read
    it" from "asks for nothing".
    """
    user = await _user(db_session, "gives_up")

    async def fetch(url: str) -> FetchedPage:
        return PAGE

    async def gave_up(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"parse_incomplete": True, "title": "Senior Backend Engineer"}

    monkeypatch.setattr("job_os.integrations.firecrawl.fetch_url_markdown", fetch)
    monkeypatch.setattr("job_os.services.jd_parse.parse_jd", gave_up)

    job = await jobs_router.create_from_url(
        JobFromUrl(url="https://job-boards.greenhouse.io/glossgenius/jobs/1"),
        _user=user,
        session=db_session,
    )
    await jd_ingest.complete_job_parse(job.id)
    await db_session.refresh(job)

    assert job.jd_parsed["parse_incomplete"] is True
    assert job.jd_parsed.get("parse_pending") in (None, False)
    # Still worth what it did learn.
    assert job.title == "Senior Backend Engineer"


@pytest.mark.asyncio
async def test_a_deferred_parse_gets_a_budget_the_router_no_longer_bounds(
    monkeypatch: pytest.MonkeyPatch, db_session, background_session, no_scheduling
) -> None:
    """Off the request path, the 25s budget that made a slow gateway fatal
    is no longer the constraint. If this reverts to the old budget the fix is
    cosmetic: the parse would still be racing a clock nothing is watching."""
    user = await _user(db_session, "budget")
    seen: list[float | None] = []

    async def fetch(url: str) -> FetchedPage:
        return PAGE

    async def parse(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        seen.append(kwargs.get("deadline_seconds"))
        return dict(PARSED)

    monkeypatch.setattr("job_os.integrations.firecrawl.fetch_url_markdown", fetch)
    monkeypatch.setattr("job_os.services.jd_parse.parse_jd", parse)

    job = await jobs_router.create_from_url(
        JobFromUrl(url="https://job-boards.greenhouse.io/glossgenius/jobs/1"),
        _user=user,
        session=db_session,
    )
    await jd_ingest.complete_job_parse(job.id)

    assert seen == [jd_ingest.BACKGROUND_PARSE_SECONDS]
    assert jd_ingest.BACKGROUND_PARSE_SECONDS > 30.0


@pytest.mark.asyncio
async def test_a_job_deleted_before_its_parse_lands_is_not_an_error(
    monkeypatch: pytest.MonkeyPatch, db_session, background_session
) -> None:
    import uuid

    # Nothing to fill, and nothing to raise about it either: the task is
    # nobody's to await, so an exception here would vanish into the loop.
    await jd_ingest.complete_job_parse(uuid.uuid4())


@pytest.mark.asyncio
async def test_reparse_requeues_a_stuck_row(
    monkeypatch: pytest.MonkeyPatch, db_session, no_scheduling
) -> None:
    """The deferred parse runs in this process, so a dyno restart mid-parse
    strands a row at pending. Re-importing would split the application and
    documents already attached to it away from the row they belong to, so the
    recovery has to work on the same job."""
    user = await _user(db_session, "requeue")

    job = await jobs_router.create_from_text(
        JobFromText(jd_text="Senior Backend Engineer at GlossGenius.", company_hint="GlossGenius"),
        _user=user,
        session=db_session,
    )
    job.jd_parsed = {"parse_incomplete": True, "parse_error": "fetch_failed"}
    await db_session.flush()
    no_scheduling.clear()

    again = await jobs_router.reparse_job(job.id, _user=user, session=db_session)

    assert again.id == job.id
    assert again.jd_parsed == {"parse_pending": True}
    assert no_scheduling == [job.id]


@pytest.mark.asyncio
async def test_reparse_returns_a_job_that_can_actually_be_serialised(
    monkeypatch: pytest.MonkeyPatch, db_session, no_scheduling
) -> None:
    """The same 500 that shipped on the paste route, on the retry button.

    `reparse_job` UPDATEs where its neighbours INSERT, and `updated_at` carries
    onupdate=func.now(), so the flush leaves it expired -- SQLAlchemy fetches
    server-generated columns back on an INSERT but not on an UPDATE. The old
    `refresh(attribute_names=["company"])` loaded the relationship and left
    `updated_at` alone, so `response_model=JobRead` was one lazy load away from
    MissingGreenlet inside an async request. The test above cannot see it: it
    reads `id` and `jd_parsed`, and neither is the expired attribute. Validating
    the response model is the assertion, because that is what FastAPI does.
    """
    from job_os.schemas.jobs import JobRead

    user = await _user(db_session, "reparse-serialise")

    job = await jobs_router.create_from_text(
        JobFromText(jd_text="Platform Engineer at Ramp.", company_hint="Ramp"),
        _user=user,
        session=db_session,
    )
    job.jd_parsed = {"parse_incomplete": True, "parse_error": "fetch_failed"}
    await db_session.flush()
    no_scheduling.clear()

    again = await jobs_router.reparse_job(job.id, _user=user, session=db_session)

    serialised = JobRead.model_validate(again)
    assert serialised.updated_at is not None
    assert serialised.created_at is not None
    assert serialised.company is not None
    assert serialised.title == job.title


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://job-boards.greenhouse.io/glossgenius/jobs/7978666003?gh_src=x", "Glossgenius"),
        ("https://boards.greenhouse.io/stripe/jobs/1", "Stripe"),
        ("https://jobs.lever.co/attentive/abc-123", "Attentive"),
        ("https://jobs.ashbyhq.com/openai/xyz", "Openai"),
        ("https://apply.workable.com/some-co/j/ABC/", "Some Co"),
        ("https://careers.stripe.com/jobs/1", "Stripe"),
        # Vendor-hosted: the registrable domain is the recruiting vendor, and
        # the employer is the leftmost label. Reading the wrong half put
        # "Myworkdayjobs" and "Oraclecloud" on a real board as company names.
        (
            "https://workiva.wd503.myworkdayjobs.com/careers/job/Summer-2027-Intern_R1",
            "Workiva",
        ),
        ("https://acme.wd1.myworkdayjobs.com/en-US/careers/job/X", "Acme"),
        ("https://www.example.com/careers/1", "Example"),
        # Nothing usable rather than something wrong.
        ("not a url", None),
        ("https://localhost/jobs/1", None),
    ],
)
def test_the_url_alone_names_the_company(url: str, expected: str | None) -> None:
    """Costs no network and no model call, and is the difference between a
    pending card reading "GlossGenius" and one reading "Unknown", which looks
    broken rather than busy. Only ever a guess: the parse overwrites it the
    moment it knows better, and never replaces it with a blank."""
    assert jd_ingest.company_hint_from_url(url) == expected


@pytest.mark.asyncio
async def test_a_parse_stranded_by_a_restart_is_requeued_at_startup(
    monkeypatch: pytest.MonkeyPatch, db_session, background_session, no_scheduling
) -> None:
    """Heroku restarts dynos daily, so this is routine, not an edge case.

    The deferred parse lives in the web process. Whatever was mid-parse when
    the dyno went down has nothing coming for it, and "the match appears once
    it lands" becomes a lie for those rows. The pending marker is already in
    the row, so the row is the queue and startup is what drains it.
    """
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import update

    from job_os.db.models import Job

    user = await _user(db_session, "stranded")
    job = await jobs_router.create_from_text(
        JobFromText(jd_text="Senior Backend Engineer at GlossGenius.", company_hint="GlossGenius"),
        _user=user,
        session=db_session,
    )
    # Older than any live task could be. updated_at is set by the DB, so it
    # has to be pushed back explicitly rather than waited for.
    await db_session.execute(
        update(Job)
        .where(Job.id == job.id)
        .values(
            updated_at=datetime.now(UTC)
            - timedelta(seconds=jd_ingest.STRANDED_AFTER_SECONDS + 60)
        )
    )
    no_scheduling.clear()

    requeued = await jd_ingest.requeue_stranded_parses()

    assert requeued >= 1
    assert job.id in no_scheduling


@pytest.mark.asyncio
async def test_a_parse_that_is_merely_slow_is_left_alone(
    monkeypatch: pytest.MonkeyPatch, db_session, background_session, no_scheduling
) -> None:
    """Requeueing a live parse would run two against the same row."""
    user = await _user(db_session, "still_running")
    job = await jobs_router.create_from_text(
        JobFromText(jd_text="Senior Backend Engineer at GlossGenius.", company_hint="GlossGenius"),
        _user=user,
        session=db_session,
    )
    no_scheduling.clear()

    await jd_ingest.requeue_stranded_parses()

    assert job.id not in no_scheduling


@pytest.mark.asyncio
async def test_a_finished_job_is_never_requeued(
    monkeypatch: pytest.MonkeyPatch, db_session, background_session, no_scheduling
) -> None:
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import update

    from job_os.db.models import Job

    user = await _user(db_session, "finished")
    job = await jobs_router.create_from_text(
        JobFromText(jd_text="Senior Backend Engineer at GlossGenius.", company_hint="GlossGenius"),
        _user=user,
        session=db_session,
    )
    await db_session.execute(
        update(Job)
        .where(Job.id == job.id)
        .values(
            jd_parsed=dict(PARSED),
            updated_at=datetime.now(UTC)
            - timedelta(seconds=jd_ingest.STRANDED_AFTER_SECONDS + 60),
        )
    )
    no_scheduling.clear()

    await jd_ingest.requeue_stranded_parses()

    assert job.id not in no_scheduling


@pytest.mark.asyncio
async def test_the_sweep_recovers_a_row_without_waiting_for_a_restart(
    monkeypatch: pytest.MonkeyPatch, db_session, background_session, no_scheduling
) -> None:
    """The gap behind a posting that appears to take ten minutes to parse.

    Recovery used to happen only at startup, so a row was rescued only if a
    restart came along AND the row was already past the cutoff at that moment.
    A parse stranded shortly before a restart is younger than the cutoff, gets
    skipped by the restart that could have saved it, and waits for the next one.
    On a dyno that cycles daily that is a card reading "Still reading this
    posting" for hours, over a parse that takes about ten seconds.

    The sweep is driven here by shortening its interval rather than by waiting
    on the real one.
    """
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import update

    from job_os.db.models import Job

    user = await _user(db_session, "swept")
    job = await jobs_router.create_from_text(
        JobFromText(jd_text="Senior Backend Engineer at GlossGenius.", company_hint="GlossGenius"),
        _user=user,
        session=db_session,
    )
    await db_session.execute(
        update(Job)
        .where(Job.id == job.id)
        .values(
            updated_at=datetime.now(UTC)
            - timedelta(seconds=jd_ingest.STRANDED_AFTER_SECONDS + 60)
        )
    )
    no_scheduling.clear()
    monkeypatch.setattr(jd_ingest, "SWEEP_INTERVAL_SECONDS", 0.01)

    sweep = asyncio.create_task(jd_ingest.sweep_stranded_parses_forever())
    try:
        for _ in range(200):
            await asyncio.sleep(0.01)
            if job.id in no_scheduling:
                break
    finally:
        sweep.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sweep

    assert job.id in no_scheduling, "no restart happened, and it was still picked up"


@pytest.mark.asyncio
async def test_the_sweep_survives_a_failing_scan(
    monkeypatch: pytest.MonkeyPatch, db_session, background_session, no_scheduling
) -> None:
    """One bad scan costs an interval, not the loop.

    An exception escaping the sweep would take it down for the life of the
    process and put the behaviour back to startup-only, silently.
    """
    calls = 0

    async def _explode() -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("database went away")
        return 0

    monkeypatch.setattr(jd_ingest, "requeue_stranded_parses", _explode)
    monkeypatch.setattr(jd_ingest, "SWEEP_INTERVAL_SECONDS", 0.01)

    sweep = asyncio.create_task(jd_ingest.sweep_stranded_parses_forever())
    try:
        for _ in range(200):
            await asyncio.sleep(0.01)
            if calls >= 3:
                break
    finally:
        sweep.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sweep

    assert calls >= 3, "the loop kept running after the first scan raised"


@pytest.mark.asyncio
async def test_a_job_already_being_parsed_here_is_not_scheduled_twice(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """The sweep's age filter is a clock and cannot see a slow live task.

    Without this guard a parse that simply takes longer than the cutoff gets a
    second task on the same row, which spends two gateway calls to write one
    result. Reachable now that the sweep runs on a timer rather than once.
    """
    from uuid import uuid4

    started = asyncio.Event()
    release = asyncio.Event()
    runs = 0

    async def _slow(job_id, owner_id=None, **_kwargs):  # type: ignore[no-untyped-def]
        nonlocal runs
        runs += 1
        started.set()
        await release.wait()

    monkeypatch.setattr(jd_ingest, "complete_job_parse", _slow)
    job_id = uuid4()

    jd_ingest.schedule_job_parse(job_id)
    await started.wait()
    jd_ingest.schedule_job_parse(job_id)  # the sweep, arriving mid-parse

    assert runs == 1
    release.set()
    await asyncio.sleep(0)
    # And once it finishes, the id is free again rather than blocked forever.
    for _ in range(50):
        await asyncio.sleep(0.01)
        if job_id not in jd_ingest._INFLIGHT:
            break
    assert job_id not in jd_ingest._INFLIGHT


@pytest.mark.asyncio
async def test_a_closed_posting_is_not_parsed_and_not_left_active(
    monkeypatch: pytest.MonkeyPatch, db_session, background_session, no_scheduling
) -> None:
    """The whole point of recognising it: stop, and say so.

    Asking the extractor to find requirements in "Job Not Found" spends a model
    call to arrive at the nothing that was already on the page. And a card that
    can never score should not sit on the board looking live: `Job.active` is
    what the jobs list filters on.
    """
    from job_os.integrations import firecrawl

    user = await _user(db_session, "closed_posting")
    job = await jobs_router.create_from_url(
        JobFromUrl(url="https://www.disneycareers.com/en/job/lake-buena-vista/"),
        _user=user,
        session=db_session,
    )

    parsed_called = False

    async def _must_not_parse(*_args: Any, **_kwargs: Any) -> dict:
        nonlocal parsed_called
        parsed_called = True
        return {}

    async def _fetch(_url: str) -> firecrawl.FetchedPage:
        return firecrawl.FetchedPage(
            url=_url,
            markdown="# Job Not Found We are sorry this job post no longer exists.",
            raw="<html></html>",
            title="Custom Job Error - Disney Careers",
            company_hint="Disneycareers",
        )

    monkeypatch.setattr(jd_ingest, "fetch_url_markdown", _fetch, raising=False)
    monkeypatch.setattr("job_os.integrations.firecrawl.fetch_url_markdown", _fetch)
    monkeypatch.setattr("job_os.services.jd_parse.parse_jd", _must_not_parse)

    await jd_ingest.complete_job_parse(job.id)

    await db_session.refresh(job)
    assert parsed_called is False, "no model call is spent on an error page"
    assert job.jd_parsed.get("posting_status") == "expired"
    assert job.jd_parsed.get("posting_status_reason")
    assert job.active is False, "a closed posting is off the board"


@pytest.mark.asyncio
async def test_a_wall_is_reported_without_closing_the_job(
    monkeypatch: pytest.MonkeyPatch, db_session, background_session, no_scheduling
) -> None:
    """A sign-in wall says nothing about whether the job is open.

    Only `expired` deactivates. Reading "we could not fetch this" as "this job
    is gone" would quietly remove postings that are still live.
    """
    from job_os.integrations import firecrawl

    user = await _user(db_session, "wall_posting")
    job = await jobs_router.create_from_url(
        JobFromUrl(url="https://my.greenhouse.io/applications/59836123"),
        _user=user,
        session=db_session,
    )

    async def _fetch(_url: str) -> firecrawl.FetchedPage:
        return firecrawl.FetchedPage(
            url=_url,
            markdown="Enter your email address to continue. Send security code",
            raw="<html></html>",
            title="MyGreenhouse",
            company_hint="Greenhouse",
        )

    monkeypatch.setattr("job_os.integrations.firecrawl.fetch_url_markdown", _fetch)

    await jd_ingest.complete_job_parse(job.id)

    await db_session.refresh(job)
    assert job.jd_parsed.get("posting_status") == "sign_in_required"
    assert job.active is True, "unreadable is not the same as closed"


@pytest.mark.asyncio
async def test_the_slug_guess_is_discarded_once_the_real_company_is_known(
    monkeypatch: pytest.MonkeyPatch, db_session, background_session, no_scheduling
) -> None:
    """Six strays of this shape were sitting in production.

    The import writes a company guessed from the link slug so the card has a
    legible name while the parse runs. The parse then learns the employer's
    real name and repoints the job, and nothing removed the guess. Because the
    guess is usually a DIFFERENT name, the duplicate merge never saw it:
    "Career Schwab" beside "Charles Schwab Corporation", and worse,
    "Myworkdayjobs" and "Oraclecloud", which are the ATS vendor's hostname.
    """
    from sqlalchemy import select

    from job_os.db.models import Company
    from job_os.integrations import firecrawl

    user = await _user(db_session, "slug_guess")
    job = await jobs_router.create_from_url(
        JobFromUrl(url="https://career-schwab.icims.com/jobs/126228/job"),
        _user=user,
        session=db_session,
    )
    guessed_id = job.company_id
    guessed = await db_session.get(Company, guessed_id)
    assert guessed is not None and guessed.domain is None

    async def _fetch(_url: str) -> firecrawl.FetchedPage:
        return firecrawl.FetchedPage(
            url=_url,
            markdown=(
                "Technology 2027 Intern. Responsibilities include building "
                "internal tooling. Requirements: Python and SQL."
            ),
            raw="<html></html>",
            title="Technology 2027 Intern",
            company_hint="Career Schwab",
        )

    async def _parsed(*_args: Any, **_kwargs: Any) -> dict:
        return {
            "title": "Technology 2027 Intern",
            "company": "Charles Schwab Corporation",
            "company_domain": "schwabjobs.com",
            "technologies": ["Python", "SQL"],
            "parse_incomplete": False,
        }

    monkeypatch.setattr("job_os.integrations.firecrawl.fetch_url_markdown", _fetch)
    monkeypatch.setattr("job_os.services.jd_parse.parse_jd", _parsed)

    await jd_ingest.complete_job_parse(job.id)
    await db_session.refresh(job)

    real = await db_session.get(Company, job.company_id)
    assert real is not None and real.name == "Charles Schwab Corporation"
    assert await db_session.get(Company, guessed_id) is None, "the guess did not survive"
    # And nothing else was collateral.
    remaining = await db_session.execute(
        select(Company).where(Company.name == "Charles Schwab Corporation")
    )
    assert len(remaining.scalars().all()) == 1


@pytest.mark.asyncio
async def test_a_guess_other_jobs_still_use_is_left_alone(
    monkeypatch: pytest.MonkeyPatch, db_session, background_session, no_scheduling
) -> None:
    """A guessed name can be right, and a right one is shared.

    Checked rather than assumed: deleting a company a second job points at
    would break that job's card to tidy this one's.
    """
    from job_os.db.models import Company, Job
    from job_os.integrations import firecrawl

    user = await _user(db_session, "shared_guess")
    job = await jobs_router.create_from_url(
        JobFromUrl(url="https://boards.greenhouse.io/stripe/jobs/1"),
        _user=user,
        session=db_session,
    )
    guessed_id = job.company_id
    db_session.add(
        Job(
            company_id=guessed_id,
            title="Another role at the same guess",
            jd_raw="",
            jd_clean="",
            jd_parsed={},
            source="text",
            active=True,
        )
    )
    await db_session.flush()

    async def _fetch(_url: str) -> firecrawl.FetchedPage:
        return firecrawl.FetchedPage(
            url=_url,
            markdown="Engineer. Responsibilities: build. Requirements: Go.",
            raw="<html></html>",
            title="Engineer",
            company_hint="Stripe",
        )

    async def _parsed(*_args: Any, **_kwargs: Any) -> dict:
        return {"company": "Stripe Payments", "company_domain": "stripe.com",
                "technologies": ["Go"], "parse_incomplete": False}

    monkeypatch.setattr("job_os.integrations.firecrawl.fetch_url_markdown", _fetch)
    monkeypatch.setattr("job_os.services.jd_parse.parse_jd", _parsed)

    await jd_ingest.complete_job_parse(job.id)

    assert await db_session.get(Company, guessed_id) is not None


@pytest.mark.asyncio
async def test_an_explicit_reread_replaces_a_title_the_last_parse_got_wrong(
    monkeypatch: pytest.MonkeyPatch, db_session, background_session, no_scheduling
) -> None:
    """The reason "read it again" needed more than a button.

    The deferred parse fills only what is still blank, which is right for a
    first read of a row carrying a URL-slug guess. It is wrong for a re-read:
    a job that came back "Custom Job Error - Disney Careers" kept that title
    forever, because the title was no longer blank, so pressing the button did
    nothing a user could see.

    Nothing in this app lets a job's title, level, location or salary be
    edited by hand -- there is no PATCH route for a job -- so what
    `replace_fields` overwrites is only ever a previous parse.
    """
    user = await _user(db_session, "reread_replaces")

    async def fetch(url: str) -> FetchedPage:
        return PAGE

    async def parse(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return dict(PARSED)

    monkeypatch.setattr("job_os.integrations.firecrawl.fetch_url_markdown", fetch)
    monkeypatch.setattr("job_os.services.jd_parse.parse_jd", parse)

    job = await jobs_router.create_from_url(
        JobFromUrl(url="https://job-boards.greenhouse.io/glossgenius/jobs/1"),
        _user=user,
        session=db_session,
    )
    job.title = "Custom Job Error - Disney Careers"
    job.location = "Nowhere"
    await db_session.flush()

    await jd_ingest.complete_job_parse(job.id, replace_fields=True)
    await db_session.refresh(job)

    assert job.title == "Senior Backend Engineer"
    assert job.location == "New York, NY"


@pytest.mark.asyncio
async def test_a_first_parse_still_leaves_what_it_already_knows(
    monkeypatch: pytest.MonkeyPatch, db_session, background_session, no_scheduling
) -> None:
    """The default has not changed, and it is a different job from the above.

    A first read is filling a row in. If the parse comes back with a worse
    company or location than the import already had, the import wins.
    """
    user = await _user(db_session, "first_parse_keeps")

    async def fetch(url: str) -> FetchedPage:
        return PAGE

    async def parse(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return dict(PARSED)

    monkeypatch.setattr("job_os.integrations.firecrawl.fetch_url_markdown", fetch)
    monkeypatch.setattr("job_os.services.jd_parse.parse_jd", parse)

    job = await jobs_router.create_from_url(
        JobFromUrl(url="https://job-boards.greenhouse.io/glossgenius/jobs/2"),
        _user=user,
        session=db_session,
    )
    job.title = "Staff Engineer, Payments"
    job.location = "Boston, MA"
    await db_session.flush()

    await jd_ingest.complete_job_parse(job.id)
    await db_session.refresh(job)

    assert job.title == "Staff Engineer, Payments"
    assert job.location == "Boston, MA"


@pytest.mark.asyncio
async def test_a_failed_reread_never_blanks_what_was_already_there(
    monkeypatch: pytest.MonkeyPatch, db_session, background_session, no_scheduling
) -> None:
    """`replace_fields` replaces a parse, not a success with silence.

    Without this, a site being down would make the re-read button destructive:
    press it once on a bad day and the job loses its title, its location and
    its salary and there is no way back.
    """
    user = await _user(db_session, "reread_fails")

    async def fetch(url: str) -> FetchedPage:
        raise RuntimeError("the site was down")

    monkeypatch.setattr("job_os.integrations.firecrawl.fetch_url_markdown", fetch)

    job = await jobs_router.create_from_url(
        JobFromUrl(url="https://job-boards.greenhouse.io/glossgenius/jobs/3"),
        _user=user,
        session=db_session,
    )
    job.title = "Senior Backend Engineer"
    job.location = "New York, NY"
    await db_session.flush()

    await jd_ingest.complete_job_parse(job.id, replace_fields=True)
    await db_session.refresh(job)

    assert job.title == "Senior Backend Engineer"
    assert job.location == "New York, NY"


@pytest.mark.asyncio
async def test_the_reparse_route_asks_for_a_replacing_read(
    monkeypatch: pytest.MonkeyPatch, db_session
) -> None:
    """The button is only worth having if the route asks for the right thing."""
    user = await _user(db_session, "reparse_route_flag")
    asked: list[dict[str, Any]] = []

    def _record(job_id: Any, owner_id: Any = None, **kwargs: Any) -> None:
        asked.append(kwargs)

    monkeypatch.setattr(jd_ingest, "schedule_job_parse", _record)

    job = await jobs_router.create_from_url(
        JobFromUrl(url="https://job-boards.greenhouse.io/glossgenius/jobs/4"),
        _user=user,
        session=db_session,
    )
    asked.clear()
    await jobs_router.reparse_job(job.id, _user=user, session=db_session)

    assert asked == [{"replace_fields": True}]
