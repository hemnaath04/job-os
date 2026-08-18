"""Talk to Appwrite's TablesDB through the `appwrite` CLI's own session.

Not the Appwrite Python SDK. There is no `APPWRITE_API_KEY` value anywhere in
this environment for a server SDK client to authenticate with, and minting one
requires a `keys.write` scope this project's role does not have. The `appwrite`
CLI, by contrast, already carries a working authenticated session (from an
earlier `appwrite login`) and was the only thing that could actually create the
`job_postings` table, its 45 columns, and its 9 indexes, and migrate all 34,942
existing rows. This module is that same mechanism, wrapped for the ingest
write path and the search read path, instead of re-deriving it per caller.

Every call shells out via `subprocess.run` with an argv list, never
`shell=True` -- a job description is untrusted, attacker-adjacent text (a
company writes it, not us), and building a shell command string out of it
would be a command-injection bug waiting to be found. Appwrite's own bulk cap
is 100 rows per call; batches here are smaller (`BATCH_SIZE`) because macOS's
`ARG_MAX` is ~1MB and a posting's `jd_raw`/`jd_clean` text makes 100 rows a
real risk of exceeding it.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
from typing import Any

DATABASE_ID = "job-os"
TABLE_ID = "job_postings"
#: Below Appwrite's 100-row bulk cap, sized to stay well under argv limits
#: with job-posting-sized text fields. See the module docstring.
BATCH_SIZE = 25


class AppwriteCliError(RuntimeError):
    """A `appwrite` CLI invocation exited non-zero. `stderr` is the CLI's own message."""

    def __init__(self, args: list[str], stderr: str):
        self.args = args
        self.stderr = stderr
        super().__init__(f"appwrite CLI failed ({' '.join(args[:4])}...): {stderr[:800]}")


def _run(args: list[str]) -> dict[str, Any]:
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        raise AppwriteCliError(args, result.stderr)
    if not result.stdout.strip():
        return {}
    return json.loads(result.stdout)


async def list_rows(
    *,
    filters: list[str] | None = None,
    queries: list[dict[str, Any]] | None = None,
    select: list[str] | None = None,
    sort_desc: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """All matching rows, one page. Callers here never need more than one:

    the search read path bounds its own pool size, and the ingest write path's
    lookups are scoped to one batch (<= `BATCH_SIZE` postings) at a time.
    """
    args = ["appwrite", "tablesdb", "list-rows", "--database-id", DATABASE_ID, "--table-id", TABLE_ID, "--json"]
    for f in filters or []:
        args += ["--filter", f]
    for q in queries or []:
        args += ["--queries", json.dumps(q)]
    for s in select or []:
        args += ["--select", s]
    if sort_desc:
        args += ["--sort-desc", sort_desc]
    if limit is not None:
        args += ["--limit", str(limit)]
    payload = await asyncio.to_thread(_run, args)
    return payload.get("rows", [])


async def create_rows(rows: list[dict[str, Any]]) -> None:
    for start in range(0, len(rows), BATCH_SIZE):
        batch = rows[start : start + BATCH_SIZE]
        args = ["appwrite", "tablesdb", "create-rows", "--database-id", DATABASE_ID, "--table-id", TABLE_ID, "--json", "--rows"]
        args += [json.dumps(r) for r in batch]
        await asyncio.to_thread(_run, args)


async def upsert_rows(rows: list[dict[str, Any]]) -> None:
    """Create-or-update, keyed by `$id` when a row carries one.

    A row without `$id` is created fresh (Appwrite assigns one); a row with
    `$id` set updates that existing row. There is no `ON CONFLICT`-style
    single-statement atomic upsert here the way Postgres gave `upsert.py` for
    free -- the caller (`ingest/upsert.py`) resolves existing `$id`s with its
    own `list_rows` lookup first, which means a second writer touching the
    same posting between that lookup and this call would race. Acceptable for
    this app's actual concurrency profile (one scheduled crawler, not several
    concurrent ones), and said plainly rather than papered over.
    """
    for start in range(0, len(rows), BATCH_SIZE):
        batch = rows[start : start + BATCH_SIZE]
        args = ["appwrite", "tablesdb", "upsert-rows", "--database-id", DATABASE_ID, "--table-id", TABLE_ID, "--json", "--rows"]
        args += [json.dumps(r) for r in batch]
        await asyncio.to_thread(_run, args)


async def update_rows(*, filters: list[str], data: dict[str, Any]) -> int:
    """Apply one `data` patch to every row matching `filters`, in a single call.

    Real bulk semantics from Appwrite itself here, unlike `upsert_rows` --
    exactly what `deactivate_missing` needs (one WHERE, one SET, no per-row
    round trip), so no lookup-then-write race exists for this path.
    """
    args = ["appwrite", "tablesdb", "update-rows", "--database-id", DATABASE_ID, "--table-id", TABLE_ID, "--json", "--data", json.dumps(data)]
    for f in filters:
        args += ["--filter", f]
    payload = await asyncio.to_thread(_run, args)
    rows = payload.get("rows")
    if isinstance(rows, list):
        return len(rows)
    total = payload.get("total")
    return int(total) if isinstance(total, int) else 0
