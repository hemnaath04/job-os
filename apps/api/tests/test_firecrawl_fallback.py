"""Covers the incident this fixes: a Firecrawl 5xx used to bubble straight up
as an unhandled 500 from /jobs/from-url (and, via the MCP connector, from
add_job_from_url) instead of degrading to the plain fetcher or, failing
that, something the caller can act on.

The last test changed shape when the fetch moved off the request path: there
is no longer a caller waiting to be handed a 502, so the failure is asserted
where it now has to land, on the job row itself.
"""
from __future__ import annotations

import pytest

from job_os.integrations import firecrawl


class _FakeSettings:
    firecrawl_api_key = "test-key"


@pytest.mark.asyncio
async def test_firecrawl_5xx_falls_back_to_plain_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(firecrawl, "get_settings", lambda: _FakeSettings())

    async def boom(url: str, api_key: str) -> firecrawl.FetchedPage:
        raise RuntimeError("502 Bad Gateway")

    plain_page = firecrawl.FetchedPage(
        url="https://example.com/job", markdown="plain fetch worked", raw="<html></html>",
        title="A Job", company_hint="Example",
    )

    async def fake_plain(url: str) -> firecrawl.FetchedPage:
        return plain_page

    monkeypatch.setattr(firecrawl, "_fetch_firecrawl_retrying", boom)
    monkeypatch.setattr(firecrawl, "_fetch_plain", fake_plain)

    result = await firecrawl.fetch_url_markdown("https://example.com/job")
    assert result is plain_page


@pytest.mark.asyncio
async def test_original_firecrawl_error_propagates_when_plain_fetch_also_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(firecrawl, "get_settings", lambda: _FakeSettings())

    async def firecrawl_boom(url: str, api_key: str) -> firecrawl.FetchedPage:
        raise RuntimeError("firecrawl 502")

    async def plain_boom(url: str) -> firecrawl.FetchedPage:
        raise ValueError("blocked host")

    monkeypatch.setattr(firecrawl, "_fetch_firecrawl_retrying", firecrawl_boom)
    monkeypatch.setattr(firecrawl, "_fetch_plain", plain_boom)

    with pytest.raises(RuntimeError, match="firecrawl 502"):
        await firecrawl.fetch_url_markdown("https://example.com/job")


@pytest.mark.asyncio
async def test_a_fetch_failure_lands_on_the_row_instead_of_the_request(
    monkeypatch: pytest.MonkeyPatch, db_session, background_session
) -> None:
    """The fetch moved off the request path, so its failure had to move too.

    This used to be a 502 the caller could act on, which was the best a
    synchronous import could do. Now the import returns a real job before any
    fetching happens, so a fetch that fails has to be recorded on the job
    rather than raised at someone who has already been answered. Silence here
    would leave the row at parse_pending forever, which is the one outcome
    with no honest reading.
    """
    from job_os.db.models import User
    from job_os.routers import jobs as jobs_router
    from job_os.schemas.jobs import JobFromUrl
    from job_os.services import jd_ingest

    user = User(clerk_id="clerk_fetch_fail", email="fetch-fail@example.com")
    db_session.add(user)
    await db_session.flush()

    async def boom(url: str) -> firecrawl.FetchedPage:
        raise RuntimeError("502 Bad Gateway")

    monkeypatch.setattr("job_os.integrations.firecrawl.fetch_url_markdown", boom)
    scheduled: list[object] = []
    monkeypatch.setattr(
        jd_ingest, "schedule_job_parse", lambda job_id, owner_id=None: scheduled.append(job_id)
    )
    monkeypatch.setattr(jobs_router, "_load_job", jobs_router._load_job)

    job = await jobs_router.create_from_url(
        JobFromUrl(url="https://job-boards.greenhouse.io/glossgenius/jobs/1"),
        _user=user,
        session=db_session,
    )

    # Answered, not raised at: the row exists and is trackable immediately.
    assert job.title == "Untitled"
    assert job.jd_parsed == {"parse_pending": True}
    assert job.company.name == "Glossgenius"

    await jd_ingest.complete_job_parse(job.id)
    await db_session.refresh(job)

    assert job.jd_parsed["parse_incomplete"] is True
    assert job.jd_parsed["parse_error"] == "fetch_failed"
    # The guess from the URL survives a failed fetch: replacing it with
    # "Unknown" would make a legible card worse for no new information.
    assert job.company.name == "Glossgenius"
