"""Talk to Appwrite's TablesDB over its documented REST API.

A first version of this module shelled out to the `appwrite` CLI's own
locally-authenticated session instead of a real key -- there was no
`APPWRITE_API_KEY` value anywhere in this environment, and minting one
through the Project API needed a `keys.write` scope this project's role did
not have (Appwrite deprecated creating keys through that API on 2026-08-17
anyway; the console is now the only way). The CLI approach worked against
the real 34,942-row migration and every local test, then failed in
production with `FileNotFoundError: 'appwrite'`: the CLI binary was never
part of the deploy image, and it should not have been running as one
developer's personal login inside a shared server process regardless.

This version talks straight to `{endpoint}/tablesdb/{databaseId}/tables/{tableId}/rows`
over `httpx`, authenticated with a real `X-Appwrite-Key` header (scoped to
`databases.read`/`databases.write` only), the same REST surface the CLI and
every server SDK call underneath. The four HTTP verbs below (GET/POST/PUT/
PATCH) and the filter-string-to-Query-object translation were read directly
out of the `appwrite-cli` package's own bundled source
(`dist/cli.cjs`, `parseFilterQuery`/`stringifyQuery`), not guessed, so
`filters=["active=true"]` produces exactly the query object the CLI's own
`--filter active=true` would have.
"""
from __future__ import annotations

import re
from typing import Any

import httpx

from job_os.settings import get_settings

#: Appwrite's own bulk-write cap per `create-rows`/`upsert-rows` call.
BATCH_SIZE = 100

#: `(regex, method)`, checked in this order so `!=`/`>=`/`<=` match before
#: the single-character `=`/`>`/`<` operators they contain. Mirrors
#: `appwrite-cli`'s `filterOperators` table exactly.
_FILTER_OPERATORS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^(.+?)\s*!=\s*(.*)$"), "notEqual"),
    (re.compile(r"^(.+?)\s*>=\s*(.*)$"), "greaterThanEqual"),
    (re.compile(r"^(.+?)\s*<=\s*(.*)$"), "lessThanEqual"),
    (re.compile(r"^(.+?)\s*=\s*(.*)$"), "equal"),
    (re.compile(r"^(.+?)\s*>\s*(.*)$"), "greaterThan"),
    (re.compile(r"^(.+?)\s*<\s*(.*)$"), "lessThan"),
]
_NUMERIC = re.compile(r"^-?(?:\d+|\d*\.\d+)(?:e[+-]?\d+)?$", re.IGNORECASE)


class AppwriteTablesError(RuntimeError):
    """A TablesDB REST call returned an error response. `detail` is Appwrite's own message."""

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"Appwrite TablesDB call failed ({status_code}): {detail[:800]}")


def _query_value(raw: str) -> Any:
    """One filter's right-hand side, typed the way `active=true`/`salary_max>=40`
    need to be to match a boolean/integer column -- a query filed as the string
    `"true"` against a boolean column matches nothing. Mirrors `appwrite-cli`'s
    `parseQueryValue` exactly (including `null`, bare numbers, and `[a,b]` arrays)."""
    value = raw.strip()
    if value == "true":
        return True
    if value == "false":
        return False
    if value == "null":
        return None
    if _NUMERIC.match(value):
        return float(value) if ("." in value or "e" in value.lower()) else int(value)
    if value.startswith("[") and value.endswith("]"):
        import json as _json

        return _json.loads(value)
    return value


def _parse_filter(expression: str) -> dict[str, Any]:
    for pattern, method in _FILTER_OPERATORS:
        match = pattern.match(expression)
        if not match:
            continue
        attribute, raw_value = match.group(1).strip(), match.group(2)
        return {"method": method, "attribute": attribute, "values": [_query_value(raw_value)]}
    raise ValueError(f"Unsupported filter expression: {expression!r}")


def _base_url() -> tuple[str, dict[str, str]]:
    settings = get_settings()
    if not settings.appwrite_api_key:
        raise AppwriteTablesError(
            0,
            "APPWRITE_API_KEY is not configured -- job_postings reads/writes cannot "
            "reach Appwrite without it. See settings.py's appwrite_api_key field.",
        )
    table_id = settings.appwrite_job_postings_table_id
    url = (
        f"{settings.appwrite_endpoint}/tablesdb/{settings.appwrite_database_id}"
        f"/tables/{table_id}/rows"
    )
    headers = {
        "X-Appwrite-Project": settings.appwrite_project_id,
        "X-Appwrite-Key": settings.appwrite_api_key,
        "Content-Type": "application/json",
    }
    return url, headers


async def _request(method: str, *, params: list[tuple[str, str]] | None = None, json_body: dict[str, Any] | None = None) -> dict[str, Any]:
    url, headers = _base_url()
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.request(method, url, headers=headers, params=params, json=json_body)
    if response.status_code >= 400:
        detail = response.text
        try:
            detail = response.json().get("message", detail)
        except ValueError:
            pass
        raise AppwriteTablesError(response.status_code, detail)
    if not response.content:
        return {}
    return response.json()


def _query_params(
    filters: list[str] | None, queries: list[dict[str, Any]] | None, select: list[str] | None,
    sort_desc: str | None, limit: int | None,
) -> list[tuple[str, str]]:
    import json as _json

    params: list[tuple[str, str]] = []
    for q in queries or []:
        params.append(("queries[]", _json.dumps(q)))
    for f in filters or []:
        params.append(("queries[]", _json.dumps(_parse_filter(f))))
    if select:
        params.append(("queries[]", _json.dumps({"method": "select", "values": select})))
    if sort_desc:
        params.append(("queries[]", _json.dumps({"method": "orderDesc", "attribute": sort_desc})))
    if limit is not None:
        params.append(("queries[]", _json.dumps({"method": "limit", "values": [limit]})))
    return params


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
    params = _query_params(filters, queries, select, sort_desc, limit)
    payload = await _request("GET", params=params)
    return payload.get("rows", [])


async def create_rows(rows: list[dict[str, Any]]) -> None:
    for start in range(0, len(rows), BATCH_SIZE):
        batch = rows[start : start + BATCH_SIZE]
        await _request("POST", json_body={"rows": batch})


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
        await _request("PUT", json_body={"rows": batch})


async def update_rows(
    *, filters: list[str] | None = None, queries: list[dict[str, Any]] | None = None, data: dict[str, Any]
) -> int:
    """Apply one `data` patch to every row matching `filters`/`queries`, in a single call.

    Real bulk semantics from Appwrite itself here, unlike `upsert_rows` --
    exactly what `deactivate_missing` needs (one WHERE, one SET, no per-row
    round trip), so no lookup-then-write race exists for this path. `queries`
    exists for the same reason `list_rows` takes it: a plain `filters` string
    cannot express an isNull check, which `mark_duplicates` needs to preserve
    its "only mark an unmerged row" guard.
    """
    queries_payload = [*(queries or [])]
    for f in filters or []:
        queries_payload.append(_parse_filter(f))
    payload = await _request("PATCH", json_body={"data": data, "queries": queries_payload})
    rows = payload.get("rows")
    if isinstance(rows, list):
        return len(rows)
    total = payload.get("total")
    return int(total) if isinstance(total, int) else 0
