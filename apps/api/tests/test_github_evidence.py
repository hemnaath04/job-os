"""The resume must not be marked down for our own GitHub configuration.

Deployed reviews carry `github_evidence_unavailable` warnings costing five points
each, while the identical fetch succeeds locally in 0.22s with every repo found.
The difference is GITHUB_TOKEN, which is set in neither deployed environment:
sixty unauthenticated requests an hour is nothing from a shared cloud IP.

Two things are pinned here. The token reaches the request when it is configured,
so provisioning it actually changes something. And a failure we caused is graded
differently from a repository that genuinely does not answer, because a reader
clicking a dead project link hits the same wall the reviewer did, while a rate
limit tells them nothing about the candidate.
"""
from __future__ import annotations

import os
from typing import Any

import httpx
import pytest

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://job_os:job_os@localhost/job_os"
)

from job_os.services import resume_engine  # noqa: E402
from job_os.services.resume_engine import (  # noqa: E402
    GITHUB_NOT_FOUND,
    GITHUB_RATE_LIMITED,
    GITHUB_UNAUTHORIZED,
    _github_failure_reason,
)

DOC = {
    "basics": {"name": "A Candidate", "email": "a@b.com", "phone": "555-0100"},
    "projects": [
        {"name": "BedRocked", "url": "https://github.com/hemnaath04/bedrocked"}
    ],
}


def _serve(handler: Any, monkeypatch: pytest.MonkeyPatch, *, token: str | None) -> None:
    from job_os.settings import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "github_token", token, raising=False)
    monkeypatch.setattr(
        resume_engine.httpx, "AsyncClient", lambda **kw: _FakeClient(handler, kw)
    )


class _FakeClient:
    def __init__(self, handler: Any, kwargs: dict[str, Any]) -> None:
        self._handler = handler
        self.headers = kwargs.get("headers") or {}

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False

    async def get(self, url: str) -> httpx.Response:
        return self._handler(url, self.headers)


def test_the_token_is_sent_when_it_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, str] = {}

    def handler(_url: str, headers: dict[str, str]) -> httpx.Response:
        seen.update(headers)
        return httpx.Response(404, request=httpx.Request("GET", _url))

    _serve(handler, monkeypatch, token="ghp_example")
    import asyncio

    asyncio.run(resume_engine.load_github_context(DOC))
    assert seen.get("Authorization") == "Bearer ghp_example"


def test_no_authorization_header_without_a_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, str] = {}

    def handler(_url: str, headers: dict[str, str]) -> httpx.Response:
        seen.update(headers)
        return httpx.Response(404, request=httpx.Request("GET", _url))

    _serve(handler, monkeypatch, token=None)
    import asyncio

    asyncio.run(resume_engine.load_github_context(DOC))
    assert "Authorization" not in seen


def test_an_exhausted_quota_is_told_apart_from_a_private_repo() -> None:
    # GitHub answers 403 for both. The remaining-quota header is what separates
    # them, and it decides whether the resume gets marked down.
    assert (
        _github_failure_reason(403, {"x-ratelimit-remaining": "0"})
        == GITHUB_RATE_LIMITED
    )
    assert (
        _github_failure_reason(403, {"x-ratelimit-remaining": "58"})
        == GITHUB_UNAUTHORIZED
    )
    assert _github_failure_reason(404, {}) == GITHUB_NOT_FOUND


@pytest.mark.asyncio
async def test_a_rate_limit_does_not_cost_the_resume_points(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(url: str, _headers: dict[str, str]) -> httpx.Response:
        return httpx.Response(
            403,
            headers={"x-ratelimit-remaining": "0"},
            request=httpx.Request("GET", url),
        )

    _serve(handler, monkeypatch, token=None)
    _contexts, _checked, missing = await resume_engine.load_github_context(DOC)
    assert missing == {"hemnaath04/bedrocked": GITHUB_RATE_LIMITED}


@pytest.mark.asyncio
async def test_a_dead_project_link_is_still_the_resume_s_problem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A reader clicking that link hits the same 404, so this one is worth points.
    def handler(url: str, _headers: dict[str, str]) -> httpx.Response:
        return httpx.Response(404, request=httpx.Request("GET", url))

    _serve(handler, monkeypatch, token="ghp_example")
    _contexts, _checked, missing = await resume_engine.load_github_context(DOC)
    assert missing == {"hemnaath04/bedrocked": GITHUB_NOT_FOUND}


@pytest.mark.asyncio
async def test_the_review_grades_the_two_cases_differently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from _fake_llm import StreamingFakeMessages

    class FakeMessages(StreamingFakeMessages):
        async def create(self, **_kwargs: Any) -> Any:
            body = '{"score": 90, "issues": [], "strengths": [], "summary": "Fine."}'
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=body)])

    monkeypatch.setattr(
        resume_engine, "_client", lambda: SimpleNamespace(messages=FakeMessages())
    )
    monkeypatch.setattr(
        resume_engine,
        "render_resume_pdf",
        lambda *_a, **_k: (_ for _ in ()).throw(
            resume_engine.TectonicUnavailableError("no engine")
        ),
    )

    async def rate_limited(*_a: Any, **_k: Any) -> Any:
        return {}, [], {"hemnaath04/bedrocked": GITHUB_RATE_LIMITED}

    async def not_found(*_a: Any, **_k: Any) -> Any:
        return {}, [], {"hemnaath04/bedrocked": GITHUB_NOT_FOUND}

    monkeypatch.setattr(resume_engine, "load_github_context", rate_limited)
    ours, _pdf = await resume_engine.review_resume(DOC, verified_facts=[])

    monkeypatch.setattr(resume_engine, "load_github_context", not_found)
    theirs, _pdf = await resume_engine.review_resume(DOC, verified_facts=[])

    def severity(result: Any) -> str:
        return next(
            issue.severity
            for issue in result.issues
            if issue.code == "github_evidence_unavailable"
        )

    assert severity(ours) == "suggestion"
    assert severity(theirs) == "warning"
    # And the difference shows up in the number, which is the point.
    assert ours.score > theirs.score
