"""`parse_jd`'s degraded paths: no key, invalid JSON, and a gateway failure.

Same shape as `test_discovery_smart_search.py` and the same underlying bug:
`jd_parse.py` called `client.messages.create` directly, bypassing
`create_message`'s retry and fallback schedule, so a Manifest outage raised
straight through the add-job-from-url/text flow instead of degrading to
"added without structured JD fields" the way the no-key branch already did.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import anthropic
import httpx
import pytest

from job_os.services import jd_parse


@dataclass
class _FakeSettings:
    anthropic_api_key: str | None = "manifest-key"
    anthropic_base_url: str | None = None
    manifest_tier_fast: str = "job-os-haiku"
    anthropic_model_extract: str = "manifest/auto"


# What a successful extraction looks like, for the tests whose subject is the
# retry and timeout schedule rather than what counts as an answer. It names a
# technology on purpose: a title alone is what the parser gets for free from
# `title_hint`, so a title-only reply is a failed parse and gets retried, which
# would make every test below count one call too many.
_GOOD_REPLY = '{"title": "Backend Engineer", "technologies": ["Go"]}'


@pytest.mark.asyncio
async def test_no_api_key_returns_just_the_title_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jd_parse, "get_settings", lambda: _FakeSettings(anthropic_api_key=None))
    result = await jd_parse.parse_jd("some jd text", title_hint="Backend Engineer")
    assert result == {"parse_incomplete": True, "title": "Backend Engineer"}


@pytest.mark.asyncio
async def test_gateway_failure_degrades_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(jd_parse, "get_settings", lambda: _FakeSettings())

    async def _raise_gateway_error(*_args: Any, **_kwargs: Any) -> None:
        request = httpx.Request("POST", "https://gateway.test/v1/messages")
        response = httpx.Response(401, request=request, json={})
        raise anthropic.APIStatusError("M102", response=response, body=None)

    monkeypatch.setattr(jd_parse, "create_message", _raise_gateway_error)

    result = await jd_parse.parse_jd("some jd text", title_hint="Backend Engineer")
    assert result == {"parse_incomplete": True, "title": "Backend Engineer"}


@pytest.mark.asyncio
async def test_gateway_failure_with_no_title_hint_returns_empty_dict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(jd_parse, "get_settings", lambda: _FakeSettings())

    async def _raise_gateway_error(*_args: Any, **_kwargs: Any) -> None:
        request = httpx.Request("POST", "https://gateway.test/v1/messages")
        response = httpx.Response(500, request=request, json={})
        raise anthropic.APIStatusError("boom", response=response, body=None)

    monkeypatch.setattr(jd_parse, "create_message", _raise_gateway_error)

    result = await jd_parse.parse_jd("some jd text")
    assert result == {"parse_incomplete": True}


class _FakeMessage:
    def __init__(self, text: str) -> None:
        self.content = [type("Block", (), {"type": "text", "text": text})()]


@pytest.mark.asyncio
async def test_a_single_timeout_is_retried_once_and_the_retry_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A normal-length JD hit this 3/3 for real in one session: a timeout on
    the first attempt should not immediately give up when one retry has a
    real chance of landing."""
    monkeypatch.setattr(jd_parse, "get_settings", lambda: _FakeSettings())
    slept: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(jd_parse, "_sleep", _fake_sleep)
    calls = 0

    async def _flaky(*_args: Any, **_kwargs: Any) -> _FakeMessage:
        nonlocal calls
        calls += 1
        if calls == 1:
            request = httpx.Request("POST", "https://gateway.test/v1/messages")
            raise anthropic.APITimeoutError(request=request)
        return _FakeMessage(_GOOD_REPLY)

    monkeypatch.setattr(jd_parse, "create_message", _flaky)

    result = await jd_parse.parse_jd("some jd text")
    assert calls == 2
    assert slept == [jd_parse._JD_PARSE_RETRY_DELAY_SECONDS]
    assert result["title"] == "Backend Engineer"
    assert result["parse_incomplete"] is False


@pytest.mark.asyncio
async def test_two_consecutive_timeouts_still_degrade_honestly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The retry reduces how often a user hits this, it does not remove the
    honest fallback: a second timeout in a row still reports incomplete
    rather than hanging or raising."""
    monkeypatch.setattr(jd_parse, "get_settings", lambda: _FakeSettings())

    async def _fake_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr(jd_parse, "_sleep", _fake_sleep)
    calls = 0

    async def _always_times_out(*_args: Any, **_kwargs: Any) -> _FakeMessage:
        nonlocal calls
        calls += 1
        request = httpx.Request("POST", "https://gateway.test/v1/messages")
        raise anthropic.APITimeoutError(request=request)

    monkeypatch.setattr(jd_parse, "create_message", _always_times_out)

    result = await jd_parse.parse_jd("some jd text", title_hint="Backend Engineer")
    assert calls == 2
    assert result == {"parse_incomplete": True, "title": "Backend Engineer"}


class _FakeUsage:
    def __init__(self, output_tokens: int) -> None:
        self.output_tokens = output_tokens


class _TruncatedMessage(_FakeMessage):
    """What the gateway returns in a degraded window: JSON cut off mid-value."""

    def __init__(self, text: str, output_tokens: int = 4096) -> None:
        super().__init__(text)
        self.stop_reason = "max_tokens"
        self.usage = _FakeUsage(output_tokens)


# A real one, copied from a live degraded run rather than invented.
TRUNCATED_JSON = (
    '{\n  "title": "Software Engineering Intern, Summer 2027",\n'
    '  "location": "New York, NY",\n  "salary_min": 52000,\n'
    '  "required_skills": ["Pytho'
)


@pytest.mark.asyncio
async def test_a_truncated_answer_is_retried_rather_than_accepted_as_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bug this covers reached a user: a JD with a location, a salary band
    and a skills list came back as "no details could be read from it", because
    the one reply arrived cut off mid-value and nothing tried again."""
    monkeypatch.setattr(jd_parse, "get_settings", lambda: _FakeSettings())
    calls = 0

    async def _truncated_then_whole(*_args: Any, **_kwargs: Any) -> _FakeMessage:
        nonlocal calls
        calls += 1
        if calls == 1:
            return _TruncatedMessage(TRUNCATED_JSON)
        return _FakeMessage(
            '{"title": "Software Engineering Intern", "location": "New York, NY", '
            '"required_skills": ["Python"]}'
        )

    monkeypatch.setattr(jd_parse, "create_message", _truncated_then_whole)

    result = await jd_parse.parse_jd("a full length jd")

    assert calls == 2
    assert result["title"] == "Software Engineering Intern"
    assert result["location"] == "New York, NY"
    assert result["parse_incomplete"] is False


@pytest.mark.asyncio
async def test_the_retry_does_not_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unlike a timeout, a truncated answer is not rate limiting, and the
    callers are interactive requests inside a hard 30s ceiling. Sleeping here
    would spend the budget the retry needs."""
    monkeypatch.setattr(jd_parse, "get_settings", lambda: _FakeSettings())
    slept: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(jd_parse, "_sleep", _fake_sleep)
    calls = 0

    async def _truncated_then_whole(*_args: Any, **_kwargs: Any) -> _FakeMessage:
        nonlocal calls
        calls += 1
        return _TruncatedMessage(TRUNCATED_JSON) if calls == 1 else _FakeMessage(_GOOD_REPLY)

    monkeypatch.setattr(jd_parse, "create_message", _truncated_then_whole)

    await jd_parse.parse_jd("a full length jd")
    assert slept == []


@pytest.mark.asyncio
async def test_two_truncated_answers_still_degrade_honestly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The retry lowers how often this is hit, it does not remove the honest
    fallback. Twice unusable still reports incomplete rather than inventing
    fields out of half an object."""
    monkeypatch.setattr(jd_parse, "get_settings", lambda: _FakeSettings())
    calls = 0

    async def _always_truncated(*_args: Any, **_kwargs: Any) -> _FakeMessage:
        nonlocal calls
        calls += 1
        return _TruncatedMessage(TRUNCATED_JSON)

    monkeypatch.setattr(jd_parse, "create_message", _always_truncated)

    result = await jd_parse.parse_jd("a full length jd", title_hint="Backend Engineer")

    assert calls == 2
    assert result == {"parse_incomplete": True, "title": "Backend Engineer"}


@pytest.mark.asyncio
async def test_an_empty_reply_is_retried_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """The other shape seen live: the reply had no text at all."""
    monkeypatch.setattr(jd_parse, "get_settings", lambda: _FakeSettings())
    calls = 0

    async def _empty_then_whole(*_args: Any, **_kwargs: Any) -> _FakeMessage:
        nonlocal calls
        calls += 1
        return _TruncatedMessage("") if calls == 1 else _FakeMessage(_GOOD_REPLY)

    monkeypatch.setattr(jd_parse, "create_message", _empty_then_whole)

    result = await jd_parse.parse_jd("a full length jd")
    assert calls == 2
    assert result["title"] == "Backend Engineer"


@pytest.mark.asyncio
async def test_a_whole_answer_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """The normal path stays one call. A second would double the cost and the
    latency of every import for nothing."""
    monkeypatch.setattr(jd_parse, "get_settings", lambda: _FakeSettings())
    calls = 0

    async def _whole(*_args: Any, **_kwargs: Any) -> _FakeMessage:
        nonlocal calls
        calls += 1
        return _FakeMessage(_GOOD_REPLY)

    monkeypatch.setattr(jd_parse, "create_message", _whole)

    result = await jd_parse.parse_jd("some jd text")
    assert calls == 1
    assert result["title"] == "Backend Engineer"


@pytest.mark.asyncio
async def test_the_token_ceiling_is_the_one_that_was_measured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """2048 was what a degraded reply ran out of. Pinned so a well-meaning
    trim back does not quietly reintroduce the truncation."""
    monkeypatch.setattr(jd_parse, "get_settings", lambda: _FakeSettings())
    seen: dict[str, Any] = {}

    async def _capture(*_args: Any, **kwargs: Any) -> _FakeMessage:
        seen.update(kwargs)
        return _FakeMessage(_GOOD_REPLY)

    monkeypatch.setattr(jd_parse, "create_message", _capture)

    await jd_parse.parse_jd("some jd text")
    assert seen["max_tokens"] == jd_parse._JD_PARSE_MAX_TOKENS
    assert jd_parse._JD_PARSE_MAX_TOKENS > 2048


class _Clock:
    """A monotonic clock a test can advance without waiting on one."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.mark.asyncio
async def test_the_retry_has_to_fit_the_time_the_caller_is_waiting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The regression this exists to stop.

    A first attempt that answers fast but unusably used to start a second one
    with no regard for how long the caller would wait. In production that
    turned a fast, honest empty parse into a 27 second wait for the same empty
    parse: the gateway answered 200 in about three seconds, the reply was cut
    off, the retry started, and the caller's deadline killed it 24 seconds
    later. With almost none of the budget left there is no second attempt to
    start, and the answer comes back now.
    """
    monkeypatch.setattr(jd_parse, "get_settings", lambda: _FakeSettings())
    clock = _Clock()
    monkeypatch.setattr(jd_parse, "_monotonic", clock)
    calls = 0

    async def _slow_and_truncated(*_args: Any, **_kwargs: Any) -> _FakeMessage:
        nonlocal calls
        calls += 1
        clock.advance(24.0)  # most of the budget gone on the first attempt
        return _TruncatedMessage(TRUNCATED_JSON)

    monkeypatch.setattr(jd_parse, "create_message", _slow_and_truncated)

    result = await jd_parse.parse_jd("a jd", title_hint="Backend Engineer")

    assert calls == 1, "a second attempt that cannot finish must not be started"
    assert result == {"parse_incomplete": True, "title": "Backend Engineer"}


@pytest.mark.asyncio
async def test_the_retry_still_runs_when_there_is_time_for_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bound must not cost us the retry in the case it was added for: a
    reply that comes back quickly and unusable leaves plenty of budget."""
    monkeypatch.setattr(jd_parse, "get_settings", lambda: _FakeSettings())
    clock = _Clock()
    monkeypatch.setattr(jd_parse, "_monotonic", clock)
    calls = 0

    async def _fast_then_whole(*_args: Any, **_kwargs: Any) -> _FakeMessage:
        nonlocal calls
        calls += 1
        clock.advance(3.0)  # what the gateway actually did in production
        if calls == 1:
            return _TruncatedMessage(TRUNCATED_JSON)
        return _FakeMessage(_GOOD_REPLY)

    monkeypatch.setattr(jd_parse, "create_message", _fast_then_whole)

    result = await jd_parse.parse_jd("a jd")

    assert calls == 2
    assert result["title"] == "Backend Engineer"


@pytest.mark.asyncio
async def test_the_first_attempt_leaves_room_for_the_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The first attempt gets half of what is left, not all of it.

    It used to get the whole budget, which meant a timeout spent every second
    there was and the retry could never run. Asserting the old behaviour is
    what let that ship: this test passed while the retry was unreachable.
    """
    monkeypatch.setattr(jd_parse, "get_settings", lambda: _FakeSettings())
    clock = _Clock()
    monkeypatch.setattr(jd_parse, "_monotonic", clock)
    seen: list[float] = []

    async def _capture(*_args: Any, **kwargs: Any) -> _FakeMessage:
        return _FakeMessage(_GOOD_REPLY)

    # Mirrors asyncio.wait_for's own signature, which is the point of the
    # double, so the timeout parameter is not ours to rename.
    async def _wait_for(coro: Any, timeout: float) -> Any:  # noqa: ASYNC109
        seen.append(timeout)
        return await coro

    monkeypatch.setattr(jd_parse, "create_message", _capture)
    monkeypatch.setattr(jd_parse.asyncio, "wait_for", _wait_for)

    await jd_parse.parse_jd("a jd", deadline_seconds=30.0)

    # (30 - 2 of backoff) / 2, so a second attempt has the same again.
    assert seen[0] == pytest.approx(14.0, abs=0.5)
    assert seen[0] < 30.0


@pytest.mark.asyncio
async def test_a_timed_out_first_attempt_still_gets_its_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bug this file is about, stated as the behaviour that was missing.

    In production (2026-08-27, request 5df6920c) attempt 1 spent the full 25s
    budget, the 2s backoff followed, and attempt 2 opened at remaining=-2.0 and
    returned at the out_of_time guard. The job saved as "Untitled" with nothing
    parsed. So: a first attempt that burns its whole slice must still leave a
    real second attempt behind it.
    """
    monkeypatch.setattr(jd_parse, "get_settings", lambda: _FakeSettings())
    clock = _Clock()
    monkeypatch.setattr(jd_parse, "_monotonic", clock)
    # The backoff spends budget too, so the clock has to feel it: that is the
    # 2 seconds that pushed the old attempt 2 to remaining=-2.0.
    async def _fake_sleep(seconds: float) -> None:
        clock.advance(seconds)

    monkeypatch.setattr(jd_parse, "_sleep", _fake_sleep)
    attempts: list[float] = []

    async def _capture(*_args: Any, **kwargs: Any) -> _FakeMessage:
        return _FakeMessage(_GOOD_REPLY)

    async def _wait_for(coro: Any, timeout: float) -> Any:  # noqa: ASYNC109
        attempts.append(timeout)
        coro.close()
        if len(attempts) == 1:
            # Spend exactly what this attempt was given, the way a real
            # timeout does, then advance the clock past it.
            clock.advance(timeout)
            raise TimeoutError
        return _FakeMessage(_GOOD_REPLY)

    monkeypatch.setattr(jd_parse, "create_message", _capture)
    monkeypatch.setattr(jd_parse.asyncio, "wait_for", _wait_for)

    result = await jd_parse.parse_jd("a jd", deadline_seconds=25.0)

    assert len(attempts) == 2, "the retry never ran"
    assert attempts[1] >= jd_parse._JD_PARSE_MIN_ATTEMPT_SECONDS
    assert result["title"] == "Backend Engineer"
    assert result["parse_incomplete"] is False


@pytest.mark.asyncio
async def test_a_short_budget_still_gives_one_usable_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Halving must not starve the first attempt when the caller is in a hurry.

    Below roughly 14s the half is under the minimum an attempt needs to land,
    and an attempt cut short before the gateway would have answered spends the
    budget without learning anything.
    """
    monkeypatch.setattr(jd_parse, "get_settings", lambda: _FakeSettings())
    monkeypatch.setattr(jd_parse, "_monotonic", _Clock())
    seen: list[float] = []

    async def _capture(*_args: Any, **kwargs: Any) -> _FakeMessage:
        return _FakeMessage(_GOOD_REPLY)

    async def _wait_for(coro: Any, timeout: float) -> Any:  # noqa: ASYNC109
        seen.append(timeout)
        return await coro

    monkeypatch.setattr(jd_parse, "create_message", _capture)
    monkeypatch.setattr(jd_parse.asyncio, "wait_for", _wait_for)

    await jd_parse.parse_jd("a jd", deadline_seconds=10.0)

    assert seen[0] == pytest.approx(jd_parse._JD_PARSE_MIN_ATTEMPT_SECONDS, abs=0.1)


@pytest.mark.asyncio
async def test_a_caller_can_hand_down_its_own_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """So the endpoint's budget and this one are one number, not two that
    drift apart. They drifted before: 30s here against 27s there."""
    monkeypatch.setattr(jd_parse, "get_settings", lambda: _FakeSettings())
    clock = _Clock()
    monkeypatch.setattr(jd_parse, "_monotonic", clock)
    calls = 0

    async def _burns_the_budget(*_args: Any, **_kwargs: Any) -> _FakeMessage:
        nonlocal calls
        calls += 1
        clock.advance(9.0)
        return _TruncatedMessage(TRUNCATED_JSON)

    monkeypatch.setattr(jd_parse, "create_message", _burns_the_budget)

    await jd_parse.parse_jd("a jd", deadline_seconds=10.0)
    assert calls == 1, "a 10s budget leaves no room for a second attempt"


@pytest.mark.asyncio
async def test_the_title_is_offered_to_the_parser(monkeypatch: pytest.MonkeyPatch) -> None:
    """A posting's heading routinely names the location and the company where
    the body never does. Parsing BNY's body alone returned location=None on
    five runs out of five against the real text."""
    monkeypatch.setattr(jd_parse, "get_settings", lambda: _FakeSettings())
    seen: dict[str, Any] = {}

    async def _capture(*_args: Any, **kwargs: Any) -> _FakeMessage:
        seen.update(kwargs)
        return _FakeMessage(_GOOD_REPLY)

    monkeypatch.setattr(jd_parse, "create_message", _capture)

    await jd_parse.parse_jd(
        "a body with no location in it",
        title_hint="Engineering (Developer) - New York, NY - BNY Careers",
    )

    prompt = seen["messages"][0]["content"]
    assert "New York, NY" in prompt


@pytest.mark.asyncio
async def test_a_reply_that_names_nothing_is_retried_then_reported_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`{}` validates. Every field on ParsedJD is optional with a default, so a
    bare object passes model_validate_json and dumps to six empty lists, two
    nulls and parse_incomplete False. That is the exact shape of a real parse
    of a JD asking for nothing, reported with the same confidence, through the
    one door _incomplete did not cover."""
    monkeypatch.setattr(jd_parse, "get_settings", lambda: _FakeSettings())
    monkeypatch.setattr(jd_parse, "_monotonic", _Clock())
    calls = 0

    async def _empty(*_args: Any, **_kwargs: Any) -> _FakeMessage:
        nonlocal calls
        calls += 1
        return _FakeMessage("{}")

    async def _fake_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr(jd_parse, "create_message", _empty)
    monkeypatch.setattr(jd_parse, "_sleep", _fake_sleep)

    result = await jd_parse.parse_jd("a jd", title_hint="Backend Engineer")

    assert calls == 2, "an empty answer deserves the same second chance a truncated one gets"
    # Not an exact-dict compare any more: a flagged parse keeps whatever it did
    # read, so the shape carries every field. What matters is that it is flagged
    # and that nothing scoreable was invented to fill the gap.
    assert result["parse_incomplete"] is True
    assert result["title"] == "Backend Engineer"
    assert result["required_skills"] == []
    assert result["technologies"] == []


@pytest.mark.asyncio
async def test_an_empty_first_reply_does_not_discard_a_good_second_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(jd_parse, "get_settings", lambda: _FakeSettings())
    monkeypatch.setattr(jd_parse, "_monotonic", _Clock())
    replies = iter(["{}", '{"title": "Backend Engineer", "keywords": ["python"]}'])

    async def _then_good(*_args: Any, **_kwargs: Any) -> _FakeMessage:
        return _FakeMessage(next(replies))

    async def _fake_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr(jd_parse, "create_message", _then_good)
    monkeypatch.setattr(jd_parse, "_sleep", _fake_sleep)

    result = await jd_parse.parse_jd("a jd")

    assert result["title"] == "Backend Engineer"
    assert result["parse_incomplete"] is False


@pytest.mark.asyncio
async def test_a_title_on_its_own_is_not_an_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """This asserted the opposite, and the opposite is what shipped the bug.

    The old claim was that "a posting that yielded only a title is still
    something the scorer can say it read". Six real postings in one workspace
    disagree: each is stored with a title, sometimes a seniority and a
    location, not one skill or qualification between them, and every one
    recorded as a successful parse. Two are not job pages at all, a Disney
    error page and a Greenhouse applications dashboard. The other four include
    NVIDIA's and Millennium's genuine postings, with 7KB and 15KB of
    description sitting unread in `jd_clean`.

    A title is the one thing the call gets for free: it is handed in as
    `title_hint`. Treating it as evidence the body was read is what let a
    failed extraction pass for a thin one, and cost the retry that would
    probably have fixed the real four.
    """
    monkeypatch.setattr(jd_parse, "get_settings", lambda: _FakeSettings())
    monkeypatch.setattr(jd_parse, "_monotonic", _Clock())
    calls = 0

    async def _thin(*_args: Any, **_kwargs: Any) -> _FakeMessage:
        nonlocal calls
        calls += 1
        return _FakeMessage('{"title": "Backend Engineer"}')

    async def _fake_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr(jd_parse, "create_message", _thin)
    monkeypatch.setattr(jd_parse, "_sleep", _fake_sleep)

    result = await jd_parse.parse_jd("a jd")

    assert calls == 2, "nothing scoreable came back, so the retry is worth spending"
    assert result["parse_incomplete"] is True


@pytest.mark.asyncio
async def test_one_scoreable_field_is_enough_to_count_as_an_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The emptiness check must not swallow a thin but real parse.

    The line is drawn at whether anything a resume can be measured against came
    back, not at how much. One technology is a real answer and is not retried.
    """
    monkeypatch.setattr(jd_parse, "get_settings", lambda: _FakeSettings())
    monkeypatch.setattr(jd_parse, "_monotonic", _Clock())
    calls = 0

    async def _thin(*_args: Any, **_kwargs: Any) -> _FakeMessage:
        nonlocal calls
        calls += 1
        return _FakeMessage('{"title": "Backend Engineer", "technologies": ["Go"]}')

    monkeypatch.setattr(jd_parse, "create_message", _thin)

    result = await jd_parse.parse_jd("a jd")

    assert calls == 1, "a thin answer is still an answer and must not be retried"
    assert result["parse_incomplete"] is False
    assert result["technologies"] == ["Go"]


@pytest.mark.asyncio
async def test_a_failed_parse_keeps_the_metadata_it_did_get_right(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flagged, not blanked.

    Millennium's posting came back with the company, the city and the seniority
    correct and no skills at all. Those three are what the board prints on the
    card, so reporting the parse honestly by discarding them would fix the
    score and break the display in the same move.
    """
    monkeypatch.setattr(jd_parse, "get_settings", lambda: _FakeSettings())
    monkeypatch.setattr(jd_parse, "_monotonic", _Clock())
    reply = (
        '{"title": "2027 Applied AI Engineer Intern", "company": "Millennium", '
        '"location": "Miami, Florida", "level": "intern"}'
    )

    async def _metadata_only(*_args: Any, **_kwargs: Any) -> _FakeMessage:
        return _FakeMessage(reply)

    async def _fake_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr(jd_parse, "create_message", _metadata_only)
    monkeypatch.setattr(jd_parse, "_sleep", _fake_sleep)

    result = await jd_parse.parse_jd("a jd")

    assert result["parse_incomplete"] is True
    assert result["company"] == "Millennium"
    assert result["location"] == "Miami, Florida"
    assert result["level"] == "intern"

