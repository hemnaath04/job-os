import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from time import perf_counter

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from job_os import __version__
from job_os.db.session import engine
from job_os.observability import setup_observability
from job_os.routers import (
    alerts,
    applications,
    calendar,
    cover_letters,
    discovery,
    interviews,
    job_index,
    jobs,
    me,
    outreach,
    profile,
    resumes,
)
from job_os.settings import Settings, get_settings

log = structlog.get_logger()

# At import rather than in `lifespan`, so a crash while the app is still being
# constructed is reported too. That is exactly the failure you cannot see from
# the outside: the container exits and the platform shows a dead deployment with
# no explanation.
setup_observability("api")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    log.info("api.startup", env=settings.app_env, version=__version__)
    # A deferred JD parse runs in this process, so whatever was mid-parse when
    # this dyno last went down is stranded at parse_pending with nothing
    # coming for it. Heroku restarts dynos daily, so that is a routine event
    # rather than an edge case. Never fatal: see requeue_stranded_parses.
    from job_os.services.jd_ingest import (
        requeue_stranded_parses,
        sweep_stranded_parses_forever,
    )

    await requeue_stranded_parses()
    # And keep looking. The startup pass alone recovers a row only if a restart
    # happens to follow it, and only if the row was already past the cutoff at
    # that moment: a parse stranded shortly before a restart is too young to be
    # picked up by it and waits for the next one.
    sweep = asyncio.create_task(sweep_stranded_parses_forever())
    try:
        yield
    finally:
        sweep.cancel()
        with suppress(asyncio.CancelledError):
            await sweep
    log.info("api.shutdown")


def docs_urls(settings: Settings) -> dict[str, str | None]:
    """Where to serve the interactive docs, or None to not serve them.

    /docs, /redoc and /openapi.json all answered 200 unauthenticated on the
    production API: 90 KB of schema, 48 paths, 63 models. The routes themselves are
    correctly 401, so this leaked shape rather than data -- but it handed anyone
    probing a complete map of the surface for free, which is the reconnaissance step
    for everything else. Served in development only.
    """
    if not settings.is_dev:
        return {"docs_url": None, "redoc_url": None, "openapi_url": None}
    return {"docs_url": "/docs", "redoc_url": "/redoc", "openapi_url": "/openapi.json"}


_DOCS = docs_urls(get_settings())

app = FastAPI(
    title="job.os API",
    version=__version__,
    lifespan=lifespan,
    # Passed explicitly rather than splatted. A `**dict[str, str | None]` cannot be
    # checked against FastAPI's signature and produced sixteen mypy errors on one
    # line; three named arguments are both type-safe and easier to read.
    docs_url=_DOCS["docs_url"],
    redoc_url=_DOCS["redoc_url"],
    openapi_url=_DOCS["openapi_url"],
)

def cors_origins(settings: Settings) -> list[str]:
    """The credentialed CORS allow-list. Production origins come from WEB_ORIGINS.

    The Next.js auth proxy fronts most calls so CORS is rarely in the hot path, but
    we still need it correct for direct browser fetches (e.g. file downloads from
    /resumes).

    `http://localhost:3000` used to be allowed unconditionally, including in
    production, and `allow_credentials=True` makes that a real grant rather than a
    leftover: any page a browser loads from localhost:3000 -- a dev server for an
    untrusted repo, or any local process that binds the port -- could call the
    production API with the user's cookies attached AND read the replies, because
    the browser only checks the origin string, not who is actually listening on it.
    Development is the only place that origin means what it says, so it is only
    trusted there. Production supplies its real origins through WEB_ORIGINS, which
    is already a documented Heroku config var (docs/DEPLOY.md).
    """
    extra = [o.strip() for o in os.environ.get("WEB_ORIGINS", "").split(",") if o.strip()]
    if settings.is_dev:
        return ["http://localhost:3000", *extra]
    return extra


app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins(get_settings()),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


#: The commit this image was built from, baked in at build time via a build arg.
#: "unknown" when the image was built without one, which is itself informative.
#:
#: Exists because "is production running main?" took an entire triage phase to
#: answer: the API deploys by a manual `heroku container:push`, so main and the
#: running image drift silently and nothing reports it. With the SHA on /health the
#: api-health workflow can compare it against main's tip and warn on drift, which
#: turns a research question into a monitor.
GIT_SHA = os.environ.get("GIT_SHA", "unknown")


@app.get("/health", tags=["meta"])
async def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__, "git_sha": GIT_SHA}


@app.get("/health/ready", tags=["meta"])
async def readiness() -> dict[str, str | int]:
    """Confirm the API and Postgres compute are both ready for user traffic."""
    started = perf_counter()
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))
    return {
        "status": "ok",
        "version": __version__,
        "database_ms": round((perf_counter() - started) * 1_000),
    }

app.include_router(alerts.router, prefix="/api/v1", tags=["alerts"])
app.include_router(applications.router, prefix="/api/v1", tags=["applications"])
app.include_router(calendar.router, prefix="/api/v1", tags=["calendar"])
app.include_router(cover_letters.router, prefix="/api/v1", tags=["cover-letters"])
app.include_router(discovery.router, prefix="/api/v1", tags=["discovery"])
app.include_router(interviews.router, prefix="/api/v1", tags=["interviews"])
# Indexed search, served from the crawled `job_postings` table. Added alongside
# /discovery/search rather than replacing it; the swap-over is a documented step.
app.include_router(job_index.router, prefix="/api/v1", tags=["index"])
app.include_router(jobs.router, prefix="/api/v1", tags=["jobs"])
app.include_router(me.router, prefix="/api/v1", tags=["me"])
app.include_router(outreach.router, prefix="/api/v1", tags=["outreach"])
app.include_router(profile.router, prefix="/api/v1", tags=["profile"])
app.include_router(resumes.router, prefix="/api/v1", tags=["resumes"])
