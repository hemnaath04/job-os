import asyncio
from typing import Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from job_os.auth import get_current_user
from job_os.db.models import Job, User
from job_os.db.session import get_session
from job_os.schemas.jobs import (
    JobCreateManual,
    JobDescriptionParse,
    JobDescriptionPaste,
    JobDetailRead,
    JobEnrichPlan,
    JobEnrichResult,
    JobFromText,
    JobFromUrl,
    JobRead,
)
from job_os.services.companies import upsert_company

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/jobs")

# A parse is one model call with its own client timeout and a retry, so its own
# worst case can outlive Heroku's hard 30s router ceiling the same way the
# fetch-and-parse path can. This budget keeps the request inside that ceiling.
# It is spent on the parse alone, since a paste skips the fetch entirely, which
# is most of why this route is the fast way to fix a thin job.
#
# Was 22s, which turned out to be the tightest thing in the chain: two real
# pastes were cut off at 22.1s and 22.2s while the gateway was in a slow window,
# and the person was told no details could be read from a JD that plainly had
# them. 27s leaves room for the write and the response to land before the
# router gives up.
#
# Honest about what this does not do: it buys five seconds, and a slow attempt
# plus jd_parse's retry can still exceed it. That is a ceiling a synchronous
# request cannot get past, and getting past it means a background job the caller
# polls, which is what the two importers now do (see services/jd_ingest.py).
# This route stays synchronous because it answers with what the paste
# earned, and it has no fetch to make, so it is one call inside the
# ceiling rather than two.
_PARSE_BUDGET_SECONDS = 27.0


async def _load_job(session: AsyncSession, job_id: UUID) -> Job | None:
    """Fetch a job with company relationship eagerly loaded (async-safe)."""
    result = await session.execute(
        select(Job).options(joinedload(Job.company)).where(Job.id == job_id)
    )
    return result.unique().scalar_one_or_none()


@router.get("", response_model=list[JobRead])
async def list_jobs(
    *,
    q: str | None = None,
    function: str | None = None,
    level: str | None = None,
    location: str | None = None,
    active: bool = True,
    limit: int = Query(default=50, le=200),
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[Job]:
    stmt = select(Job).where(Job.active == active)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(Job.title.ilike(like))
    if function:
        stmt = stmt.where(Job.function == function)
    if level:
        stmt = stmt.where(Job.level == level)
    if location:
        stmt = stmt.where(Job.location.ilike(f"%{location}%"))
    stmt = stmt.order_by(Job.posted_at.desc().nullslast(), Job.created_at.desc()).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


@router.get("/{job_id}", response_model=JobDetailRead)
async def get_job(
    job_id: UUID,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Job:
    """One job, description included -- see `JobDetailRead` for why only this route."""
    job = await _load_job(session, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return job


@router.post("/manual", response_model=JobRead, status_code=201)
async def create_manual(
    payload: JobCreateManual,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Job:
    from job_os.services.jd_parse import parse_jd

    company = await upsert_company(
        session, name=payload.company_name, domain=payload.company_domain
    )
    parsed = await parse_jd(payload.jd_text, title_hint=payload.title)
    job = Job(
        company_id=company.id,
        title=payload.title,
        level=payload.level or parsed.get("level"),
        function=payload.function or parsed.get("function"),
        location=payload.location,
        remote=payload.remote,
        jd_raw=payload.jd_text,
        jd_clean=payload.jd_text,
        jd_parsed=parsed,
        source="manual",
        source_url=str(payload.source_url) if payload.source_url else None,
    )
    session.add(job)
    await session.flush()
    await session.refresh(job, attribute_names=["company"])
    return job


@router.post("/from-url", response_model=JobRead, status_code=201)
async def create_from_url(
    payload: JobFromUrl,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Job:
    """Save the link now, read the posting after.

    This used to scrape and parse inside the request, which does not fit:
    Heroku kills a request at 30s, the scrape takes about four and the parse
    is budgeted at 25. In production on 2026-08-27 this 504'd after 28.3s and
    sent the person to the paste tab, which then spent 27s reaching an empty
    parse. Neither path could return a structured job, so the fallback was not
    one. Both now answer immediately and fill in underneath.
    """
    from job_os.services.jd_ingest import (
        PENDING,
        company_hint_from_url,
        schedule_job_parse,
    )

    url = str(payload.url)

    # Same dedup this app already does correctly for discovery imports
    # (discovery.py's import_result, keyed on source_id there). A URL paste
    # has no vendor id to key on, so source_url is the equivalent identity.
    # Without this, `source_id IS NULL` on every row here, and Postgres does
    # not treat NULL = NULL, so the unique constraint on (source, source_id)
    # never once caught a repeat paste of the same link.
    existing_q = await session.execute(
        select(Job)
        .options(joinedload(Job.company))
        .where(Job.source == "url", Job.source_url == url)
    )
    existing = existing_q.unique().scalar_one_or_none()
    if existing:
        return existing

    # A guess, and labelled as one by the pending parse beside it. The slug in
    # an ATS link is the company and costs nothing to read, which is the
    # difference between a card that looks busy and one that looks broken.
    company = await upsert_company(
        session, name=company_hint_from_url(url) or "Unknown", domain=None
    )
    job = Job(
        company_id=company.id,
        title="Untitled",
        # NOT NULL, and the posting has not been fetched yet. Empty is the
        # truthful placeholder: the scrape fills both in the background, and
        # anything reading jd_clean for substance already has to handle a job
        # whose description never arrived.
        jd_raw="",
        jd_clean="",
        jd_parsed=dict(PENDING),
        source="url",
        source_url=url,
    )
    session.add(job)
    await session.flush()
    await session.refresh(job, attribute_names=["company"])
    # Committed here rather than left to get_session's commit on the way out:
    # the background task opens its own session and looks this row up by id,
    # so the row has to be visible to another connection before the task can
    # start. Scheduling first is a race that loses quietly, as a parse that
    # reports the job missing.
    await session.commit()
    schedule_job_parse(job.id, getattr(_user, "clerk_id", None))
    return job


@router.post("/from-text", response_model=JobRead, status_code=201)
async def create_from_text(
    payload: JobFromText,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Job:
    """Save the description now, read it after.

    The paste tab is what the URL importer recommends when it times out, and
    it was parsing inside the request too, so a heavy posting failed both
    ways. It has the text already, so the row it writes is complete apart from
    what the model has to read out of it.
    """
    from job_os.services.jd_ingest import PENDING, schedule_job_parse

    source_url = str(payload.source_url) if payload.source_url else None

    # Same reasoning as create_from_url: source_id is never set here either,
    # so the DB constraint alone never caught a repeat paste. A source_url,
    # when the user gave one, is the strongest identity available. Lacking
    # that, the pasted text itself is the only identity a raw JD paste has.
    dedup_clause = (
        Job.source_url == source_url
        if source_url
        else Job.jd_clean == payload.jd_text
    )
    existing_q = await session.execute(
        select(Job)
        .options(joinedload(Job.company))
        .where(Job.source == "text", dedup_clause)
    )
    existing = existing_q.unique().scalar_one_or_none()
    if existing:
        return existing

    company = await upsert_company(
        session, name=payload.company_hint or "Unknown", domain=None
    )
    job = Job(
        company_id=company.id,
        title="Untitled",
        jd_raw=payload.jd_text,
        jd_clean=payload.jd_text,
        jd_parsed=dict(PENDING),
        source="text",
        source_url=source_url,
    )
    session.add(job)
    await session.flush()
    await session.refresh(job, attribute_names=["company"])
    await session.commit()
    schedule_job_parse(job.id, getattr(_user, "clerk_id", None))
    return job


@router.post("/{job_id}/reparse", response_model=JobRead, status_code=202)
async def reparse_job(
    job_id: UUID,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Job:
    """Read this posting again, in the background.

    The deferred parse runs in this process, so a dyno restart mid-parse
    leaves a row at `parse_pending` with nothing coming for it. Rather than
    reach for durable queueing on a one-dyno app, this makes the stuck state
    recoverable: anything pending, incomplete, or thin can be asked again
    without re-importing it, which matters because re-importing would strand
    the application, documents and history already attached to this row.
    """
    from job_os.services.jd_ingest import PENDING, schedule_job_parse

    job = await _load_job(session, job_id)
    if job is None:
        raise HTTPException(404, "Job not found.")
    if job.source == "text" and not (job.jd_clean or job.jd_raw or "").strip():
        raise HTTPException(422, "Paste the job description first.")

    job.jd_parsed = dict(PENDING)
    await session.commit()
    schedule_job_parse(job.id, getattr(_user, "clerk_id", None))
    # Re-SELECT, for the reason `add_description` sets out at length below: this
    # is an UPDATE, `updated_at` carries onupdate=func.now(), and SQLAlchemy
    # fetches server-generated columns back on an INSERT but not on an UPDATE.
    # `refresh(attribute_names=["company"])` loaded the relationship and left
    # `updated_at` expired, so serialising the response was one lazy load away
    # from MissingGreenlet -- the same 500 that shipped on the paste route, on
    # the retry button one endpoint over.
    job = await _load_job(session, job_id)
    if job is None:  # pragma: no cover - the row was committed moments ago
        raise HTTPException(404, "Job not found.")
    return job


@router.post("/{job_id}/description", response_model=JobEnrichResult)
async def add_description(
    job_id: UUID,
    payload: JobDescriptionPaste,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> JobEnrichResult:
    """Fill in a thin job from its description, in place.

    A URL import can land with no location, no work type, no salary and an
    empty parse, which is also why its match score honestly reports itself as
    unavailable. This gives the parser the text it never got and backfills
    what it finds, on the SAME job: the person is already tracking it, so
    creating a second row would split its application, its documents and its
    history away from the row they belong to.
    """
    from job_os.services.jd_parse import parse_jd
    from job_os.services.job_backfill import apply_enrichment, plan_enrichment

    jd_text = payload.jd_text.strip()
    if not jd_text:
        raise HTTPException(422, "Paste the job description first.")

    job = await _load_job(session, job_id)
    if job is None:
        raise HTTPException(404, "Job not found.")

    # A parse that times out or fails is not a reason to lose the paste. The
    # description is the durable part and is worth storing on its own: it is
    # what the tailor reads, and what a later attempt would re-parse. So this
    # degrades to "saved, learned nothing" and says so, rather than 500ing and
    # throwing away text the person typed.
    parsed: dict[str, Any] = {}
    try:
        parsed = await asyncio.wait_for(
            # The job's own title goes in as a hint: a posting's heading often
            # names the location and the company where the body never does.
            # parse_jd gets the budget too, so it stops and says so rather than
            # being cut off mid-attempt by the wait_for below.
            parse_jd(
                jd_text,
                title_hint=job.title,
                deadline_seconds=_PARSE_BUDGET_SECONDS - 2,
            ),
            timeout=_PARSE_BUDGET_SECONDS,
        )
    except TimeoutError:
        log.warning("jobs.description.parse_timeout", job_id=str(job_id))
    except Exception as e:
        log.warning("jobs.description.parse_failed", job_id=str(job_id), error=str(e))

    plan = plan_enrichment(job, parsed, jd_text)
    apply_enrichment(job, plan)
    await session.flush()

    # Re-SELECT rather than refresh a named attribute, because this endpoint
    # UPDATEs a row where every other one here INSERTs, and that difference is
    # what decides whether the object is safe to serialise.
    #
    # `updated_at` carries `onupdate=func.now()` (db/models/_mixins.py), so the
    # flush's UPDATE leaves it expired: only the database knows its new value.
    # Reading an expired attribute costs a round trip, and doing that from
    # `JobRead.model_validate` inside an async request is precisely what raises
    # MissingGreenlet. An INSERT never hits this, since SQLAlchemy fetches
    # server-generated columns back as part of the insert, which is why
    # `refresh(attribute_names=["company"])` is sufficient in create_from_url
    # and create_from_text and was silently insufficient here.
    #
    # A bare `session.refresh(job)` would repopulate the columns but expire the
    # `company` relationship, moving the same failure onto a different field.
    # One eager-loading SELECT settles both: it fills every expired column and
    # loads the relationship in the same statement.
    job = await _load_job(session, job_id)
    if job is None:  # pragma: no cover - the row was loaded moments ago
        raise HTTPException(404, "Job not found.")

    log.info(
        "jobs.description.enriched",
        job_id=str(job_id),
        filled=plan.filled,
        parse_used=plan.parse_replaced,
    )
    return JobEnrichResult(
        job=JobRead.model_validate(job),
        filled=plan.filled,
        parse_used=plan.parse_replaced,
    )


@router.post("/parse-description", response_model=JobEnrichPlan)
async def parse_description(
    payload: JobDescriptionParse,
    _user: User = Depends(get_current_user),
) -> JobEnrichPlan:
    """Plan a backfill from a pasted description, without persisting anything.

    The sibling `/{job_id}/description` writes the Postgres row and is right
    only when the job lives there. The live pipeline keeps applications in
    Appwrite, and a card created there has no Postgres `jobs` row at all, so
    that endpoint answers 404 for a job the user can plainly see on their
    board. This one takes the job as the caller holds it and hands back what
    changed, leaving the caller to write it wherever the job actually lives.

    Stateless on purpose: no session, no row, no id. That is what makes it
    correct for both stores instead of one.
    """
    import asyncio

    from job_os.services.jd_parse import parse_jd
    from job_os.services.job_backfill import plan_enrichment

    jd_text = payload.jd_text.strip()
    if not jd_text:
        raise HTTPException(422, "Paste the job description first.")

    parsed: dict[str, Any] = {}
    try:
        parsed = await asyncio.wait_for(
            parse_jd(
                jd_text,
                title_hint=payload.job.title,
                deadline_seconds=_PARSE_BUDGET_SECONDS - 2,
            ),
            timeout=_PARSE_BUDGET_SECONDS,
        )
    except TimeoutError:
        log.warning("jobs.parse_description.timeout")
    except Exception as e:
        log.warning("jobs.parse_description.failed", error=str(e))

    plan = plan_enrichment(payload.job, parsed, jd_text)

    # The planner also stages the description onto the row, which is right for
    # the Postgres path and meaningless here: nothing on an Appwrite card reads
    # it, and a full JD in every card snapshot is weight for nothing.
    updates = {k: v for k, v in plan.updates.items() if k not in ("jd_raw", "jd_clean")}

    log.info(
        "jobs.parse_description.planned",
        filled=plan.filled,
        parse_used=plan.parse_replaced,
    )
    return JobEnrichPlan(
        updates=updates,
        filled=plan.filled,
        parse_used=plan.parse_replaced,
    )
