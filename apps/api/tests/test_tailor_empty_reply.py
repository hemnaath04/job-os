"""Recovering from a reply that came back with no text at all.

A real run against a strong-match JD produced two consecutive empty replies and
died with "Tailoring agent returned an invalid response". The answer had run past
the output ceiling, and the recovery attempt made the same request again with the
same ceiling plus a corrective note aimed at a chatty reply, which was not the
problem.
"""
from __future__ import annotations

import json
import os
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx
import pytest

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://job_os:job_os@localhost/job_os"
)

from job_os.services import tailor  # noqa: E402
from job_os.services.llm_json import EMPTY_REPLY_RETRY  # noqa: E402

from _fake_llm import StreamingFakeMessages


def _stub(monkeypatch: pytest.MonkeyPatch, replies: list[Any]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    class FakeMessages(StreamingFakeMessages):
        async def create(self, **kwargs: Any) -> Any:
            calls.append(kwargs)
            body = replies[min(len(calls) - 1, len(replies) - 1)]
            return SimpleNamespace(
                content=([SimpleNamespace(type="text", text=body)] if body else []),
                stop_reason="max_tokens" if not body else "end_turn",
                usage=SimpleNamespace(output_tokens=8192 if not body else 200),
            )

    class FakeAnthropic:
        def __init__(self, **_kwargs: Any) -> None:
            self.messages = FakeMessages()

    monkeypatch.setattr(
        tailor,
        "get_settings",
        lambda: SimpleNamespace(
            anthropic_api_key="test",
            anthropic_base_url="https://example.invalid",
            anthropic_model_tailor="manifest/auto",
            manifest_tier_sonnet="job-os-sonnet",
        ),
    )
    monkeypatch.setattr(anthropic, "AsyncAnthropic", FakeAnthropic)
    return calls


@pytest.mark.asyncio
async def test_an_empty_reply_is_retried_with_more_room_and_a_brevity_note(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    good = json.dumps({"ats_keywords_matched": [], "ats_keywords_missing": ["python"]})
    calls = _stub(monkeypatch, ["", good])

    _doc, _prov, _gaps, _score, report, _note = await tailor.run_tailor(
        facts=[],
        bullets_by_fact={},
        master_json_resume={"basics": {}},
        jd_parsed={},
        jd_clean="Python role",
    )

    # The run survived rather than raising.
    assert report["iterations"]
    retry = calls[1]
    assert retry["max_tokens"] > calls[0]["max_tokens"]
    assert retry["messages"][-1]["content"] == EMPTY_REPLY_RETRY
    # An empty turn is never echoed back to the model as its own words.
    assert all(
        message.get("content") != "(empty)" for message in retry["messages"]
    )


@pytest.mark.asyncio
async def test_a_chatty_reply_is_still_shown_its_own_words(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    good = json.dumps({"ats_keywords_matched": [], "ats_keywords_missing": ["python"]})
    calls = _stub(monkeypatch, ["Sure! Here is the plan:", good])

    await tailor.run_tailor(
        facts=[],
        bullets_by_fact={},
        master_json_resume={"basics": {}},
        jd_parsed={},
        jd_clean="Python role",
    )
    retry_messages = calls[1]["messages"]
    assert retry_messages[-2] == {
        "role": "assistant",
        "content": "Sure! Here is the plan:",
    }


@pytest.mark.asyncio
async def test_a_rate_limit_on_a_later_pass_keeps_the_passes_that_worked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real run reached 78.3 over three good passes and lost all of it to a 429.

    A refine pass improves on something that already works, so a transient gateway
    failure on one must ship the best pass so far rather than failing the call.
    """
    good = json.dumps({"ats_keywords_matched": ["python"], "ats_keywords_missing": []})
    calls: list[dict[str, Any]] = []

    class FakeMessages(StreamingFakeMessages):
        async def create(self, **kwargs: Any) -> Any:
            calls.append(kwargs)
            # The analyst call comes first and is not a pass, so counting writing
            # passes rather than calls is what keeps this test about the thing it
            # is named after.
            writing_passes = sum(
                1 for call in calls if "analyst step" not in call["system"]
            )
            if writing_passes >= 2:
                raise anthropic.RateLimitError(
                    "Rate limited by upstream provider",
                    response=httpx.Response(
                        429, request=httpx.Request("POST", "https://example.invalid")
                    ),
                    body=None,
                )
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text=good)],
                stop_reason="end_turn",
                usage=SimpleNamespace(output_tokens=200),
            )

    class FakeAnthropic:
        def __init__(self, **_kwargs: Any) -> None:
            self.messages = FakeMessages()

    monkeypatch.setattr(
        tailor,
        "get_settings",
        lambda: SimpleNamespace(
            anthropic_api_key="test",
            anthropic_base_url="https://example.invalid",
            anthropic_model_tailor="manifest/auto",
            manifest_tier_sonnet="job-os-sonnet",
        ),
    )
    monkeypatch.setattr(anthropic, "AsyncAnthropic", FakeAnthropic)

    document, _prov, _gaps, _score, report, _note = await tailor.run_tailor(
        facts=[],
        bullets_by_fact={},
        master_json_resume={"basics": {"name": "A Candidate"}},
        jd_parsed={"technologies": ["Python"]},
        jd_clean="Python role",
    )
    # The run completed on the strength of pass one instead of raising.
    assert len(report["iterations"]) == 1
    assert document["basics"]["name"] == "A Candidate"


@pytest.mark.asyncio
async def test_a_rate_limit_on_the_very_first_pass_still_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With nothing to ship, the caller has to hear about it."""

    class FakeMessages(StreamingFakeMessages):
        async def create(self, **_kwargs: Any) -> Any:
            raise anthropic.RateLimitError(
                "Rate limited by upstream provider",
                response=httpx.Response(
                    429, request=httpx.Request("POST", "https://example.invalid")
                ),
                body=None,
            )

    class FakeAnthropic:
        def __init__(self, **_kwargs: Any) -> None:
            self.messages = FakeMessages()

    monkeypatch.setattr(
        tailor,
        "get_settings",
        lambda: SimpleNamespace(
            anthropic_api_key="test",
            anthropic_base_url="https://example.invalid",
            anthropic_model_tailor="manifest/auto",
            manifest_tier_sonnet="job-os-sonnet",
        ),
    )
    monkeypatch.setattr(anthropic, "AsyncAnthropic", FakeAnthropic)

    with pytest.raises(anthropic.RateLimitError):
        await tailor.run_tailor(
            facts=[],
            bullets_by_fact={},
            master_json_resume={"basics": {}},
            jd_parsed={},
            jd_clean="Python role",
        )
