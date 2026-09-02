"""Extract an imported resume after the response, not during it.

WHY THIS EXISTS
    `POST /api/v1/resumes/import` ran an Anthropic extraction per file inside
    the request. Heroku's router gives a request 30 seconds and then returns
    503 to the browser while the dyno keeps working, so a single multi-page
    master resume was enough to lose the response:

        at=error code=H12 desc="Request timeout" method=POST
        path="/api/v1/resumes/import" service=30000ms status=503

    The upload had usually succeeded by then. What the user saw was Heroku's
    error page, and the importer stored that HTML as the import's status,
    which is how a working import came to read
    "Protected master. 503: <!DOCTYPE html> ... Application Error".

    Batching made it worse but was never the cause: the limit is 30 files and
    one file already exceeded the ceiling.

THE SHAPE, which is the one `jd_ingest` already uses for the same reason
    Persist what the upload earned, commit it so another connection can see
    it, answer, and finish the slow part afterwards. The row exists
    immediately with `status="extracting"`, so the UI has something true to
    show and to poll, rather than a request that may or may not come back.

    Deliberately the same module shape as `jd_ingest.schedule_job_parse`,
    including holding a strong reference to the task: `asyncio.create_task`
    keeps only a weak one, so a task nobody holds can be collected
    mid-flight, which is the difference between reliable and usually-fine.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import UUID

import anthropic
import structlog
from pydantic import ValidationError
from sqlalchemy import select

# Module level, like jd_ingest, so the deferred task can be pointed at a test's
# own session. The request that scheduled it has long since closed one, and in
# a test the row lives inside the fixture's outer transaction where no other
# connection can see it.
from job_os.db.session import async_session

log = structlog.get_logger(__name__)

_RUNNING: set[asyncio.Task[None]] = set()
#: Version ids those tasks are working on. `_RUNNING` holds tasks, which
#: cannot be asked which resume they are reading.
_INFLIGHT: set[UUID] = set()

#: What `json_resume` holds until the extraction lands. A real shape rather
#: than an empty object, so anything reading the column before the task
#: finishes sees a valid JSON Resume with no content instead of failing
#: validation on a placeholder.
PENDING_RESUME: dict[str, Any] = {
    "basics": {"name": ""},
    "work": [],
    "education": [],
    "skills": [],
}


def is_pending(document: dict[str, Any] | None) -> bool:
    """Whether this version is still waiting on its extraction."""
    if not document:
        return True
    basics = document.get("basics") or {}
    return not basics.get("name") and not document.get("work")


async def complete_resume_extraction(version_id: UUID) -> None:
    """Read the stored bytes, extract, and fill the version in place."""
    from job_os.db.models.resume import Resume, ResumeVersion
    from job_os.db.models.user import User
    from job_os.services.profile_extract import (
        extract_json_resume_from_docx,
        extract_json_resume_from_pdf,
    )
    from job_os.services.profile_import import import_json_resume
    from job_os.services.resume_engine import validate_json_resume_document

    async with async_session() as session:
        version = (
            await session.execute(select(ResumeVersion).where(ResumeVersion.id == version_id))
        ).scalar_one_or_none()
        if version is None:
            # Reachable: the row is committed before the task starts, but a
            # user can delete an import while it is still being read.
            log.info("resume_ingest.version_missing", version_id=str(version_id))
            return

        filename = (version.source_filename or "").lower()
        raw = version.pdf_bytes
        try:
            if filename.endswith(".pdf"):
                if not raw:
                    raise ValueError("The uploaded PDF was not stored, so it cannot be read.")
                doc = await extract_json_resume_from_pdf(raw)
            elif filename.endswith(".docx"):
                if not raw:
                    raise ValueError("The uploaded DOCX was not stored, so it cannot be read.")
                doc = await extract_json_resume_from_docx(raw)
            elif filename.endswith(".json"):
                if not raw:
                    raise ValueError("The uploaded JSON was not stored, so it cannot be read.")
                doc = json.loads(raw.decode("utf-8"))
            else:
                raise ValueError("Only PDF, DOCX, and JSON Resume files are supported.")
            validate_json_resume_document(doc)
        except (
            ValueError,
            json.JSONDecodeError,
            RuntimeError,
            ValidationError,
            anthropic.APIError,
        ) as exc:
            # Recorded on the row rather than raised into a task nobody is
            # awaiting. A failed extraction that leaves `status="extracting"`
            # forever is indistinguishable from a slow one.
            version.status = "import_failed"
            version.revision_note = f"Import failed: {exc}"
            await session.commit()
            log.warning(
                "resume_ingest.extract_failed",
                version_id=str(version_id),
                error=f"{type(exc).__name__}: {exc}",
            )
            return

        version.json_resume = doc
        version.status = "imported"
        await session.commit()

        # The master resume seeds the profile facts, and that was previously
        # done inside the same request. It belongs with the extraction it
        # depends on, not with the upload.
        resume = (
            await session.execute(select(Resume).where(Resume.id == version.resume_id))
        ).scalar_one_or_none()
        if resume is not None and resume.is_master:
            user = (
                await session.execute(select(User).where(User.id == resume.user_id))
            ).scalar_one_or_none()
            if user is not None:
                await import_json_resume(
                    session, user=user, doc=doc, mark_verified=True, replace_existing=False
                )
                await session.commit()
        log.info("resume_ingest.extract_done", version_id=str(version_id))


def schedule_resume_extraction(version_id: UUID) -> None:
    """Start the deferred extraction. Returns immediately."""
    if version_id in _INFLIGHT:
        log.info("resume_ingest.already_running", version_id=str(version_id))
        return
    _INFLIGHT.add(version_id)
    task = asyncio.create_task(complete_resume_extraction(version_id))
    _RUNNING.add(task)
    task.add_done_callback(_RUNNING.discard)
    task.add_done_callback(lambda _t: _INFLIGHT.discard(version_id))
