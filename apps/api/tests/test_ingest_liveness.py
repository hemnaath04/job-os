"""Token liveness: what each observation means, and when to look again.

Roughly a third of the bundled corpus is dead (measured: 158 of 440 sampled tokens
answered 404). The rules here are what stop the crawler spending a third of its
request budget relearning the same 404s every night, without throwing away tokens
that might come back.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from job_os.db.models.ingest import AtsBoardToken, TokenStatus
from job_os.ingest import liveness
from job_os.ingest.providers import BoardResult, BoardStatus, RawPosting

NOW = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)


def token(**overrides: object) -> AtsBoardToken:
    row = AtsBoardToken(
        provider=overrides.pop("provider", "greenhouse"),
        token=overrides.pop("token", "acme"),
    )
    # Server defaults do not apply to an object that was never flushed, so the
    # counters are seeded explicitly here.
    row.status = TokenStatus.UNKNOWN.value
    row.priority = 0
    row.checks_count = 0
    row.consecutive_failures = 0
    row.consecutive_empty = 0
    row.last_ok_at = None
    row.etag = None
    row.last_payload_bytes = None
    row.max_job_count = None
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


def result(status: BoardStatus, *, postings: int = 0, etag: str | None = None) -> BoardResult:
    return BoardResult(
        provider="greenhouse",
        token="acme",
        status=status,
        postings=[
            RawPosting(
                source="greenhouse",
                board_token="acme",
                external_id=str(i),
                title="Engineer",
                company_name="Acme Corp",
                source_url=f"https://example.test/{i}",
                jd_clean="Body",
            )
            for i in range(postings)
        ],
        etag=etag,
        bytes_fetched=1234,
    )


# ---------------------------------------------------------------------------
# status transitions
# ---------------------------------------------------------------------------


def test_a_board_with_postings_is_live() -> None:
    row = token()
    liveness.apply_result(row, result(BoardStatus.LIVE, postings=12), now=NOW)

    assert row.status == TokenStatus.LIVE.value
    assert row.last_ok_at == NOW
    assert row.last_job_count == 12
    assert row.max_job_count == 12
    assert row.consecutive_failures == 0


def test_the_company_name_is_learned_from_the_board() -> None:
    """The bulk corpus is tokens only. Greenhouse reports the employer's name, and
    learning it once stops dedupe and display falling back to a board slug."""
    row = token()
    assert row.company_name is None
    liveness.apply_result(row, result(BoardStatus.LIVE, postings=1), now=NOW)
    assert row.company_name == "Acme Corp"


def test_a_curated_name_is_not_overwritten_by_the_board() -> None:
    row = token(company_name="Acme Incorporated")
    liveness.apply_result(row, result(BoardStatus.LIVE, postings=1), now=NOW)
    assert row.company_name == "Acme Incorporated"


def test_one_404_marks_missing_but_does_not_retire() -> None:
    """A vendor outage can 404 a real board, so one observation is not proof."""
    row = token()
    liveness.apply_result(row, result(BoardStatus.MISSING), now=NOW)

    assert row.status == TokenStatus.MISSING.value
    assert row.consecutive_failures == 1


def test_repeated_404s_retire_the_token() -> None:
    row = token()
    for _ in range(liveness.RETIRE_AFTER_MISSING):
        liveness.apply_result(row, result(BoardStatus.MISSING), now=NOW)

    assert row.status == TokenStatus.RETIRED.value


def test_a_live_answer_resets_the_failure_streak() -> None:
    row = token(consecutive_failures=2, status=TokenStatus.MISSING.value)
    liveness.apply_result(row, result(BoardStatus.LIVE, postings=3), now=NOW)

    assert row.consecutive_failures == 0
    assert row.status == TokenStatus.LIVE.value


def test_empty_is_not_treated_as_dead() -> None:
    """A company between hiring rounds is not a dead token."""
    row = token()
    liveness.apply_result(row, result(BoardStatus.EMPTY), now=NOW)

    assert row.status == TokenStatus.EMPTY.value
    assert row.status != TokenStatus.MISSING.value
    assert row.last_job_count == 0


def test_a_board_that_has_produced_postings_stays_empty_forever() -> None:
    """SmartRecruiters cannot distinguish an unknown company from an idle one, so
    death is inferred from repetition. A board we have seen postings on is idle,
    however long that lasts, and must never be retired for it."""
    row = token(last_ok_at=NOW - timedelta(days=90))
    for _ in range(liveness.RETIRE_AFTER_EMPTY * 3):
        liveness.apply_result(row, result(BoardStatus.EMPTY), now=NOW)

    assert row.status == TokenStatus.EMPTY.value
    assert row.status != TokenStatus.RETIRED.value


def test_a_token_that_never_produced_anything_retires_after_enough_empties() -> None:
    """This is how the SmartRecruiters trap gets resolved: over time, not at once."""
    row = token(provider="smartrecruiters")
    for _ in range(liveness.RETIRE_AFTER_EMPTY):
        liveness.apply_result(row, result(BoardStatus.EMPTY), now=NOW)

    assert row.status == TokenStatus.RETIRED.value


def test_a_transport_error_does_not_change_what_we_believe_about_the_token() -> None:
    row = token(status=TokenStatus.LIVE.value, last_ok_at=NOW - timedelta(days=1))
    liveness.apply_result(
        row,
        BoardResult(provider="greenhouse", token="acme", status=BoardStatus.ERROR, error="timeout"),
        now=NOW,
    )
    assert row.status == TokenStatus.ERROR.value
    assert row.consecutive_failures == 1
    # The last good observation is untouched.
    assert row.last_ok_at == NOW - timedelta(days=1)


# ---------------------------------------------------------------------------
# conditional GET bookkeeping
# ---------------------------------------------------------------------------


def test_a_304_keeps_the_previous_verdict_and_etag() -> None:
    """A 304 proves the board is reachable but says nothing new about its contents."""
    row = token(status=TokenStatus.LIVE.value, etag='W/"cached"', last_job_count=42)
    liveness.apply_result(row, result(BoardStatus.NOT_MODIFIED), now=NOW)

    assert row.status == TokenStatus.LIVE.value
    assert row.etag == 'W/"cached"', "clearing it would force a full download next time"
    assert row.last_job_count == 42


def test_a_304_on_an_unknown_token_proves_it_is_reachable() -> None:
    row = token(status=TokenStatus.UNKNOWN.value, etag='W/"cached"')
    liveness.apply_result(row, result(BoardStatus.NOT_MODIFIED), now=NOW)
    assert row.status == TokenStatus.LIVE.value


def test_an_error_does_not_clear_the_etag() -> None:
    """Clearing it would make the next crawl download a payload it could have
    revalidated for nothing."""
    row = token(status=TokenStatus.LIVE.value, etag='W/"cached"')
    liveness.apply_result(
        row,
        BoardResult(provider="greenhouse", token="acme", status=BoardStatus.ERROR, error="boom"),
        now=NOW,
    )
    assert row.etag == 'W/"cached"'


def test_a_successful_fetch_stores_the_new_etag_and_size() -> None:
    row = token()
    liveness.apply_result(row, result(BoardStatus.LIVE, postings=2, etag='W/"new"'), now=NOW)
    assert row.etag == 'W/"new"'
    assert row.last_payload_bytes == 1234


# ---------------------------------------------------------------------------
# scheduling
# ---------------------------------------------------------------------------


def test_a_live_board_is_rechecked_sooner_than_a_dead_one() -> None:
    live = token()
    liveness.apply_result(live, result(BoardStatus.LIVE, postings=5), now=NOW)
    dead = token()
    liveness.apply_result(dead, result(BoardStatus.MISSING), now=NOW)

    assert live.next_check_after is not None and dead.next_check_after is not None
    assert live.next_check_after < dead.next_check_after


def test_curated_companies_are_rechecked_more_often() -> None:
    """They are what users actually search for."""
    curated = token(priority=100)
    liveness.apply_result(curated, result(BoardStatus.LIVE, postings=5), now=NOW)
    bulk = token(priority=0)
    liveness.apply_result(bulk, result(BoardStatus.LIVE, postings=5), now=NOW)

    assert curated.next_check_after is not None and bulk.next_check_after is not None
    assert curated.next_check_after < bulk.next_check_after


def test_a_persistently_failing_host_backs_off_to_the_dead_cadence() -> None:
    """Otherwise a broken host eats the retry budget every hour, forever."""
    row = token(consecutive_failures=liveness.ERROR_PATIENCE)
    when = liveness.next_check_at(
        row,
        TokenStatus.ERROR.value,
        now=NOW,
        consecutive_failures=liveness.ERROR_PATIENCE,
    )
    assert when == NOW + liveness.RECHECK_INTERVALS[TokenStatus.MISSING.value]


def test_a_first_failure_is_retried_soon() -> None:
    row = token()
    when = liveness.next_check_at(row, TokenStatus.ERROR.value, now=NOW, consecutive_failures=1)
    assert when == NOW + liveness.RECHECK_INTERVALS[TokenStatus.ERROR.value]


def test_every_status_has_a_recheck_interval() -> None:
    """A status with no interval would schedule None and never be crawled again."""
    for status in TokenStatus:
        assert status.value in liveness.RECHECK_INTERVALS


def test_a_retired_token_is_still_scheduled_far_out_rather_than_never() -> None:
    """Retiring is not deleting. A company that switches ATS vendor comes back, and
    a corpus that forgets it has to rediscover it from nothing."""
    row = token()
    for _ in range(liveness.RETIRE_AFTER_MISSING):
        liveness.apply_result(row, result(BoardStatus.MISSING), now=NOW)

    assert row.status == TokenStatus.RETIRED.value
    assert row.next_check_after is not None
    assert row.next_check_after > NOW + timedelta(days=30)


def test_check_count_increments_on_every_observation() -> None:
    row = token()
    for status in (BoardStatus.LIVE, BoardStatus.NOT_MODIFIED, BoardStatus.ERROR):
        liveness.apply_result(row, result(status, postings=1), now=NOW)
    assert row.checks_count == 3
    assert row.last_checked_at == NOW


@pytest.mark.parametrize(
    "status",
    [
        BoardStatus.LIVE,
        BoardStatus.EMPTY,
        BoardStatus.MISSING,
        BoardStatus.ERROR,
        BoardStatus.NOT_MODIFIED,
    ],
)
def test_every_outcome_schedules_a_next_check(status: BoardStatus) -> None:
    row = token()
    liveness.apply_result(
        row, result(status, postings=1 if status is BoardStatus.LIVE else 0), now=NOW
    )
    assert row.next_check_after is not None
    assert row.next_check_after > NOW
