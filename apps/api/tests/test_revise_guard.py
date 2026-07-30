"""What happens to a chat edit that tries to invent a number.

The guard itself is not negotiable and is unchanged: a metric no verified fact
supports never reaches the page. What changed is the experience around it. A real
edit spent two and a half minutes and came back as a 400 reading "The requested
edit introduced unverified metrics: 10.0, 8.39", discarding every honest
improvement in the same edit.
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

from job_os.services import resume_engine  # noqa: E402
from job_os.services.resume_engine import (  # noqa: E402
    REVISE_MAX_TOKENS,
    _strip_unverified_numbers,
)

from _fake_llm import StreamingFakeMessages  # noqa: E402

BEFORE = {
    "basics": {"name": "A Candidate", "summary": "Backend engineer."},
    "work": [
        {
            "name": "Acme",
            "position": "Engineer",
            "highlights": ["Wrote the pricing test suite.", "Migrated CI to Jenkins."],
        }
    ],
    "projects": [{"name": "One", "highlights": ["Shipped a scheduler."]}],
}


def _stub(monkeypatch: pytest.MonkeyPatch, revised: dict[str, Any]) -> list[Any]:
    calls: list[Any] = []

    class FakeMessages(StreamingFakeMessages):
        async def create(self, **kwargs: Any) -> Any:
            calls.append(kwargs)
            body = json.dumps(
                {
                    "assistant_message": "Tightened the bullets.",
                    "suggestions": ["Consider adding a metric."],
                    "json_resume": revised,
                }
            )
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=body)])

    monkeypatch.setattr(
        resume_engine, "_client", lambda: SimpleNamespace(messages=FakeMessages())
    )

    async def no_github(*_a: Any, **_k: Any) -> Any:
        return {}, [], []

    monkeypatch.setattr(resume_engine, "load_github_context", no_github)
    return calls


@pytest.mark.asyncio
async def test_a_clean_edit_applies_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    revised = {
        **BEFORE,
        "work": [
            {
                "name": "Acme",
                "position": "Engineer",
                "highlights": ["Wrote the pricing engine test suite.", "Migrated CI."],
            }
        ],
    }
    calls = _stub(monkeypatch, revised)
    output = await resume_engine.revise_resume(
        BEFORE, message="tighten the bullets", verified_facts=[]
    )
    assert output.blocked_claims == []
    assert output.assistant_message == "Tightened the bullets."
    assert output.json_resume["work"][0]["highlights"][0].startswith("Wrote the pricing")
    # One call, not two: the reply has room to land in a single request.
    assert len(calls) == 1
    assert calls[0]["max_tokens"] == REVISE_MAX_TOKENS


@pytest.mark.asyncio
async def test_the_honest_half_of_an_edit_survives_an_invented_metric(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exact production case, with a legitimate change in the same edit."""
    revised = {
        "basics": {"name": "A Candidate", "summary": "Backend engineer."},
        "work": [
            {
                "name": "Acme",
                "position": "Engineer",
                "highlights": [
                    # Honest rewording, keeps no new number.
                    "Wrote the pricing engine test suite.",
                    # Invents a metric nothing supports.
                    "Migrated CI to Jenkins, cutting build time by 8.39 minutes.",
                ],
            }
        ],
        "projects": [{"name": "One", "highlights": ["Shipped a scheduler."]}],
    }
    _stub(monkeypatch, revised)
    output = await resume_engine.revise_resume(
        BEFORE, message="add build time numbers", verified_facts=[]
    )

    highlights = output.json_resume["work"][0]["highlights"]
    # The invented claim is gone.
    assert not any("8.39" in h for h in highlights)
    # The honest improvement in the same edit is kept.
    assert "Wrote the pricing engine test suite." in highlights
    # And the user is told exactly what was dropped and how to proceed.
    assert output.blocked_claims
    assert output.blocked_claims[0].metric == "8.39"
    assert "verified fact" in output.blocked_claims[0].remedy
    assert "8.39" in output.assistant_message
    assert "Profile" in output.assistant_message


@pytest.mark.asyncio
async def test_the_edit_is_never_returned_carrying_the_invented_number(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole point of the guard, restated as a test over the returned document."""
    revised = {
        "basics": {"name": "A Candidate", "summary": "Engineer with a 10.0 GPA."},
        "work": BEFORE["work"],
        "projects": BEFORE["projects"],
    }
    _stub(monkeypatch, revised)
    output = await resume_engine.revise_resume(
        BEFORE, message="mention my GPA", verified_facts=[]
    )
    assert "10.0" not in json.dumps(output.json_resume)
    # A scalar field reverts to the wording it already had, which was verified.
    assert output.json_resume["basics"]["summary"] == "Backend engineer."


def test_an_entry_stripped_of_every_bullet_gets_its_originals_back() -> None:
    revised = {
        "work": [
            {
                "name": "Acme",
                "highlights": ["Cut latency by 42 percent.", "Served 99.9 requests."],
            }
        ]
    }
    original = {"work": [{"name": "Acme", "highlights": ["Wrote the test suite."]}]}
    cleaned, blocked = _strip_unverified_numbers(
        revised, original=original, unsupported={"42", "99.9"}
    )
    assert len(blocked) == 2
    # Better an untouched role than a blank one.
    assert cleaned["work"][0]["highlights"] == ["Wrote the test suite."]
