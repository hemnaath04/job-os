"""Covers the incident this fixes: a Firecrawl 5xx used to bubble straight up
as an unhandled 500 from /jobs/from-url (and, via the MCP connector, from
add_job_from_url) instead of degrading to the plain fetcher or, failing
that, a clean error the caller can act on.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

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
async def test_create_from_url_turns_a_fetch_failure_into_a_clean_502(
    monkeypatch: pytest.MonkeyPatch, db_session
) -> None:
    from job_os.db.models import User
    from job_os.routers import jobs as jobs_router
    from job_os.schemas.jobs import JobFromUrl

    user = User(clerk_id="clerk_fetch_fail", email="fetch-fail@example.com")
    db_session.add(user)
    await db_session.flush()

    async def boom(url: str) -> firecrawl.FetchedPage:
        raise RuntimeError("502 Bad Gateway")

    monkeypatch.setattr(
        "job_os.integrations.firecrawl.fetch_url_markdown", boom
    )

    with pytest.raises(HTTPException) as exc_info:
        await jobs_router.create_from_url(
            JobFromUrl(url="https://example.com/never-fetched"),
            _user=user,
            session=db_session,
        )
    assert exc_info.value.status_code == 502
    assert "paste the description" in exc_info.value.detail.lower()
