from __future__ import annotations

import json
import os
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import anthropic
import pytest

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://job_os:job_os@localhost/job_os",
)

from job_os.services import tailor  # noqa: E402


@pytest.mark.asyncio
async def test_tailor_langgraph_refines_until_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = [
        {
            "ats_keywords_matched": ["python"],
            "ats_keywords_missing": ["agents"],
            "agent_note": "first pass",
        },
        {
            "ats_keywords_matched": ["python", "agents"],
            "ats_keywords_missing": [],
            "agent_note": "refined",
        },
    ]
    calls: list[dict[str, Any]] = []

    class FakeMessages:
        async def create(self, **kwargs: Any) -> Any:
            calls.append(kwargs)
            payload = responses[len(calls) - 1]
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text=json.dumps(payload))]
            )

    class FakeAnthropic:
        def __init__(self, **_kwargs: Any) -> None:
            self.messages = FakeMessages()

    async def no_facts(*_args: Any, **_kwargs: Any) -> list[Any]:
        return []

    async def no_bullets(*_args: Any, **_kwargs: Any) -> dict[Any, Any]:
        return {}

    settings = SimpleNamespace(
        anthropic_api_key="test",
        anthropic_base_url="https://example.invalid",
        anthropic_model_tailor="manifest/auto",
        manifest_tier_quality="job-os-quality",
    )
    monkeypatch.setattr(tailor, "get_settings", lambda: settings)
    monkeypatch.setattr(anthropic, "AsyncAnthropic", FakeAnthropic)
    monkeypatch.setattr(tailor, "_load_verified_facts", no_facts)
    monkeypatch.setattr(tailor, "_load_bullets", no_bullets)

    session: Any = SimpleNamespace()
    user: Any = SimpleNamespace(id=uuid4())
    resume: Any = SimpleNamespace(id=uuid4())
    master_version: Any = SimpleNamespace(json_resume={"basics": {}})
    job: Any = SimpleNamespace(jd_parsed={}, jd_clean="Python agent role")
    result = await tailor.tailor_resume(
        session,
        user=user,
        resume=resume,
        master_version=master_version,
        job=job,
    )

    _, _, gaps, score, report, note = result
    assert len(calls) == 2
    assert calls[0]["extra_headers"] == {"x-manifest-tier": "job-os-quality"}
    assert score == 100
    assert report["iterations"] == [50.0, 100.0]
    assert gaps == []
    assert note == "refined\n(Hit target ATS 80 in 2 passes: 50 -> 100)"
