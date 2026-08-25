"""Workspace.reap_stale_running_jobs (main.py): who gets marked failed.

This has to be right in both directions. Reap too little and an orphaned row
(the function was killed by Appwrite's 900s timeout, see STALE_RUNNING_AFTER_S
in main.py) sits at "running" until the browser's own poll ceiling gives up on
it. Reap too much, someone else's row, the job that is dispatching right
now, or one that is merely slow rather than dead, and a real in-flight run
gets its status column overwritten out from under it.

Every case below goes through the real reap_stale_running_jobs and update_job,
not a reimplementation, with only the Appwrite TablesDB client swapped for an
in-memory fake at the one seam this method calls through: self.tables.
FakeTables.list_rows applies the same query filters Appwrite would (parsed
from the real JSON Query strings the method builds), so "a non-running row is
left alone" is proven by the query excluding it, the same way production is
protected, not by an assumption about what the fake happens to hand back.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import main

STALE = timedelta(seconds=main.STALE_RUNNING_AFTER_S + 60)  # comfortably past the ceiling
FRESH = timedelta(seconds=main.STALE_RUNNING_AFTER_S - 60)  # comfortably inside it


class FakeTables:
    """Stands in for appwrite.services.tables_db.TablesDB.

    Implements exactly the three calls this code path makes: list_rows for
    the reap query itself, and get_row/update_row, both reached through
    Workspace.owned_row/update_snapshot inside update_job whenever a row is
    actually marked failed.
    """

    def __init__(self, rows: dict[str, dict[str, Any]]) -> None:
        self.rows = rows
        self.update_calls: list[tuple[str, dict[str, Any]]] = []

    def list_rows(
        self, database_id: str, table_id: str, queries: list[str], total: bool = False
    ) -> SimpleNamespace:
        matched = list(self.rows.values())
        for raw in queries:
            parsed = json.loads(raw)
            method = parsed["method"]
            if method == "equal":
                allowed = parsed["values"]
                matched = [r for r in matched if r.get(parsed["attribute"]) in allowed]
            elif method == "limit":
                matched = matched[: parsed["values"][0]]
            else:
                # A future change to the real query (an order clause, say)
                # would otherwise pass straight through unmodelled and the
                # fake would silently stop matching production. Fail loudly
                # instead so the fake gets extended, not trusted by accident.
                raise AssertionError(f"FakeTables does not model Query.{method}")
        return SimpleNamespace(rows=matched)

    def get_row(self, database_id: str, table_id: str, row_id: str) -> dict[str, Any]:
        return self.rows[row_id]

    def update_row(
        self, database_id: str, table_id: str, row_id: str, fields: dict[str, Any]
    ) -> dict[str, Any]:
        self.rows[row_id] = {**self.rows[row_id], **fields}
        self.update_calls.append((row_id, dict(fields)))
        return self.rows[row_id]


class RaisingListTables(FakeTables):
    """A tables client whose list query is the one thing broken.

    Models a real Appwrite query/lookup failure inside the reap step with
    everything else intact, which is the shape main() actually depends on:
    the job about to run must still be marked "running" even though the
    query used to look for stale siblings blew up.
    """

    def list_rows(self, *args: Any, **kwargs: Any) -> SimpleNamespace:
        raise RuntimeError("Appwrite query failed")


def _row(*, owner_id: str, status: str, updated_at: datetime, row_id: str) -> dict[str, Any]:
    return {
        "$id": row_id,
        "owner_id": owner_id,
        "status": status,
        "source_updated_at": updated_at.isoformat(),
        "snapshot": json.dumps({"status": status}),
    }


def _workspace(user_id: str, tables: Any) -> main.Workspace:
    """A real Workspace with a fake tables client, no Appwrite connection.

    Workspace.__init__ only builds local SDK client objects, no network call,
    so constructing the real class and swapping self.tables afterwards
    exercises the actual reap_stale_running_jobs/update_job code, not a
    stand-in for it.
    """
    req = SimpleNamespace(headers={"x-appwrite-user-id": user_id, "x-appwrite-key": "test-key"})
    workspace = main.Workspace(req)
    workspace.tables = tables
    return workspace


def test_a_stale_running_row_owned_by_the_caller_is_marked_failed() -> None:
    now = datetime.now(UTC)
    row_id = "job-orphaned"
    tables = FakeTables(
        {row_id: _row(owner_id="user-1", status="running", updated_at=now - STALE, row_id=row_id)}
    )
    workspace = _workspace("user-1", tables)

    workspace.reap_stale_running_jobs(skip_job_id="job-currently-dispatching")

    assert tables.rows[row_id]["status"] == "failed"
    assert len(tables.update_calls) == 1
    written_row_id, written_fields = tables.update_calls[0]
    assert written_row_id == row_id
    assert written_fields["status"] == "failed"
    assert "15 minutes" in json.loads(written_fields["snapshot"])["error"]


def test_a_different_owners_stale_running_row_is_left_alone() -> None:
    now = datetime.now(UTC)
    other_row_id = "job-belongs-to-someone-else"
    tables = FakeTables(
        {
            other_row_id: _row(
                owner_id="user-2", status="running", updated_at=now - STALE, row_id=other_row_id
            )
        }
    )
    workspace = _workspace("user-1", tables)

    workspace.reap_stale_running_jobs(skip_job_id="job-currently-dispatching")

    assert tables.rows[other_row_id]["status"] == "running"
    assert tables.update_calls == []


def test_the_currently_dispatching_job_is_never_reaped_even_if_stale() -> None:
    now = datetime.now(UTC)
    dispatching_id = "job-about-to-run"
    tables = FakeTables(
        {
            dispatching_id: _row(
                owner_id="user-1", status="running", updated_at=now - STALE, row_id=dispatching_id
            )
        }
    )
    workspace = _workspace("user-1", tables)

    # Every other condition matches (same owner, status=="running", 15+
    # minutes stale). Only skip_job_id should be the reason this survives.
    workspace.reap_stale_running_jobs(skip_job_id=dispatching_id)

    assert tables.rows[dispatching_id]["status"] == "running"
    assert tables.update_calls == []


def test_a_fresh_running_row_is_left_alone() -> None:
    now = datetime.now(UTC)
    row_id = "job-still-working"
    tables = FakeTables(
        {row_id: _row(owner_id="user-1", status="running", updated_at=now - FRESH, row_id=row_id)}
    )
    workspace = _workspace("user-1", tables)

    workspace.reap_stale_running_jobs(skip_job_id="job-currently-dispatching")

    assert tables.rows[row_id] == {
        "$id": row_id,
        "owner_id": "user-1",
        "status": "running",
        "source_updated_at": (now - FRESH).isoformat(),
        "snapshot": json.dumps({"status": "running"}),
    }
    assert tables.update_calls == []


def test_a_non_running_row_is_left_alone_no_matter_how_stale() -> None:
    now = datetime.now(UTC)
    succeeded_id, failed_id = "job-done", "job-already-failed"
    tables = FakeTables(
        {
            succeeded_id: _row(
                owner_id="user-1", status="succeeded", updated_at=now - STALE, row_id=succeeded_id
            ),
            failed_id: _row(
                owner_id="user-1", status="failed", updated_at=now - STALE, row_id=failed_id
            ),
        }
    )
    workspace = _workspace("user-1", tables)

    workspace.reap_stale_running_jobs(skip_job_id="job-currently-dispatching")

    assert tables.rows[succeeded_id]["status"] == "succeeded"
    assert tables.rows[failed_id]["status"] == "failed"
    assert tables.update_calls == []


def test_a_query_failure_is_swallowed_and_does_not_block_the_dispatch_that_follows() -> None:
    now = datetime.now(UTC)
    job_id = "job-now-dispatching"
    tables = RaisingListTables(
        {job_id: _row(owner_id="user-1", status="queued", updated_at=now, row_id=job_id)}
    )
    workspace = _workspace("user-1", tables)

    # main() calls this immediately before workspace.update_job(job_id,
    # status="running"). A broken query here must not raise, and must not
    # leave anything in a state that stops that next call from succeeding.
    workspace.reap_stale_running_jobs(skip_job_id=job_id)

    assert tables.update_calls == []

    workspace.update_job(job_id, status="running")

    assert len(tables.update_calls) == 1
    written_row_id, written_fields = tables.update_calls[0]
    assert written_row_id == job_id
    assert written_fields["status"] == "running"
    assert json.loads(written_fields["snapshot"])["status"] == "running"
