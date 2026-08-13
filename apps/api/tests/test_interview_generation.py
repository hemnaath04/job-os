"""Generating a whole pack for one real posting.

The fixture is a real backend/AI posting and the candidate's real verified
vault, so what these cases assert is what a user actually receives: the caps
hold, a duplicate question does not reach them twice, the model's opinion of how
ready they are never becomes the grade, and a model call that fails still leaves
them with the half of the pack that never needed a model.

No network. The fake speaks the streaming API, because production streams every
request and a fake that only implements `create` would pass while production was
broken.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import anthropic
import pytest

from _fake_llm import StreamingFakeMessages
from _interview_fixtures import JD_CLEAN, JD_PARSED, vault
from job_os.services import interview_prep
from job_os.services.interview_prep import ResumeBullet, generate_prep

RESUME_BULLETS = [
    ResumeBullet(
        section="work",
        text=(
            "Worked on a Go and Python automated test suite for a rideshare client's "
            "pricing engine."
        ),
        fact_bullet_id="b-epam-go",
        fact_id="fact-epam",
    ),
    ResumeBullet(
        section="projects",
        text="Built a FastAPI service over PostgreSQL that scores job postings.",
        fact_bullet_id="b-js-api",
        fact_id="fact-jobsearcher",
    ),
]

REPLY = {
    "technical": [
        {
            "question": (
                "Your scoring service reads and writes PostgreSQL on every request. "
                "Where would it fall over first under ten times the traffic?"
            ),
            "topic": "PostgreSQL",
            "difficulty": "core",
            "why_asked": "The posting owns services backed by PostgreSQL.",
        },
        {
            "question": "How would you roll out a change to a service running on Kubernetes?",
            "topic": "Kubernetes",
            "difficulty": "stretch",
            "why_asked": "Named as a must-have and absent from the vault.",
        },
    ],
    "behavioral": [
        {
            "question": "Tell me about a time you owned an ambiguous piece of work.",
            "topic": "ownership",
            "difficulty": "core",
            "why_asked": "The posting asks for comfort owning ambiguous work end to end.",
            "fact_bullet_ids": ["b-js-api"],
            "scaffold": {
                "situation": "I was searching for roles and reading every posting by hand.",
                "task": "I wanted a ranked list instead of a feed.",
                "action": "I built a FastAPI service over PostgreSQL that does the scoring.",
                "result": "It serves the ranked list.",
            },
        },
        {
            "question": "Describe a time you disagreed with a senior engineer.",
            "topic": "conflict",
            "difficulty": "core",
            "why_asked": "Cross-team work is named in the responsibilities.",
        },
    ],
    "resume_probes": [
        {
            "question": (
                "Your resume says you worked on the Go test suite. What part was yours, "
                "and what would you do differently now?"
            ),
            "topic": "EPAM Go suite",
            "difficulty": "core",
            "why_asked": "The scope of 'worked on' is the first thing a reader tests.",
            "fact_bullet_ids": ["b-epam-go"],
            "scaffold": {
                "situation": "The pricing engine suite was flaky.",
                "action": "I investigated failures and fixed the flaky tests.",
                "result": "I was given the team lead role for it.",
            },
        }
    ],
    "candidate_asks": [
        {
            "question": (
                "How do the platform team and the data scientists split ownership of "
                "an LLM feature once it is live?"
            ),
            "topic": "team split",
            "why_asked": "The posting describes both, and the seam is where the work is.",
        }
    ],
    "readiness_estimate": 95,
    "note": "Focus on Kubernetes.",
}


def _fake_anthropic(reply: Any, calls: list[dict[str, Any]] | None = None):
    class FakeMessages(StreamingFakeMessages):
        async def create(self, **kwargs: Any) -> Any:
            if calls is not None:
                calls.append(kwargs)
            text = reply if isinstance(reply, str) else json.dumps(reply)
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])

    class FakeAnthropic:
        def __init__(self, **_kwargs: Any) -> None:
            self.messages = FakeMessages()

    return FakeAnthropic


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        anthropic_api_key="test",
        anthropic_base_url="https://example.invalid",
        anthropic_model_tailor="claude-opus-4-8",
        manifest_tier_sonnet="job-os-sonnet",
    )


async def _run(monkeypatch: pytest.MonkeyPatch, reply: Any, calls=None):
    monkeypatch.setattr(interview_prep, "get_settings", _settings)
    monkeypatch.setattr(anthropic, "AsyncAnthropic", _fake_anthropic(reply, calls))
    facts, bullets = vault()
    return await generate_prep(
        jd_parsed=JD_PARSED,
        jd_clean=JD_CLEAN,
        job_title="Software Engineer, Backend and AI Platform",
        company_name="Northwind Data",
        facts=facts,
        bullets_by_fact=bullets,
        resume_bullets=RESUME_BULLETS,
    )


@pytest.mark.asyncio
async def test_a_pack_for_a_real_posting_covers_all_four_categories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = await _run(monkeypatch, REPLY)
    categories = {question.category for question in result.questions}
    assert categories == {"technical", "behavioral", "resume_probe", "candidate_ask"}
    assert result.readiness.score is not None
    assert "scaffold built from verified evidence" in result.note


@pytest.mark.asyncio
async def test_every_scaffold_in_the_pack_cites_verified_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The invariant, asserted over a whole generated pack rather than one answer."""
    result = await _run(monkeypatch, REPLY)
    scaffolded = [q for q in result.questions if q.scaffold is not None]
    assert scaffolded
    for question in scaffolded:
        assert question.evidence, question.question
        assert all(citation.fact_id for citation in question.evidence)
        assert not question.gap


@pytest.mark.asyncio
async def test_a_competency_the_vault_cannot_answer_is_returned_as_a_gap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The conflict question cites nothing, so it is a gap and not a story."""
    result = await _run(monkeypatch, REPLY)
    conflict = next(q for q in result.questions if q.topic == "conflict")
    assert conflict.gap
    assert conflict.scaffold is None
    assert conflict.gap_note and "verified profile" in conflict.gap_note


@pytest.mark.asyncio
async def test_an_invented_promotion_is_stripped_from_a_resume_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """"I was given the team lead role" is not in any verified bullet."""
    result = await _run(monkeypatch, REPLY)
    probe = next(q for q in result.questions if q.category == "resume_probe")
    assert probe.scaffold is not None
    assert "team lead" not in probe.scaffold.joined()
    assert probe.removed_claims


@pytest.mark.asyncio
async def test_the_models_own_readiness_number_is_never_the_grade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """It answered 95 against a vault with a real Kubernetes gap.

    Carried as context and marked as the model's estimate. The grade stays the
    number Python derived, which is the same separation `ResumeReviewResult`
    makes between `score` and `model_estimate`.
    """
    result = await _run(monkeypatch, REPLY)
    assert result.readiness.model_estimate == 95
    assert result.readiness.score != 95
    assert result.readiness.score is not None


@pytest.mark.asyncio
async def test_the_prompt_hands_over_the_briefing_and_the_gateway_tier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    await _run(monkeypatch, REPLY, calls)
    assert len(calls) == 1
    call = calls[0]
    assert call["extra_headers"]["x-manifest-tier"] == "job-os-sonnet"
    prompt = call["messages"][0]["content"]
    assert "BACKED BY VERIFIED EVIDENCE" in prompt
    assert "b-js-api" in prompt  # the ids it is allowed to cite
    assert "Northwind Data" in prompt
    # The candidate's own boundaries ride along with the house rules.
    assert "EPAM Systems is the only professional employer" in call["system"]


@pytest.mark.asyncio
async def test_a_flood_of_questions_is_capped_and_deduplicated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A model that answers at length must not become a wall nobody reads."""
    flood = {
        "technical": [
            {"question": f"Question number {index}?", "topic": "Python"}
            for index in range(30)
        ]
        + [{"question": "Question number 0?", "topic": "Python"}],
        "behavioral": [],
        "resume_probes": [],
        "candidate_asks": [],
    }
    result = await _run(monkeypatch, flood)
    technical = [q for q in result.questions if q.category == "technical"]
    assert len(technical) == interview_prep.MAX_TECHNICAL
    assert len({q.question for q in technical}) == len(technical)
    assert [q.position for q in technical] == list(range(len(technical)))


@pytest.mark.asyncio
async def test_a_reply_that_is_not_json_is_asked_once_more(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    class FakeMessages(StreamingFakeMessages):
        async def create(self, **kwargs: Any) -> Any:
            calls.append(kwargs)
            text = (
                "Sure, here are some questions for you!"
                if len(calls) == 1
                else json.dumps(REPLY)
            )
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])

    class FakeAnthropic:
        def __init__(self, **_kwargs: Any) -> None:
            self.messages = FakeMessages()

    monkeypatch.setattr(interview_prep, "get_settings", _settings)
    monkeypatch.setattr(anthropic, "AsyncAnthropic", FakeAnthropic)
    facts, bullets = vault()
    result = await generate_prep(
        jd_parsed=JD_PARSED,
        jd_clean=JD_CLEAN,
        job_title="Software Engineer",
        facts=facts,
        bullets_by_fact=bullets,
    )
    assert len(calls) == 2
    # The retry names this schema's own fields. Handing a model instructions
    # about fields it was never asked for is how a retry produces a second
    # unusable reply.
    assert "resume_probes" in calls[1]["messages"][-1]["content"]
    assert result.questions


@pytest.mark.asyncio
async def test_a_dead_model_still_returns_the_half_that_needed_no_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The readiness report runs on rules, so it survives a failed call.

    A user who clicked generate and got an error page would learn nothing. A user
    who gets their gap list and an honest note about the missing questions can
    still prepare tonight.
    """

    class FakeMessages(StreamingFakeMessages):
        async def create(self, **_kwargs: Any) -> Any:
            raise anthropic.APIConnectionError(request=None)  # type: ignore[arg-type]

    class FakeAnthropic:
        def __init__(self, **_kwargs: Any) -> None:
            self.messages = FakeMessages()

    monkeypatch.setattr(interview_prep, "get_settings", _settings)
    monkeypatch.setattr(anthropic, "AsyncAnthropic", FakeAnthropic)
    facts, bullets = vault()
    result = await generate_prep(
        jd_parsed=JD_PARSED,
        jd_clean=JD_CLEAN,
        job_title="Software Engineer",
        facts=facts,
        bullets_by_fact=bullets,
    )
    assert result.questions == []
    assert result.readiness.score is not None
    assert result.readiness.topics
    assert "readiness report only" in result.note


@pytest.mark.asyncio
async def test_generation_never_writes_an_em_dash(monkeypatch: pytest.MonkeyPatch) -> None:
    reply = {
        "technical": [{"question": "How do you test a scoring service — end to end?"}],
        "behavioral": [],
        "resume_probes": [],
        "candidate_asks": [],
        "note": "Prepare Kubernetes — it is the gap.",
    }
    result = await _run(monkeypatch, reply)
    assert "—" not in result.questions[0].question
    assert "—" not in result.note
