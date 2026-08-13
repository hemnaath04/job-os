"""The compose, measure, repair loop, against a fake gateway.

Every LLM reply here is canned. What is under test is what Python does with one:
which of two passes ships, what the repair turn is told, and that the decision is
made from the assembled letter rather than from the model's account of it.
"""
from __future__ import annotations

import json
import os
from types import SimpleNamespace
from typing import Any

import anthropic
import pytest

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://job_os:job_os@localhost/job_os"
)

from _cover_letter_fixtures import (
    BEDROCKED_SCORE,
    EPAM_TESTS,
    JD_PARSED,
    MASTER_RESUME,
    vault,
)
from _fake_llm import StreamingFakeMessages
from job_os.services import cover_letter  # noqa: E402

SETTINGS = SimpleNamespace(
    anthropic_api_key="test",
    anthropic_base_url="https://example.invalid",
    anthropic_model_tailor="manifest/auto",
    manifest_tier_sonnet="job-os-sonnet",
)


def _install(
    monkeypatch: pytest.MonkeyPatch, replies: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Serve `replies` in order, and hand back the recorded calls."""
    calls: list[dict[str, Any]] = []

    class FakeMessages(StreamingFakeMessages):
        async def create(self, **kwargs: Any) -> Any:
            calls.append(kwargs)
            payload = replies[min(len(calls) - 1, len(replies) - 1)]
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text=json.dumps(payload))]
            )

    class FakeAnthropic:
        def __init__(self, **_kwargs: Any) -> None:
            self.messages = FakeMessages()

    monkeypatch.setattr(cover_letter, "get_settings", lambda: SETTINGS)
    monkeypatch.setattr(anthropic, "AsyncAnthropic", FakeAnthropic)
    return calls


async def _run(**kwargs: Any):
    facts, bullets = vault()
    return await cover_letter.run_cover_letter(
        facts=facts,
        bullets_by_fact=bullets,
        master_json_resume=MASTER_RESUME,
        jd_parsed=JD_PARSED,
        jd_clean="Backend platform role. Python, test automation, Kubernetes.",
        company="Corvus Systems",
        role="Backend Engineer, Platform",
        **kwargs,
    )


def _paragraph(sentences: list[dict[str, Any]]) -> dict[str, Any]:
    return {"sentences": sentences}


def _claim(text: str, bullet_id: str | None = None) -> dict[str, Any]:
    return {"text": text, "fact_bullet_id": bullet_id}


@pytest.mark.asyncio
async def test_a_pass_whose_claims_were_deleted_earns_a_repair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The repair turn names the refusal, in the model's own words back at it.

    A pass that lost a claim to a guard has something a repair can genuinely fix,
    which is the only reason to spend another call. The turn has to carry the
    reason and the sentence, or the second pass is a re-roll rather than a fix.
    """
    replies = [
        {
            "opening": _paragraph([_claim("I am applying for the platform role.")]),
            "body": [
                _paragraph(
                    [
                        _claim(
                            "I wrote Python suites that cut failures by 40%.",
                            EPAM_TESTS,
                        )
                    ]
                )
            ],
            "closing": _paragraph([_claim("I would welcome a conversation.")]),
            "agent_note": "first pass",
        },
        {
            "opening": _paragraph([_claim("I am applying for the platform role.")]),
            "body": [
                _paragraph(
                    [
                        _claim(
                            "I wrote Python and Go suites against a rideshare "
                            "pricing engine and triaged the daily failures.",
                            EPAM_TESTS,
                        )
                    ]
                ),
                _paragraph(
                    [
                        _claim(
                            "I scored 2,404 sewer segments on a six-factor index.",
                            BEDROCKED_SCORE,
                        )
                    ]
                ),
            ],
            "closing": _paragraph([_claim("I would welcome a conversation.")]),
            "agent_note": "repaired",
        },
    ]
    calls = _install(monkeypatch, replies)
    result = await _run()

    assert len(calls) == 2
    assert calls[0]["extra_headers"] == {"x-manifest-tier": "job-os-sonnet"}
    repair_turn = calls[1]["messages"][-1]["content"]
    assert "unverified_number(40%)" in repair_turn
    assert "cut failures by 40%" in repair_turn
    # The repaired pass lost nothing, so it is the one that ships.
    assert result.refused == []
    assert len(result.provenance) == 2
    assert result.agent_note == "repaired"
    assert result.passes == 2


@pytest.mark.asyncio
async def test_the_first_pass_ships_when_the_repair_is_worse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keeping the later pass regardless is how a worse rewrite wins a tie.

    The comparison is deterministic and made from the assembled letters:
    refusals first, then measured writing problems. The model does not get to
    tell us which of its own attempts was better.
    """
    good_body = [
        _paragraph(
            [
                _claim(
                    "I wrote Python and Go suites against a rideshare pricing "
                    "engine and triaged the daily failures.",
                    EPAM_TESTS,
                )
            ]
        ),
        _paragraph(
            [
                _claim(
                    "I scored 2,404 sewer segments on a six-factor index.",
                    BEDROCKED_SCORE,
                )
            ]
        ),
    ]
    replies = [
        {
            "opening": _paragraph([_claim("I am applying for the platform role.")]),
            "body": good_body,
            "closing": _paragraph([_claim("I would welcome a conversation.")]),
            "agent_note": "first pass",
        },
        {
            "opening": _paragraph([_claim("I am thrilled to apply.")]),
            "body": [
                _paragraph(
                    [_claim("I shipped an agent nobody approved.", EPAM_TESTS)]
                )
            ],
            "closing": _paragraph([_claim("I would welcome a conversation.")]),
            "agent_note": "second pass",
        },
    ]
    _install(monkeypatch, replies)
    result = await _run()

    assert result.agent_note == "first pass"
    assert result.refused == []
    assert len(result.provenance) == 2


@pytest.mark.asyncio
async def test_a_clean_pass_costs_one_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """No refusals and no flags means nothing a second call could fix.

    Worth asserting rather than assuming: a loop that always spends its budget
    doubles the cost and the wait of every letter for a coin flip.
    """
    # The letter has to land inside the word band as well as pass the guards, or
    # `thin_letter` fires and there IS something a second call could fix, which
    # is a different test from this one. Ten words a repetition: 7 + 230 + 15 + 5
    # is 257, comfortably inside 250 to 350. The repetitions are separate
    # sentences, so none of them trips the long-sentence flag either.
    filler = "I wrote and maintained the suites that covered that service. "
    replies = [
        {
            "opening": _paragraph([_claim("I am applying for the platform role.")]),
            "body": [
                _paragraph([_claim(filler * 23, EPAM_TESTS)]),
                _paragraph(
                    [
                        _claim(
                            "I scored 2,404 sewer segments on a six-factor index "
                            "and served the result from FastAPI.",
                            BEDROCKED_SCORE,
                        )
                    ]
                ),
            ],
            "closing": _paragraph([_claim("I would welcome a conversation.")]),
        }
    ]
    calls = _install(monkeypatch, replies)
    result = await _run()

    assert result.quality_flags == {}
    assert result.refused == []
    assert len(calls) == 1
    assert result.passes == 1


@pytest.mark.asyncio
async def test_the_brief_tells_the_model_which_requirements_are_out_of_reach(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Python settles coverage before the model is called, and says so.

    Kubernetes is required and absent from the vault. Telling the writer up front
    is what stops it spending a sentence gesturing at experience the candidate
    does not have, and the gap still reaches the user as a question.
    """
    replies = [
        {
            "opening": _paragraph([_claim("I am applying for the platform role.")]),
            "body": [
                _paragraph(
                    [
                        _claim(
                            "I wrote Python and Go suites against a rideshare "
                            "pricing engine and triaged the daily failures.",
                            EPAM_TESTS,
                        )
                    ]
                )
            ],
            "closing": _paragraph([_claim("I would welcome a conversation.")]),
        }
    ]
    calls = _install(monkeypatch, replies)
    result = await _run()

    brief = calls[0]["messages"][0]["content"]
    assert "REQUIREMENTS THE VAULT DOES NOT HOLD" in brief
    assert "Kubernetes" in brief.split("REQUIREMENTS THE VAULT DOES NOT HOLD")[1]
    assert [gap.requirement for gap in result.gap_questions] == ["Kubernetes"]


@pytest.mark.asyncio
async def test_the_system_prompt_carries_the_house_rules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The prompt is not the enforcement, but it is still part of the contract.

    Python refuses what the prompt forbids, so a rule missing here costs a wasted
    pass rather than a bad letter. Asserting on it keeps the two in step: every
    guard below has a sentence above telling the model about it.
    """
    replies = [
        {
            "opening": _paragraph([_claim("I am applying for the platform role.")]),
            "body": [],
            "closing": _paragraph([_claim("I would welcome a conversation.")]),
        }
    ]
    calls = _install(monkeypatch, replies)
    await _run()

    system = calls[0]["system"]
    # The career-ops rules are prefixed, exactly as the tailoring agent does it.
    assert "You are the resume quality gate" in system
    for rule in (
        "leveraged",
        "utilized",
        "spearheaded",
        "cutting-edge",
        "robust",
        "seamlessly",
        "facilitated",
        "enabled",
        "end-to-end",
        "No em dashes",
        "Never upgrade a fact's status",
        "Never \"we\" or \"our\"",
        "250 to 350 words",
        "Do not name a person",
    ):
        assert rule in system, rule
    # And the prompt itself obeys the global dash rule it is imposing.
    assert "—" not in system
    assert "–" not in system
