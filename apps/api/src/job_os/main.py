import os
from contextlib import asynccontextmanager
from time import perf_counter

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from job_os import __version__
from job_os.db.session import engine
from job_os.observability import setup_observability
from job_os.routers import (
    applications,
    calendar,
    discovery,
    interviews,
    jobs,
    me,
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
async def lifespan(_app: FastAPI):
    settings = get_settings()
    log.info("api.startup", env=settings.app_env, version=__version__)
    yield
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

# CORS allow-list. Append production origins via WEB_ORIGINS env var
# (comma-separated). The Next.js auth proxy fronts most calls so CORS is rarely
# in the hot path, but we still need it correct for direct browser fetches
# (e.g. file downloads from /resumes).
_extra_origins = [o.strip() for o in os.environ.get("WEB_ORIGINS", "").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", *_extra_origins],
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

app.include_router(applications.router, prefix="/api/v1", tags=["applications"])
app.include_router(calendar.router, prefix="/api/v1", tags=["calendar"])
app.include_router(discovery.router, prefix="/api/v1", tags=["discovery"])
app.include_router(interviews.router, prefix="/api/v1", tags=["interviews"])
app.include_router(jobs.router, prefix="/api/v1", tags=["jobs"])
app.include_router(me.router, prefix="/api/v1", tags=["me"])
app.include_router(profile.router, prefix="/api/v1", tags=["profile"])
app.include_router(resumes.router, prefix="/api/v1", tags=["resumes"])
