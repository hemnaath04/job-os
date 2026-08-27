"""Workspace.verified_facts (main.py): one user's vault, never another's.

verified_facts is the read that feeds a tailored resume, and this function
runs on a server API key, which bypasses Appwrite's row permissions. Nothing
below the query protects the boundary, so the filters in the query are the
whole of the isolation and are what these tests exercise.

Same seam as test_reap_stale_running_jobs: the real Workspace with only
self.tables swapped, and a fake that applies the real JSON Query strings the
method builds. So "the other user's rows are absent" is proven by the query
excluding them, exactly as production excludes them, and not by the fake
being handed a convenient table.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import main

MINE = "user-1"
THEIRS = "user-2"


class FakeTables:
    """Stands in for TablesDB across the two tables this read touches.

    Keyed by table so a fact query and a bullet query cannot accidentally
    answer from the same pile, which is the distinction under test.
    """

    def __init__(self, tables: dict[str, list[dict[str, Any]]]) -> None:
        self.tables = tables

    def list_rows(
        self, database_id: str, table_id: str, queries: list[str], total: bool = False
    ) -> SimpleNamespace:
        matched = list(self.tables.get(table_id, []))
        for raw in queries:
            parsed = json.loads(raw)
            method = parsed["method"]
            if method == "equal":
                allowed = parsed["values"]
                matched = [r for r in matched if r.get(parsed["attribute"]) in allowed]
            elif method == "limit":
                matched = matched[: parsed["values"][0]]
            else:
                raise AssertionError(f"FakeTables does not model Query.{method}")
        return SimpleNamespace(rows=matched)


def _fact(*, row_id: str, owner_id: str, title: str, verified: bool = True) -> dict[str, Any]:
    return {
        "$id": row_id,
        "owner_id": owner_id,
        "verified": verified,
        "archived": False,
        "snapshot": json.dumps({"id": row_id, "kind": "role", "title": title}),
    }


def _bullet(*, row_id: str, owner_id: str, fact_id: str, text: str) -> dict[str, Any]:
    return {
        "$id": row_id,
        "owner_id": owner_id,
        "fact_id": fact_id,
        "snapshot": json.dumps({"id": row_id, "text": text}),
    }


def _workspace(user_id: str, tables: Any) -> main.Workspace:
    req = SimpleNamespace(headers={"x-appwrite-user-id": user_id, "x-appwrite-key": "test-key"})
    workspace = main.Workspace(req)
    workspace.tables = tables
    return workspace


def _tables(facts: list[dict[str, Any]], bullets: list[dict[str, Any]]) -> FakeTables:
    probe = _workspace(MINE, FakeTables({}))
    return FakeTables({probe.profile_facts_table: facts, probe.fact_bullets_table: bullets})


def test_another_users_facts_are_absent_from_the_vault() -> None:
    tables = _tables(
        facts=[
            _fact(row_id="fact-mine", owner_id=MINE, title="Test Automation Engineer"),
            _fact(row_id="fact-theirs", owner_id=THEIRS, title="Computer Vision Engineer"),
        ],
        bullets=[
            _bullet(row_id="b-mine", owner_id=MINE, fact_id="fact-mine", text="mine"),
            _bullet(row_id="b-theirs", owner_id=THEIRS, fact_id="fact-theirs", text="theirs"),
        ],
    )

    vault = _workspace(MINE, tables).verified_facts()

    assert [fact["id"] for fact in vault] == ["fact-mine"]
    rendered = json.dumps(vault)
    assert "theirs" not in rendered
    assert "Computer Vision Engineer" not in rendered


def test_another_users_bullet_pointed_at_my_fact_is_still_excluded() -> None:
    """The bullet query is owner-filtered in its own right, not just by fact id.

    Scoping bullets solely through owner-scoped fact ids holds only while
    every fact_id is trustworthy. A row carrying someone else's owner_id and
    one of my fact ids, from a bad import or a stale write, would otherwise
    be joined straight into my resume as if I had written it.
    """
    tables = _tables(
        facts=[_fact(row_id="fact-mine", owner_id=MINE, title="Test Automation Engineer")],
        bullets=[
            _bullet(row_id="b-mine", owner_id=MINE, fact_id="fact-mine", text="mine"),
            _bullet(row_id="b-stray", owner_id=THEIRS, fact_id="fact-mine", text="theirs"),
        ],
    )

    vault = _workspace(MINE, tables).verified_facts()

    assert [b["text"] for b in vault[0]["bullets"]] == ["mine"]


def test_an_unverified_or_archived_fact_of_mine_is_not_served() -> None:
    tables = _tables(
        facts=[
            _fact(row_id="fact-draft", owner_id=MINE, title="Draft", verified=False),
            {**_fact(row_id="fact-old", owner_id=MINE, title="Old"), "archived": True},
        ],
        bullets=[],
    )

    assert _workspace(MINE, tables).verified_facts() == []
