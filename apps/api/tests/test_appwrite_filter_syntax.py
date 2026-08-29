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
