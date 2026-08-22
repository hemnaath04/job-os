"""`parse_smart_query`'s degraded paths.

Three ways the AI step can fail to hand back parsed filters, and all three
have to leave the search itself still runnable: no key configured, a reply
that isn't valid JSON, and -- the gap this file exists to close -- the
gateway call itself raising. `discovery_smart_search.py` used to call
`client.messages.create` directly, bypassing `create_message`'s retry and
fallback schedule and raising straight through to a 500 on any gateway
failure (the M102 "OAuth credentials could not be refreshed" error, seen in
production, is exactly this). Every failure mode falls back to the raw query
as a single title keyword, which the FE's subsequent `/discovery/search`
call still resolves against the index/DB search -- so a broken AI step
degrades the search, it does not break it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import anthropic
import httpx
import pytest

from job_os.schemas.discovery import DiscoverySearchRequest
from job_os.services import discovery_smart_search


@dataclass
class _FakeSettings:
    anthropic_api_key: str | None = "manifest-key"
    anthropic_base_url: str | None = None
    manifest_tier_fast: str = "job-os-haiku"
    anthropic_model_extract: str = "manifest/auto"


@pytest.mark.asyncio
async def test_no_api_key_falls_back_to_the_raw_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        discovery_smart_search, "get_settings", lambda: _FakeSettings(anthropic_api_key=None)
    )
    result = await discovery_smart_search.parse_smart_query("python intern boston")
    assert result.filters == DiscoverySearchRequest(title_keywords=["python intern boston"])


@pytest.mark.asyncio
async def test_gateway_failure_falls_back_to_the_raw_query_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(discovery_smart_search, "get_settings", lambda: _FakeSettings())

    async def _raise_gateway_error(*_args: Any, **_kwargs: Any) -> None:
        request = httpx.Request("POST", "https://gateway.test/v1/messages")
        response = httpx.Response(401, request=request, json={})
        raise anthropic.APIStatusError("M102", response=response, body=None)

    monkeypatch.setattr(discovery_smart_search, "create_message", _raise_gateway_error)

    result = await discovery_smart_search.parse_smart_query("ai engineer intern")
    assert result.filters == DiscoverySearchRequest(title_keywords=["ai engineer intern"])
    assert "unavailable" in result.explanation.lower()


@pytest.mark.asyncio
async def test_invalid_json_reply_falls_back_to_the_raw_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(discovery_smart_search, "get_settings", lambda: _FakeSettings())

    async def _garbage_reply(*_args: Any, **_kwargs: Any) -> str:
        return "not json at all"

    monkeypatch.setattr(discovery_smart_search, "create_message", _garbage_reply)
    monkeypatch.setattr(discovery_smart_search, "response_text", lambda _msg: "not json at all")

    result = await discovery_smart_search.parse_smart_query("data engineer new grad")
    assert result.filters == DiscoverySearchRequest(title_keywords=["data engineer new grad"])
