"""Importing a job without making the person wait for the model.

Both importers used to fetch and parse inside the request. That cannot work:
Heroku's router kills a request at 30 seconds, a Firecrawl scrape of a real
posting takes about four, and a JD parse is budgeted at 25 on its own. The two
together do not fit, and no amount of budget tuning makes them fit, because the
ceiling is not ours to raise. A URL import 504'd in production on 2026-08-27
after 28.3 seconds, and the paste that was recommended as its fallback then
spent 27 seconds reaching an empty parse.

So the request now does only what it can do quickly and honestly: write down
the job, mark the parse as pending, and answer. The reading happens after, in
the same process, and the row fills in underneath. The job is real and
trackable the whole time; only its structured fields arrive late.

Same in-process shape as resumes.py's render-review job, and for the same
reason: this image runs `--workers 1`, so there is exactly one process, and a
background task started here is one the poller can see the results of.
"""
from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from job_os.db.models import Job
from job_os.db.session import async_session
from job_os.services.job_backfill import parse_has_signal

log = structlog.get_logger(__name__)

# What jd_parsed holds between the insert and the parse landing.
#
# A sentinel in the column the reader already looks at, rather than a new one,
# so nothing has to learn a second place to check: `parse_pending` sits beside
# `parse_incomplete` and answers the question that field could not, which is
# the difference between "we tried and failed" and "we have not tried yet".
# Both are the honest alternative to six empty lists.
PENDING: dict[str, Any] = {"parse_pending": True}

# The parse is off the request path now, so it is no longer racing Heroku and
# does not need the tight budget that made a slow gateway fatal.
BACKGROUND_PARSE_SECONDS = 120.0

# The slug in an ATS URL is the company, and knowing it costs nothing: no
# network, no model. Worth doing because the alternative on every pending
# import is a card reading "Unknown / Untitled", which looks broken rather
# than busy.
_ATS_SLUG_HOSTS = {
    "job-boards.greenhouse.io": 0,
    "boards.greenhouse.io": 0,
    "jobs.lever.co": 0,
    "jobs.ashbyhq.com": 0,
    "apply.workable.com": 0,
}


def company_hint_from_url(url: str) -> str | None:
    """A readable company name from the URL alone, or None.

    Only a hint: the parse overwrites it the moment it knows better. It exists
    so a pending card says "GlossGenius" instead of "Unknown" for the minute
    before the real name arrives.
    """
    try:
        parts = urlparse(url)
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    segments = [segment for segment in parts.path.split("/") if segment]
    index = _ATS_SLUG_HOSTS.get(host)
    if index is not None and len(segments) > index:
        slug = segments[index].replace("-", " ").replace("_", " ").strip()
        if slug:
            return slug.title()
    # Not an ATS we know: the registrable-looking part of the host is still
    # better than nothing, minus the www and the careers subdomain that carry
    # no information.
    labels = [label for label in host.split(".") if label not in ("www", "careers", "jobs")]
    if len(labels) >= 2:
        return labels[-2].replace("-", " ").title()
    return None


def _incomplete(reason: str) -> dict[str, Any]:
    """What jd_parsed holds when the reading did not happen.

    Carries `parse_incomplete` so it reads exactly like a failed synchronous
    parse to everything downstream, which is what keeps the scorer honest: it
    reports "could not check" rather than a confident 0% match.
    """
    return {"parse_incomplete": True, "parse_error": reason}


async def _read_posting(job: Job) -> tuple[dict[str, Any], str | None, str | None]:
    """Fetch if needed, then parse. Returns (parsed, jd_raw, jd_clean)."""
    from job_os.services.jd_parse import parse_jd

    if job.source == "url" and job.source_url:
        from job_os.integrations.firecrawl import fetch_url_markdown

        # Inside the background task, so a slow or failing scrape costs the
        # person nothing they are waiting on. This is the call that made the
        # request path a two-call pipeline inside a 30 second ceiling, and
        # moving the parse alone would have left the 504 in place, just rarer.
        fetched = await fetch_url_markdown(job.source_url)
        parsed = await parse_jd(
            fetched.markdown,
            title_hint=fetched.title,
            deadline_seconds=BACKGROUND_PARSE_SECONDS,
        )
        return parsed, fetched.raw, fetched.markdown

    text = job.jd_clean or job.jd_raw or ""
    if not text.strip():
        return _incomplete("no_description"), None, None
    parsed = await parse_jd(text, deadline_seconds=BACKGROUND_PARSE_SECONDS)
    return parsed, None, None


async def complete_job_parse(job_id: UUID) -> None:
    """Fill in a job whose parse was deferred, in place.

    Opens its own session: the request that scheduled this has long since
    returned and closed its own. Writes on every path, including failure,
    because a row left at `parse_pending` forever is the one outcome with no
    honest reading at all.
    """
    from job_os.services.companies import upsert_company

    try:
        async with async_session() as session:
            result = await session.execute(
                select(Job).options(joinedload(Job.company)).where(Job.id == job_id)
            )
            job = result.unique().scalar_one_or_none()
            if job is None:
                # Deleted between the insert and here. Nothing to fill.
                log.info("jd_ingest.job_gone", job_id=str(job_id))
                return

            try:
                parsed, raw, clean = await _read_posting(job)
            except Exception as exc:  # noqa: BLE001 -- every failure has to land on the row
                log.warning(
                    "jd_ingest.read_failed", job_id=str(job_id), error=str(exc)[:300]
                )
                parsed, raw, clean = _incomplete("fetch_failed"), None, None

            if raw is not None:
                job.jd_raw = raw
            if clean is not None:
                job.jd_clean = clean

            title = parsed.get("title")
            if title and job.title in (None, "", "Untitled"):
                job.title = title
            for attribute in ("level", "function", "location", "remote"):
                value = parsed.get(attribute)
                if value and getattr(job, attribute, None) in (None, ""):
                    setattr(job, attribute, value)
            if parsed.get("salary_min") and job.salary_min is None:
                job.salary_min = parsed["salary_min"]
            if parsed.get("salary_max") and job.salary_max is None:
                job.salary_max = parsed["salary_max"]
                if parsed.get("salary_currency"):
                    job.salary_currency = parsed["salary_currency"]

            # The company the request guessed from a hint or a URL slug is
            # replaced only by a name the parse actually read, never by a
            # blank, so a failed parse leaves the guess standing rather than
            # resetting a legible card to "Unknown".
            company_name = parsed.get("company")
            if company_name:
                company = await upsert_company(
                    session, name=company_name, domain=parsed.get("company_domain")
                )
                job.company_id = company.id

            # Stored whether or not it found anything: an incomplete parse is
            # a fact about this job that the scorer and the interface both
            # need, and leaving PENDING in place would claim it is still
            # coming when it is not.
            job.jd_parsed = parsed
            await session.commit()

            log.info(
                "jd_ingest.done",
                job_id=str(job_id),
                signal=parse_has_signal(parsed),
                incomplete=bool(parsed.get("parse_incomplete")),
            )
    except Exception:
        # The task is nobody's to await, so an exception escaping here would
        # be swallowed by the event loop and the row would sit at pending with
        # no record of why.
        log.exception("jd_ingest.failed", job_id=str(job_id))


def schedule_job_parse(job_id: UUID) -> None:
    """Start the deferred parse. Returns immediately."""
    task = asyncio.create_task(complete_job_parse(job_id))
    # create_task keeps only a weak reference, so a task nobody holds can be
    # garbage collected mid-flight. Holding it until it finishes is what makes
    # this reliable rather than usually-fine.
    _RUNNING.add(task)
    task.add_done_callback(_RUNNING.discard)


_RUNNING: set[asyncio.Task[None]] = set()


# Nothing older than this could still be running: the parse budget is two
# minutes and the fetch has its own timeout well under that. Anything still
# pending past it belongs to a process that is gone.
STRANDED_AFTER_SECONDS = 600


async def requeue_stranded_parses() -> int:
    """Restart parses whose process died, at startup.

    The deferred parse lives in this process, so a dyno restart mid-parse
    leaves the row at parse_pending with nothing coming for it, and Heroku
    restarts dynos daily. That is frequent enough that "the match appears once
    it lands" would become a lie for a real share of imports.

    Cheaper and more honest than durable queueing on a one-dyno app: the
    pending marker is already in the row, so the row IS the queue, and the
    thing that recovers it is the process coming back up. Bounded, because a
    row pending past STRANDED_AFTER_SECONDS cannot belong to a live task.
    """
    from datetime import UTC, datetime, timedelta

    cutoff = datetime.now(UTC) - timedelta(seconds=STRANDED_AFTER_SECONDS)
    try:
        async with async_session() as session:
            result = await session.execute(
                select(Job.id).where(
                    Job.jd_parsed["parse_pending"].astext == "true",
                    Job.updated_at < cutoff,
                )
            )
            stranded = [row[0] for row in result.all()]
    except Exception:
        # Startup must not depend on this. A failure here costs a requeue,
        # which the reparse endpoint can still do by hand; raising would cost
        # the whole API.
        log.exception("jd_ingest.requeue_scan_failed")
        return 0

    for job_id in stranded:
        schedule_job_parse(job_id)
    if stranded:
        log.info("jd_ingest.requeued_stranded", count=len(stranded))
    return len(stranded)

