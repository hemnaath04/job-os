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

from _fake_llm import StreamingFakeMessages
from job_os.services import tailor  # noqa: E402


@pytest.mark.asyncio
async def test_tailor_langgraph_repairs_a_pass_that_left_problems(
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

    class FakeMessages(StreamingFakeMessages):
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
        manifest_tier_sonnet="job-os-sonnet",
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
    # This JD parses to no requirements at all, so there is nothing for the
    # analyst to resolve and no analyst call is made. Both calls are writing
    # passes: the draft, then one repair, because the draft left a writing
    # problem a repair could have fixed.
    assert len(calls) == 2
    assert all("analyst step" not in call["system"] for call in calls)
    assert calls[0]["extra_headers"] == {"x-manifest-tier": "job-os-sonnet"}
    # The loop scores the document it would actually ship, not the model's own
    # account of how it did. Pass two claims every keyword matched, but the
    # assembled document is empty, so both passes score 0 internally and the
    # second pass ends the run by failing to improve on the first. The
    # CUSTOMER-FACING score is None, not 0: this JD parses to zero
    # requirements, and _finalize_ats_score reports that honestly rather than
    # as a confident 0% match (see test_ats_scoring.py's own coverage of that
    # function).
    assert score is None
    # An empty document has no keywords to cover and cannot fill a page, so both
    # passes come in at zero coverage minus the thin-page penalty.
    # -12: four flags at 3 points each against an intentionally empty document
    # (thin_page, missing_education, no_github_link, no_linkedin_link). The
    # number matters less than the two iterations being identical, which is the
    # subject here: the score comes from the rendered document, not from what
    # the pass claims about itself.
    assert report["iterations"] == [-12.0, -12.0]
    assert report["scoring"] == "no_scoreable_requirements"
    # The empty fixture also has no education entry and no links, which the
    # reader-side checks report alongside the thin page.
    assert report["writing_flags"] == {
        "page": ["thin_page(0 bullets)"],
        "education": ["missing_education"],
        "links": ["no_github_link", "no_linkedin_link"],
    }
    assert gaps == []
    # The repair pass did not beat the draft, so the draft is what ships. Keeping
    # the later pass regardless is how a padded rewrite used to win a tie.
    # The score and pass trail are not in the note text: both are already on
    # the page as `report["iterations"]` (asserted above) and the Job Match
    # ring, so the note only says what those two do not. This JD has no real
    # requirements at all, so the note explains the missing score instead of
    # a pass count that would not mean anything here.
    assert note == (
        "first pass\n(This job description named no requirements this score "
        "could check, so Keyword Match is not shown.)"
    )
    # The repair turn must hand back measurements, not the model's own numbers.
    refine_turn = calls[1]["messages"][-1]["content"]
    assert "not from your own" in refine_turn


@pytest.mark.asyncio
async def test_a_pass_cannot_raise_its_score_by_claiming_more_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The old loop scored the model's matched/missing counts against each other.

    Claiming a longer matched list was therefore the cheapest way to raise the
    score, and the loop learned to paste JD wording onto unrelated bullets. Now
    the same claim moves nothing, because the resume is what gets measured.
    """
    responses = [
        {"ats_keywords_matched": [], "ats_keywords_missing": ["python", "agents"]},
        {"ats_keywords_matched": ["python", "agents"], "ats_keywords_missing": []},
    ]
    calls: list[dict[str, Any]] = []

    class FakeMessages(StreamingFakeMessages):
        async def create(self, **kwargs: Any) -> Any:
            calls.append(kwargs)
            # The analyst runs first here, because neither requirement is worded
            # anywhere in an empty profile. It finds nothing and is not one of
            # the writing passes under test.
            if "analyst step" in kwargs["system"]:
                return SimpleNamespace(
                    content=[SimpleNamespace(type="text", text=json.dumps({}))]
                )
            writing_pass = sum(
                1 for call in calls if "analyst step" not in call["system"]
            )
            payload = responses[min(writing_pass - 1, len(responses) - 1)]
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text=json.dumps(payload))]
            )

    class FakeAnthropic:
        def __init__(self, **_kwargs: Any) -> None:
            self.messages = FakeMessages()

    settings = SimpleNamespace(
        anthropic_api_key="test",
        anthropic_base_url="https://example.invalid",
        anthropic_model_tailor="manifest/auto",
        manifest_tier_sonnet="job-os-sonnet",
    )
    monkeypatch.setattr(tailor, "get_settings", lambda: settings)
    monkeypatch.setattr(anthropic, "AsyncAnthropic", FakeAnthropic)

    _doc, _prov, _gaps, score, report, _note = await tailor.run_tailor(
        facts=[],
        bullets_by_fact={},
        master_json_resume={"basics": {}},
        jd_parsed={"technologies": ["Python", "agents"]},
        jd_clean="Python agent role",
    )
    assert score == 0
    # -12: four flags at 3 points each against an intentionally empty document
    # (thin_page, missing_education, no_github_link, no_linkedin_link). The
    # number matters less than the two iterations being identical, which is the
    # subject here: the score comes from the rendered document, not from what
    # the pass claims about itself.
    assert report["iterations"] == [-12.0, -12.0]
