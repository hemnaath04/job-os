from contextlib import asynccontextmanager

import os

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from job_os import __version__
from job_os.routers import applications, jobs, profile, resumes
from job_os.settings import get_settings

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    log.info("api.startup", env=settings.app_env, version=__version__)
    yield
    log.info("api.shutdown")


app = FastAPI(
    title="job.os API",
    version=__version__,
    lifespan=lifespan,
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


@app.get("/health", tags=["meta"])
async def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}




app.include_router(applications.router, prefix="/api/v1", tags=["applications"])
app.include_router(jobs.router, prefix="/api/v1", tags=["jobs"])
app.include_router(profile.router, prefix="/api/v1", tags=["profile"])
app.include_router(resumes.router, prefix="/api/v1", tags=["resumes"])
