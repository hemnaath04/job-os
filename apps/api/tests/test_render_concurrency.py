"""Two renders must make progress together, not queue behind each other.

`render_resume_pdf` compiles through a blocking `subprocess.run`. Calling it
directly from an async handler on this single-worker process serializes every
other request behind that one compile: two "parallel" tailor jobs would
actually run one after the other, and so would one tailor job and an unrelated
page load. `render_resume_pdf_async` exists to move that wait onto a worker
thread instead.
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import patch

from job_os.services.latex_render import RenderedPdf, render_resume_pdf_async

SLEEP_SECONDS = 0.2


def _slow_render(json_resume: dict, **_kwargs: object) -> RenderedPdf:
    time.sleep(SLEEP_SECONDS)
    return RenderedPdf(bytes_=b"%PDF-fake")


async def test_two_renders_overlap_instead_of_queueing() -> None:
    with patch(
        "job_os.services.latex_render.render_resume_pdf", side_effect=_slow_render
    ):
        started = time.monotonic()
        await asyncio.gather(
            render_resume_pdf_async({}),
            render_resume_pdf_async({}),
        )
        elapsed = time.monotonic() - started

    # Serialized, two 0.2s renders take >= 0.4s. Run concurrently on separate
    # threads, they take about one render's worth of wall time.
    assert elapsed < SLEEP_SECONDS * 1.5, (
        f"two renders took {elapsed:.3f}s, which is what queueing looks like"
    )


async def test_a_render_does_not_block_unrelated_async_work() -> None:
    """The event loop itself must stay free while a render is in flight.

    This is the actual production symptom: an unrelated request (any other
    page load, any other user's request) hanging until a render inside a
    completely different request finishes.
    """
    with patch(
        "job_os.services.latex_render.render_resume_pdf", side_effect=_slow_render
    ):
        ticks = 0

        async def tick_counter() -> None:
            nonlocal ticks
            for _ in range(20):
                await asyncio.sleep(SLEEP_SECONDS / 20)
                ticks += 1

        await asyncio.gather(render_resume_pdf_async({}), tick_counter())

    # If the render blocked the loop, the counter could not have advanced
    # while it ran and would land far short of its 20 ticks.
    assert ticks >= 15, f"only {ticks}/20 ticks ran, so the loop was blocked"
