"""The retry that fires when an edit comes back as prose.

It is a second full document generation, so it doubles a two-minute edit, and in
production it doubled the wait and then failed anyway: two jobs ended in "The
editor could not produce a usable revision" after 156s and 172s. Both were handed
their own "**Assistant message:** ..." back as an assistant turn, which
establishes prose as the format of the conversation, and both answered in prose
again.

So the retry now depends on what came back. Something object-shaped is worth
showing back, because the model can see what to fix. Pure prose is not.
"""
from __future__ import annotations

import json
import os
from types import SimpleNamespace
from typing import Any

import pytest

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://job_os:job_os@localhost/job_os"
)

from _fake_llm import StreamingFakeMessages  # noqa: E402
from job_os.services import resume_engine  # noqa: E402
from job_os.services.resume_engine import REVISION_FORMAT_RETRY  # noqa: E402

BEFORE = {
    "basics": {"name": "A Candidate", "summary": "Backend engineer."},
    "work": [
        {
            "name": "Acme",
            "position": "Engineer",
            "highlights": ["Wrote the pricing test suite."],
        }
    ],
    "projects": [{"name": "One", "highlights": ["Built a scheduler."]}],
}
GOOD = json.dumps(
    {
        "assistant_message": "Tightened the bullets.",
        "suggestions": [],
        "json_resume": BEFORE,
    }
)
# The exact shape that failed in production.
PROSE = (
    "**Assistant message:**\n\nI have reviewed the resume and I will run the "
    "Review action myself before making changes."
)
# A reply that tried to produce the object and got it slightly wrong.
MALFORMED = '{"assistant_message": "Done.", "suggestions": [], "json_resume": {'


def _stub(monkeypatch: pytest.MonkeyPatch, replies: list[str]) -> list[Any]:
    calls: list[Any] = []
    queue = list(replies)

    class FakeMessages(StreamingFakeMessages):
        async def create(self, **kwargs: Any) -> Any:
            calls.append(kwargs)
            text = queue.pop(0) if queue else GOOD
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])

    monkeypatch.setattr(
        resume_engine, "_client", lambda: SimpleNamespace(messages=FakeMessages())
    )

    async def no_github(*_a: Any, **_k: Any) -> Any:
        return {}, [], {}

    monkeypatch.setattr(resume_engine, "load_github_context", no_github)
    return calls


def _roles(call: Any) -> list[str]:
    return [m["role"] for m in call["messages"]]


def _contents(call: Any) -> list[str]:
    """The raw turn contents. Not json.dumps, which escapes the quotes and
    newlines these assertions are looking for and would pass vacuously."""
    return [str(m["content"]) for m in call["messages"]]


@pytest.mark.asyncio
async def test_a_prose_reply_is_not_quoted_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _stub(monkeypatch, [PROSE, GOOD])
    output = await resume_engine.revise_resume(
        BEFORE, message="tighten the bullets", verified_facts=[]
    )
    assert output.assistant_message == "Tightened the bullets."
    assert len(calls) == 2
    retry = calls[1]
    # No assistant turn: the conversation never learns that prose is acceptable.
    assert _roles(retry) == ["user", "user"]
    assert not any(PROSE in content for content in _contents(retry))
    assert not any("**Assistant message:**" in c for c in _contents(retry))
    assert retry["messages"][-1]["content"] == REVISION_FORMAT_RETRY


@pytest.mark.asyncio
async def test_a_half_built_object_is_shown_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Here the echo earns its place: the model can see what it got wrong.
    calls = _stub(monkeypatch, [MALFORMED, GOOD])
    await resume_engine.revise_resume(
        BEFORE, message="tighten the bullets", verified_facts=[]
    )
    assert _roles(calls[1]) == ["user", "assistant", "user"]
    assert MALFORMED in _contents(calls[1])


@pytest.mark.asyncio
async def test_a_clean_edit_still_costs_exactly_one_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _stub(monkeypatch, [GOOD])
    await resume_engine.revise_resume(
        BEFORE, message="tighten the bullets", verified_facts=[]
    )
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_two_failures_still_surface_as_a_usable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub(monkeypatch, [PROSE, PROSE])
    with pytest.raises(ValueError, match="could not produce a usable revision"):
        await resume_engine.revise_resume(
            BEFORE, message="tighten the bullets", verified_facts=[]
        )


@pytest.mark.asyncio
async def test_the_honesty_guard_still_runs_on_a_retried_edit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cheap path must not become a way around the number guard."""
    invented = json.loads(json.dumps(BEFORE))
    invented["work"][0]["highlights"] = ["Cut the nightly suite by 91%."]
    reply = json.dumps(
        {
            "assistant_message": "Added a metric.",
            "suggestions": [],
            "json_resume": invented,
        }
    )
    _stub(monkeypatch, [PROSE, reply])
    output = await resume_engine.revise_resume(
        BEFORE, message="add a number", verified_facts=[]
    )
    assert output.blocked_claims
    assert "91%" not in json.dumps(output.json_resume)
