"""Thin client for a self-hosted Reactive Resume instance.

When `REACTIVE_RESUME_BASE_URL` is unset, calls raise a clear error so the
route can return a structured "rendering not configured" response instead
of a 500.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from job_os.settings import get_settings

log = structlog.get_logger(__name__)


class ReactiveResumeNotConfigured(RuntimeError):
    pass


@dataclass(slots=True)
class RenderResult:
    format: Literal["pdf", "docx"]
    bytes_: bytes
    content_type: str


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8), reraise=True)
async def render(json_resume: dict, *, fmt: Literal["pdf", "docx"] = "pdf") -> RenderResult:
    s = get_settings()
    if not s.reactive_resume_base_url:
        raise ReactiveResumeNotConfigured(
            "REACTIVE_RESUME_BASE_URL is not set — see docs/SETUP.md for the "
            "Docker run command."
        )

    base = s.reactive_resume_base_url.rstrip("/")
    headers = {}
    if s.reactive_resume_api_key:
        headers["Authorization"] = f"Bearer {s.reactive_resume_api_key}"

    # Reactive Resume's print endpoint accepts a JSON Resume payload.
    # Templates are picked by the document's `meta.template` field; we
    # default to "rhyhorn" which is the closest to a clean tech CV.
    async with httpx.AsyncClient(timeout=60.0, headers=headers) as client:
        resp = await client.post(
            f"{base}/api/v1/resume/print",
            params={"format": fmt},
            json=json_resume,
        )
        resp.raise_for_status()
        ct = resp.headers.get("content-type", "application/octet-stream")
        return RenderResult(format=fmt, bytes_=resp.content, content_type=ct)
