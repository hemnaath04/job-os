"""The reviewer and the editor must actually receive the evidence vault.

Both had a hole. `/resumes/render-review` never passed `verified_facts` at all,
so the reviewer graded the candidate's own verified history as unverified: on one
real tailored document the identical resume scored 21.0 blind and 60.0 with the
facts, a 39-point swing carried by three false blocking issues. `revise_resume`
did pass them, but raw and truncated at 18,000 characters against a 40,179
character profile, which cut 39 skill facts, 3 certifications, 3 projects, an
education entry and a job out of the middle of the JSON.

These tests pin the plumbing, not the model: what reaches the prompt, and what
reaches `review_resume`.
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
from job_os.schemas.resumes import ResumeRenderReviewRequest  # noqa: E402
from job_os.services import resume_engine  # noqa: E402

DOC = {
    "basics": {"name": "A Candidate", "email": "a@b.com", "phone": "555-0100"},
    "work": [
        {
            "name": "Acme",
            "position": "Engineer",
            "highlights": ["Wrote the pricing test suite."],
        }
    ],
    "projects": [{"name": "One", "highlights": ["Built a scheduler."]}],
}

# Shaped like the real vault: a handful of substantive facts and a long tail of
# skill facts, which is exactly the tail a naive truncation eats.
FACTS: list[dict[str, Any]] = [
    {
        "kind": "experience",
        "title": "Engineer",
        "org": "Acme",
        "payload": {"description": "x" * 400},
        "bullets": [{"text": "Wrote the pricing test suite."}],
    },
    *[
        {
            "kind": "skill",
            "title": f"Skill number {index}",
            "org": "Category",
            "payload": {"category": "Category", "notes": "y" * 400},
            "bullets": [],
        }
        for index in range(60)
    ],
]


def _capture_prompt(monkeypatch: pytest.MonkeyPatch, reply: dict[str, Any]) -> list[Any]:
    calls: list[Any] = []

    class FakeMessages(StreamingFakeMessages):
        async def create(self, **kwargs: Any) -> Any:
            calls.append(kwargs)
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text=json.dumps(reply))]
            )

    monkeypatch.setattr(
        resume_engine, "_client", lambda: SimpleNamespace(messages=FakeMessages())
    )

    async def no_github(*_a: Any, **_k: Any) -> Any:
        return {}, [], []

    monkeypatch.setattr(resume_engine, "load_github_context", no_github)
    return calls


def test_the_render_review_request_can_carry_the_vault() -> None:
    # The endpoint is stateless and Appwrite-tailored resumes are not in this
    # database, so the caller is the only party that can supply the evidence.
    request = ResumeRenderReviewRequest(json_resume=DOC, verified_facts=FACTS[:1])
    assert request.verified_facts == FACTS[:1]
    # Optional, so an older client still gets a review rather than a 422.
    assert ResumeRenderReviewRequest(json_resume=DOC).verified_facts is None


@pytest.mark.asyncio
async def test_every_verified_fact_reaches_the_revise_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _capture_prompt(
        monkeypatch,
        {"assistant_message": "Done.", "suggestions": [], "json_resume": DOC},
    )
    await resume_engine.revise_resume(
        DOC, message="tighten the bullets", verified_facts=FACTS
    )
    prompt = calls[0]["messages"][0]["content"]
    # Raw, this payload is far past the 18,000 cut. Compacted, all of it lands.
    assert len(json.dumps(FACTS)) > 18000
    assert "Skill number 0" in prompt
    assert "Skill number 59" in prompt, "the tail of the vault was truncated away"


@pytest.mark.asyncio
async def test_the_number_guard_still_reads_the_full_uncompacted_vault(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Compacting is a prompt change only. The guard must see every real number.

    A metric that lives in a fact's payload rather than in a bullet is still
    verified, and shrinking the prompt must not turn it into a blocked claim.
    """
    facts = [
        {
            "kind": "experience",
            "title": "Engineer",
            "org": "Acme",
            "payload": {"description": "Cut the nightly suite to 12 minutes."},
            "bullets": [{"text": "Wrote the pricing test suite."}],
        }
    ]
    revised = json.loads(json.dumps(DOC))
    revised["work"][0]["highlights"] = ["Cut the nightly suite to 12 minutes."]
    _capture_prompt(
        monkeypatch,
        {"assistant_message": "Done.", "suggestions": [], "json_resume": revised},
    )
    output = await resume_engine.revise_resume(
        DOC, message="mention the suite runtime", verified_facts=facts
    )
    assert output.blocked_claims == []
    assert "12 minutes" in output.json_resume["work"][0]["highlights"][0]


@pytest.mark.asyncio
async def test_an_invented_number_is_still_blocked_after_compacting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revised = json.loads(json.dumps(DOC))
    revised["work"][0]["highlights"] = ["Cut the nightly suite by 91%."]
    _capture_prompt(
        monkeypatch,
        {"assistant_message": "Done.", "suggestions": [], "json_resume": revised},
    )
    output = await resume_engine.revise_resume(
        DOC, message="add a number", verified_facts=FACTS
    )
    assert output.blocked_claims
    assert "91%" not in json.dumps(output.json_resume)
