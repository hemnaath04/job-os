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
import json
import re
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from job_os.db.models import Job
from job_os.db.session import async_session
from job_os.services.job_backfill import parse_has_signal
from job_os.settings import get_settings

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


# Hosts belonging to a recruiting vendor rather than to the employer. On these
# the registrable domain names the vendor, so it is the wrong half to read.
_ATS_VENDOR_HOSTS = (
    "myworkdayjobs.com",
    "myworkdaysite.com",
    "oraclecloud.com",
    "icims.com",
    "taleo.net",
    "successfactors.com",
    "successfactors.eu",
    "avature.net",
    "eightfold.ai",
)

# Workday puts a routing label beside the tenant: wd1, wd5, wd503.
_WORKDAY_ROUTING_RE = re.compile(r"wd\d+")


def labels_of(host: str) -> list[str]:
    """Host labels worth reading, with the ones that carry no name removed."""
    return [label for label in host.split(".") if label not in ("www", "careers", "jobs")]


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
    # A host the employer does not own. On these the registrable domain is the
    # recruiting vendor, and the employer is the leftmost label instead:
    # workiva.wd503.myworkdayjobs.com is Workiva, not Myworkdayjobs. Taking the
    # registrable part put "Myworkdayjobs" and "Oraclecloud" on a real board as
    # company names.
    if any(host.endswith(vendor) for vendor in _ATS_VENDOR_HOSTS):
        for label in labels_of(host):
            # Workday hosts a routing label like wd1/wd503 next to the tenant.
            if label in ("fa", "hcm") or _WORKDAY_ROUTING_RE.fullmatch(label):
                continue
            return label.replace("-", " ").title()
        return None
    # Not a vendor host: the registrable-looking part is still better than
    # nothing, minus the www and careers subdomains that carry no information.
    labels = labels_of(host)
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


async def complete_job_parse(job_id: UUID, owner_id: str | None = None) -> None:
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

            # The board reads Appwrite, not this row. Without the write below a
            # card keeps its insert-time snapshot and goes on saying "still
            # reading this posting" long after the reading finished.
            if owner_id:
                await sync_job_into_cards(job, owner_id)

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


def schedule_job_parse(job_id: UUID, owner_id: str | None = None) -> None:
    """Start the deferred parse. Returns immediately."""
    if job_id in _INFLIGHT:
        # Already being parsed in this process. Reached from the sweep below,
        # whose age filter is a clock and cannot see a task that is simply
        # taking a long time; parsing the same posting twice at once would
        # spend two gateway calls to write the same row.
        log.info("jd_ingest.already_running", job_id=str(job_id))
        return
    _INFLIGHT.add(job_id)
    task = asyncio.create_task(complete_job_parse(job_id, owner_id))
    # create_task keeps only a weak reference, so a task nobody holds can be
    # garbage collected mid-flight. Holding it until it finishes is what makes
    # this reliable rather than usually-fine.
    _RUNNING.add(task)
    task.add_done_callback(_RUNNING.discard)
    task.add_done_callback(lambda _t: _INFLIGHT.discard(job_id))


_RUNNING: set[asyncio.Task[None]] = set()
# The job ids those tasks are working on. `_RUNNING` holds tasks, which cannot
# be asked what they are parsing.
_INFLIGHT: set[UUID] = set()


# Nothing older than this could still be running here. The parse budget is two
# minutes and the fetch adds at most about a minute on top of it, so five
# minutes clears the real ceiling with room to spare.
#
# Was ten. Ten minutes was not a bound on the work, it was a guess, and it set
# the floor on how long a lost parse could sit looking like a live one: a row
# stranded thirty seconds before a restart was younger than the cutoff, so the
# restart that could have rescued it skipped it, and it waited for the next
# one. On a dyno that cycles daily that is a card reading "Still reading this
# posting" for hours.
STRANDED_AFTER_SECONDS = 300

# How often the sweep runs once the process is up. The startup pass alone only
# ever recovered a row if a restart happened to follow it, which is the wrong
# thing to depend on: the parse is lost the moment its process dies, and
# nothing about a later deploy makes that more or less true.
SWEEP_INTERVAL_SECONDS = 60


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


async def sweep_stranded_parses_forever() -> None:
    """Re-run the stranded scan on a timer for as long as the process lives.

    A deferred parse lives in the process that started it, so it is lost when
    that process dies: a dyno cycle, a crash, an OOM kill. The row is left at
    parse_pending and the card keeps saying "Still reading this posting".

    Recovery used to happen only in `lifespan`, at startup, which recovers a row
    only if a restart happens to come along AND the row is already older than
    the cutoff at that moment. A row stranded shortly before a restart is
    younger than the cutoff, gets skipped by the restart that could have saved
    it, and then waits for the next one. That is the gap behind a posting that
    appears to take ten minutes to parse when the parse itself takes ten
    seconds.

    Cancelled on shutdown, and never fatal: a failed scan costs one interval,
    where an exception escaping here would take the loop down for the life of
    the process and put the behaviour back exactly as it was.
    """
    while True:
        try:
            await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
            requeued = await requeue_stranded_parses()
            if requeued:
                log.info("jd_ingest.sweep_requeued", count=requeued)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("jd_ingest.sweep_failed")


def _card_job_view(job: Job) -> dict[str, Any]:
    """The job fields a board card shows, in the shape the card already holds."""
    company = getattr(job, "company", None)
    return {
        "id": str(job.id),
        "title": job.title,
        "level": job.level,
        "function": job.function,
        "location": job.location,
        "remote": job.remote,
        "salary_min": job.salary_min,
        "salary_max": job.salary_max,
        "salary_currency": job.salary_currency,
        "source": job.source,
        "source_url": job.source_url,
        "jd_parsed": job.jd_parsed or {},
        "company": (
            {"id": str(company.id), "name": company.name, "domain": company.domain}
            if company is not None
            else None
        ),
    }


async def sync_job_into_cards(job: Job, owner_id: str) -> int:
    """Write a freshly parsed job onto the board cards that show it.

    The board reads Appwrite `application_cards`, not Postgres, and a card
    carries its own copy of the job taken when the card was made. A deferred
    parse updates Postgres only, so without this the card keeps saying "still
    reading this posting" after the reading is long finished, with the title
    and company it never had. Four cards on the live board sat that way.

    Scanned within one owner rather than queried by job id, because the card
    schema keeps the job id inside the `snapshot` JSON where Appwrite cannot
    index it. Bounded by one person's board, and `owner_id` is filtered in the
    query rather than after it: this table is multi-tenant and the API key
    bypasses row permissions, so the filter is the whole of the isolation.
    """
    from job_os.services import appwrite_tables

    settings = get_settings()
    if not settings.appwrite_api_key:
        return 0

    table = settings.appwrite_application_cards_table_id
    fresh = _card_job_view(job)
    updated = 0
    try:
        rows = await appwrite_tables.list_rows(
            # `attribute=value`, which is what _parse_filter accepts. Appwrite's own
            # Query JSON is not a filter expression here and raises.
            filters=[f"owner_id={owner_id}", "archived=false"],
            limit=500,
            table_id=table,
        )
        for row in rows:
            try:
                snapshot = json.loads(row.get("snapshot") or "{}")
            except ValueError:
                continue
            if str((snapshot.get("job") or {}).get("id")) != str(job.id):
                continue
            snapshot["job"] = {**(snapshot.get("job") or {}), **fresh}
            await appwrite_tables.update_rows(
                row_id=row["$id"],
                data={"snapshot": json.dumps(snapshot)},
                table_id=table,
            )
            updated += 1
    except Exception:
        # A card that does not get refreshed is a stale card, not a lost parse.
        # The Postgres write has already committed by the time this runs.
        log.exception("jd_ingest.card_sync_failed", job_id=str(job.id))
        return updated
    if updated:
        log.info("jd_ingest.cards_synced", job_id=str(job.id), cards=updated)
    return updated

