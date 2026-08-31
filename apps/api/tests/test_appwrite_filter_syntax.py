"""Filters handed to `appwrite_tables` have to be expressions it can parse.

`_parse_filter` accepts `attribute=value`, the same shape the ingest paths
already use. Appwrite's own Query JSON looks plausible next to it and is not
accepted: it raises `ValueError: Unsupported filter expression`.

That is exactly what shipped. The card sync and its backfill both passed
`equal("owner_id", ["..."])`, and because the sync swallows its own failures
so a stale card cannot fail a parse, it would have failed silently on every
import. The backfill is what surfaced it, by failing loudly on a dyno.
"""
from __future__ import annotations

import inspect

import pytest

from job_os.scripts import backfill_stuck_cards
from job_os.services import appwrite_tables
from job_os.services.jd_ingest import sync_job_into_cards


def test_the_parser_rejects_appwrite_query_json() -> None:
    # The shape that shipped, kept here so the mistake stays recognisable.
    with pytest.raises(ValueError):
        appwrite_tables._parse_filter('equal("owner_id", ["user_123"])')


def test_the_parser_accepts_what_these_callers_send() -> None:
    assert appwrite_tables._parse_filter("owner_id=user_123") == {
        "method": "equal",
        "attribute": "owner_id",
        "values": ["user_123"],
    }
    # `archived=false` has to reach Appwrite as a boolean: a boolean column
    # matches nothing against the string "false".
    assert appwrite_tables._parse_filter("archived=false")["values"] == [False]


@pytest.mark.parametrize(
    "source",
    [inspect.getsource(sync_job_into_cards), inspect.getsource(backfill_stuck_cards.backfill)],
    ids=["sync_job_into_cards", "backfill"],
)
def test_neither_caller_hands_over_query_json(source: str) -> None:
    assert 'equal("' not in source, "Query JSON in a filter list raises at runtime"
    assert "owner_id=" in source, "the owner filter is what keeps this single-tenant"


# ---------------------------------------------------------------------------
# operator precedence and value typing
# ---------------------------------------------------------------------------
# Moved here from `test_appwrite_job_postings.py`, which was deleted when
# `job_postings` went back to Postgres. The filter shapes below are written
# the way that table's callers wrote them, because that is where the parser was
# exercised hardest -- and they are still what `_parse_filter` has to accept for
# every table that remains. The translation was read out of `appwrite-cli`'s own
# bundled source rather than guessed, so a change to either side is caught here
# instead of by a live 400.


def test_operator_precedence_and_typing() -> None:
    assert appwrite_tables._parse_filter("active=true") == {
        "method": "equal",
        "attribute": "active",
        "values": [True],
    }
    assert appwrite_tables._parse_filter("jd_hydrated=false") == {
        "method": "equal",
        "attribute": "jd_hydrated",
        "values": [False],
    }
    assert appwrite_tables._parse_filter("salary_max>=40") == {
        "method": "greaterThanEqual",
        "attribute": "salary_max",
        "values": [40],
    }
    # `!=` must not be swallowed by the plain `=` pattern.
    run_id = "b51dcfb4-9a0d-4c51-ab7e-0ae60362c6b9"
    assert appwrite_tables._parse_filter(f"last_crawl_run_id!={run_id}") == {
        "method": "notEqual",
        "attribute": "last_crawl_run_id",
        "values": [run_id],
    }
    # A UUID/string value that happens to not be true/false/null/numeric
    # stays a plain string, not silently coerced.
    assert appwrite_tables._parse_filter(f"row_id={run_id}")["values"] == [run_id]


def test_a_null_value_becomes_its_own_method() -> None:
    """Appwrite has no null-valued equality: `equal` with null is rejected
    outright ("Query value is invalid for attribute"), so the null check has to
    become `isNull` rather than pass a null through as a value."""
    assert appwrite_tables._parse_filter("canonical_id=null") == {
        "method": "isNull",
        "attribute": "canonical_id",
    }
    assert appwrite_tables._parse_filter("canonical_id!=null") == {
        "method": "isNotNull",
        "attribute": "canonical_id",
    }


def test_datetime_filter_stays_a_string() -> None:
    parsed = appwrite_tables._parse_filter("posted_at>=2026-08-01T00:00:00+00:00")
    assert parsed["method"] == "greaterThanEqual"
    assert parsed["values"] == ["2026-08-01T00:00:00+00:00"]


def test_unsupported_expression_raises() -> None:
    with pytest.raises(ValueError):
        appwrite_tables._parse_filter("active true")
