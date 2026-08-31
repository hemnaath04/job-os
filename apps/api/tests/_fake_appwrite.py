"""An in-memory Appwrite TablesDB, so the job_postings tests can run anywhere.

WHY THIS EXISTS
---------------
`test_ingest_upsert.py` and `test_job_index_ranking.py` cover `upsert_postings`,
`deactivate_missing`, `mark_duplicates` and `search_index` -- the whole write and
read path of the crawl index. They were marked `requires_appwrite_key`, which
meant: skipped in CI (35 of them, on every run, since the Appwrite cutover), and
run against the PRODUCTION table locally. Production still carries 123 rows whose
`source` starts `rank_`, left there by those runs; nothing ever cleaned up. Three
real bugs shipped through that gap in one day (a wrong bulk-query shape, a
null-filter shape, and a missing index). Tests that run nowhere are how.

WHERE THE SEAM IS, AND WHY IT IS NOT THE FIVE PUBLIC FUNCTIONS
--------------------------------------------------------------
This fake replaces `appwrite_tables._request`, one level below `list_rows`,
`count_rows`, `create_rows`, `upsert_rows` and `update_rows`, rather than
replacing those five.

The obvious choice is the five, and it was the starting plan. It cannot express
the fidelity these tests need, because two of the three shapes that actually
broke live *below* those functions:

  * `_parse_filter` turns `col=null` into `isNull`, never `equal` with a null
    value, because Appwrite rejects the latter outright. Replacing `list_rows`
    means `_parse_filter` never runs and the fake has to re-implement it -- a
    second copy of the exact translation whose first copy was the bug.
  * `_encoded_queries` serialises a bulk PATCH's queries to JSON STRINGS,
    because Appwrite validates that array as strings and rejects objects. That
    is the line that killed every sweep, and it lives inside `update_rows`.
    Replacing `update_rows` deletes it from the test entirely.

At `_request`, all five functions run their real code: filter parsing, query
serialisation for both transports, `BATCH_SIZE` batching, and `update_rows`'
own rows-vs-total return counting. The fake receives exactly what Appwrite's
REST endpoint would receive -- a method, `queries[]` URL params, a JSON body --
and answers with the payload shape Appwrite answers with.

This is deliberately NOT the HTTP layer. No sockets, no httpx, no status codes
invented, no auth headers, no retry ladder. Those already have their own
coverage in `test_appwrite_tables.py`, which drives the real `_request` against
a fake httpx client, including `test_a_bulk_update_sends_its_queries_as_json_strings`.
This fake does not duplicate any of it.

WHAT IS MODELLED FAITHFULLY, AND WHAT IS NOT
--------------------------------------------
Modelled, because it is the class of thing that broke:

  * `equal`/`notEqual` with a null value is REJECTED, the way Appwrite rejects
    it ("Query value is invalid for attribute"). `_parse_filter` is supposed to
    make that unreachable; if it ever stops doing so, these tests say so
    instead of quietly matching nothing.
  * A bulk PATCH's `queries` must be an array of strings. An array of objects
    gets Appwrite's own confusing size-shaped error, verbatim.
  * The `queries` array caps at 100 entries and each entry at 4096 characters,
    which is what `_lookup_by_source_id`'s character-budget chunking exists to
    respect. Chunking that stopped working would fail here.
  * `uq_source_pair`, the UNIQUE index on (source, source_id) from
    `bootstrap_appwrite_job_postings.py`. A lookup-before-write that stopped
    finding the existing row would try to insert a duplicate; without the
    constraint that reads as a silently doubled table.
  * `notEqual` against a NULL column does not match, per SQL three-valued
    logic. UNVERIFIED against the live service; it is what MariaDB does with
    `col != 'x'` where col IS NULL, and Appwrite's MariaDB adapter emits
    exactly that comparison. See `deactivate_missing`'s note in
    `test_ingest_upsert.py` for why it matters.
  * Datetime columns hold millisecond precision. Taken from Appwrite's
    documented datetime format (`Y-m-d\\TH:i:s.vP`), NOT measured against the
    live table.

Not modelled, on purpose:

  * `total` saturation. Appwrite's own `total` is an estimate that was observed
    capping around 5000 against a 34,942-row table, and this fake returns an
    exact count instead. It cannot matter here: `search_index` deliberately
    never reads `total` (it uses `len(rows)`, and says why in its own comment),
    `count_rows` is the only reader and neither of these two files calls it,
    and every test in them works with fewer than ten rows, so a 5000-row cap is
    unreachable. Faking it would be untestable decoration. `index_stats`, which
    does read `total`, is covered separately in `test_index_stats_degrades.py`.
  * MariaDB fulltext minimum token size and stopword lists. `search` here is a
    phrase match over word tokens (see `_matches_search`). The production
    behaviour being pinned is "a quoted phrase is a phrase, not a bag of
    words", which is the bug `_quote_phrase` was written for; the tokenizer's
    exact agreement with MariaDB's is not something these tests turn on, and
    claiming it without measuring would be worse than saying so here.
  * Row permissions and row security. `job_postings` is created with
    `row_security=False` and is only ever read through the API key.
  * Appwrite's per-value array caps beyond the two above, and its column size
    limits. Unverified, so unmodelled.

ONE MORE ASSUMPTION WORTH SAYING OUT LOUD
------------------------------------------
`upsert_rows` MERGES into an existing `$id` rather than replacing the row.
`ingest/upsert._write_batch` depends on that completely: for an existing
posting it sends only `$id`, `last_seen_at`, `last_crawl_run_id`, `active`,
`inactive_since` and `repost_count`, and every "first_seen_at survives a
re-crawl" guarantee rests on the columns it did not send being left alone. If
Appwrite replaced instead of merged, production would be destroying
`first_seen_at` on every sweep. I did not verify this against the live service
(these tests must not touch it), so it is written down here as the assumption
it is rather than left implicit in the fake's code.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from job_os.services.appwrite_tables import AppwriteTablesError

#: Columns Appwrite stores as a real datetime rather than a string, so the fake
#: parses, compares and orders them as datetimes instead of comparing ISO text.
#: The first five are `bootstrap_appwrite_job_postings._DATETIME_COLUMNS`
#: verbatim. `content_updated_at` is not in that script -- the script predates
#: the column -- and is inferred from `ingest/upsert.to_row` writing an
#: `isoformat()` string into it. Unverified against the live table.
_DATETIME_COLUMNS = frozenset(
    {
        "posted_at",
        "closes_at",
        "first_seen_at",
        "last_seen_at",
        "inactive_since",
        "content_updated_at",
    }
)

#: Appwrite returns these on every row whatever `select` asked for.
_SYSTEM_COLUMNS = ("$id", "$createdAt", "$updatedAt")

#: Appwrite's own caps on the `queries` parameter, quoted from the 400 that
#: killed every sweep: "Value must a valid array no longer than 100 items and
#: Value must be a valid string and at least 1 chars and no longer than 4096
#: chars".
_MAX_QUERIES = 100
_MAX_QUERY_CHARS = 4096

_QUERIES_PARAM_ERROR = (
    "Invalid `queries` param: Value must a valid array no longer than 100 items "
    "and Value must be a valid string and at least 1 chars and no longer than "
    "4096 chars"
)

#: Same character class `job_index._title_words` splits on, so a phrase that
#: this fake says matches is one the ranker would also read as a title hit.
_WORD = re.compile(r"[a-z0-9+#]+")

_MODIFIERS = frozenset({"select", "limit", "offset", "orderDesc", "orderAsc", "cursorAfter"})


def _tokens(text: object) -> list[str]:
    return _WORD.findall(str(text or "").lower())


def _parse_dt(value: object) -> datetime | None:
    """One datetime-column value, as the column would hold it.

    Truncated to milliseconds because that is the precision Appwrite's datetime
    format carries. A caller that writes microseconds does not get them back,
    and a test that asserts an exact round trip has to say so.
    """
    if value is None:
        return None
    parsed = (
        value
        if isinstance(value, datetime)
        else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    )
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).replace(microsecond=(parsed.microsecond // 1000) * 1000)


def _format_dt(value: datetime) -> str:
    """Appwrite's own datetime rendering: ISO 8601 with exactly three decimals."""
    return f"{value.strftime('%Y-%m-%dT%H:%M:%S')}.{value.microsecond // 1000:03d}+00:00"


def _stored(column: str, value: object) -> object:
    return _parse_dt(value) if column in _DATETIME_COLUMNS else value


def _served(column: str, value: object) -> object:
    if column in _DATETIME_COLUMNS and isinstance(value, datetime):
        return _format_dt(value)
    return value


def _comparable(column: str, value: object) -> object:
    """A query value coerced to whatever the column actually holds."""
    return _parse_dt(value) if column in _DATETIME_COLUMNS else value


def _matches_search(haystack: object, term: object) -> bool:
    """One `search` query, modelled as MariaDB phrase mode.

    `job_index` always sends a double-quoted phrase (see `_quote_phrase` and
    the live search that made it necessary: unquoted, "software engineer
    intern" ran in natural-language mode and matched almost the whole table).
    Quoted means the words must appear adjacent and in order; that is what this
    checks, over word tokens so punctuation and case do not count. An unquoted
    term falls back to "any word appears", the coarse shape of MariaDB's
    natural-language mode -- coarse on purpose, see the module docstring.
    """
    raw = str(term or "").strip()
    quoted = len(raw) >= 2 and raw.startswith('"') and raw.endswith('"')
    needle = _tokens(raw.strip('"'))
    if not needle:
        return False
    hay = _tokens(haystack)
    if not quoted:
        return any(word in hay for word in needle)
    width = len(needle)
    return any(hay[i : i + width] == needle for i in range(len(hay) - width + 1))


class FakeAppwriteTables:
    """An in-memory stand-in for the TablesDB REST endpoint.

    Install it with `install(monkeypatch)`, then drive the real
    `appwrite_tables` functions at it. `find`/`all_rows` read the store back in
    the shape Appwrite would serve it.
    """

    def __init__(self, *, table_id: str = "job_postings") -> None:
        self.default_table = table_id
        #: table id -> ($id -> row). Column values are stored typed, not as the
        #: JSON text they arrived as, so comparisons behave like a real column.
        self._tables: dict[str, dict[str, dict[str, Any]]] = {}
        #: Every call the code under test made, for tests that assert on shape
        #: rather than on data.
        self.requests: list[dict[str, Any]] = []

    # -- installation ------------------------------------------------------

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("job_os.services.appwrite_tables._request", self._request)

    # -- inspection --------------------------------------------------------

    def all_rows(self, table_id: str | None = None) -> list[dict[str, Any]]:
        table = self._tables.get(table_id or self.default_table, {})
        return [self._serve(row, None) for row in table.values()]

    def find(self, table_id: str | None = None, **columns: Any) -> dict[str, Any]:
        """The single stored row matching every column given.

        Deliberately strict about "single": these tests are largely about a
        re-crawl updating one row rather than writing a second, and an
        assertion that silently read the first of two would miss exactly that.
        """
        matches = [
            row
            for row in self.all_rows(table_id)
            if all(row.get(key) == value for key, value in columns.items())
        ]
        if len(matches) != 1:
            raise AssertionError(
                f"expected exactly one row matching {columns}, found {len(matches)}"
            )
        return matches[0]

    def seed(self, rows: list[dict[str, Any]], table_id: str | None = None) -> None:
        """Put rows in without going through the code under test."""
        table = self._tables.setdefault(table_id or self.default_table, {})
        for row in rows:
            stored = {key: _stored(key, value) for key, value in row.items()}
            stored.setdefault("$id", uuid.uuid4().hex[:20])
            stored.setdefault("$createdAt", _format_dt(datetime.now(UTC)))
            stored["$updatedAt"] = _format_dt(datetime.now(UTC))
            table[str(stored["$id"])] = stored

    # -- the seam ----------------------------------------------------------

    async def _request(
        self,
        method: str,
        *,
        params: list[tuple[str, str]] | None = None,
        json_body: dict[str, Any] | None = None,
        budget_seconds: float | None = None,
        table_id: str | None = None,
        row_id: str | None = None,
    ) -> dict[str, Any]:
        del budget_seconds  # timing is `test_appwrite_tables.py`'s subject, not this one
        name = table_id or self.default_table
        self.requests.append({"method": method, "table": name, "row_id": row_id})
        table = self._tables.setdefault(name, {})

        if method == "GET":
            return self._read(table, self._decode_params(params))
        if method == "POST":
            return self._create(table, list((json_body or {}).get("rows") or []))
        if method == "PUT":
            return self._upsert(table, list((json_body or {}).get("rows") or []))
        if method == "PATCH":
            return self._patch(table, json_body or {}, row_id)
        raise AppwriteTablesError(405, f"fake has no route for {method}")

    # -- request decoding --------------------------------------------------

    @staticmethod
    def _decode_params(params: list[tuple[str, str]] | None) -> list[dict[str, Any]]:
        """The `queries[]` URL parameters a read sends, back into query objects.

        A read serialises each query itself and sends it as its own parameter.
        That half was always right; the write half was not (see
        `_encoded_queries`), which is why both are decoded here by the same
        rules rather than one being trusted.
        """
        encoded = [value for key, value in (params or []) if key == "queries[]"]
        return FakeAppwriteTables._decode_queries(encoded)

    @staticmethod
    def _decode_queries(encoded: list[Any]) -> list[dict[str, Any]]:
        if len(encoded) > _MAX_QUERIES:
            raise AppwriteTablesError(400, _QUERIES_PARAM_ERROR)
        decoded: list[dict[str, Any]] = []
        for item in encoded:
            # The shape that broke every sweep: Appwrite validates this array as
            # strings and rejects objects, with a message that reads like a size
            # complaint and is not one.
            if not isinstance(item, str) or not item or len(item) > _MAX_QUERY_CHARS:
                raise AppwriteTablesError(400, _QUERIES_PARAM_ERROR)
            decoded.append(json.loads(item))
        return decoded

    # -- reads -------------------------------------------------------------

    def _read(
        self, table: dict[str, dict[str, Any]], queries: list[dict[str, Any]]
    ) -> dict[str, Any]:
        filters, select, order_desc, limit, offset = self._split(queries)
        rows = [row for row in table.values() if self._matches(row, filters)]
        for attribute in reversed(order_desc):
            rows = self._ordered(rows, attribute)
        total = len(rows)
        page = rows[offset : offset + limit] if limit is not None else rows[offset:]
        # `total` is the full match count, not the page size -- that is what
        # makes `count_rows`' limit(1) trick work. Exact here rather than
        # saturating; see the module docstring for why that is safe.
        return {"total": total, "rows": [self._serve(row, select) for row in page]}

    @staticmethod
    def _split(
        queries: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[str] | None, list[str], int | None, int]:
        filters: list[dict[str, Any]] = []
        select: list[str] | None = None
        order_desc: list[str] = []
        limit: int | None = None
        offset = 0
        for query in queries:
            method = query.get("method")
            if method not in _MODIFIERS:
                filters.append(query)
            elif method == "select":
                select = list(query.get("values") or [])
            elif method == "orderDesc":
                order_desc.append(str(query.get("attribute")))
            elif method == "limit":
                limit = int((query.get("values") or [0])[0])
            elif method == "offset":
                offset = int((query.get("values") or [0])[0])
        return filters, select, order_desc, limit, offset

    @staticmethod
    def _ordered(rows: list[dict[str, Any]], attribute: str) -> list[dict[str, Any]]:
        present = [row for row in rows if row.get(attribute) is not None]
        absent = [row for row in rows if row.get(attribute) is None]
        present.sort(key=lambda row: row[attribute], reverse=True)
        return present + absent

    @staticmethod
    def _serve(row: dict[str, Any], select: list[str] | None) -> dict[str, Any]:
        keys = row.keys() if select is None else [*_SYSTEM_COLUMNS, *select]
        return {key: _served(key, row[key]) for key in keys if key in row}

    # -- matching ----------------------------------------------------------

    def _matches(self, row: dict[str, Any], queries: list[dict[str, Any]]) -> bool:
        return all(self._matches_one(row, query) for query in queries)

    def _matches_one(self, row: dict[str, Any], query: dict[str, Any]) -> bool:
        method = str(query.get("method"))
        if method == "or":
            return any(self._matches_one(row, sub) for sub in query.get("values") or [])
        if method == "and":
            return all(self._matches_one(row, sub) for sub in query.get("values") or [])

        attribute = str(query.get("attribute"))
        values = list(query.get("values") or [])
        actual = row.get(attribute)

        if method == "isNull":
            return actual is None
        if method == "isNotNull":
            return actual is not None

        if method in ("equal", "notEqual") and any(value is None for value in values):
            # Appwrite has no null-valued equality. `_parse_filter` is supposed
            # to turn `col=null` into isNull before it ever gets here, so this
            # is a canary on that translation, not a path production takes.
            raise AppwriteTablesError(
                400, f"Query value is invalid for attribute \"{attribute}\""
            )

        wanted = [_comparable(attribute, value) for value in values]
        if method == "equal":
            return actual is not None and actual in wanted
        if method == "notEqual":
            # SQL three-valued logic: `col != 'x'` is NULL, not true, when col
            # is NULL, so a null-valued row is not returned. See the module
            # docstring -- modelled from MariaDB, not measured against Appwrite.
            return actual is not None and actual not in wanted
        if method == "search":
            return _matches_search(actual, values[0] if values else "")
        if method == "startsWith":
            return actual is not None and str(actual).startswith(str(values[0]))
        if method in ("greaterThan", "greaterThanEqual", "lessThan", "lessThanEqual"):
            return self._compare(method, actual, wanted[0] if wanted else None)
        raise AppwriteTablesError(400, f"Invalid query method: {method}")

    @staticmethod
    def _compare(method: str, actual: object, bound: object) -> bool:
        if actual is None or bound is None:
            return False
        if method == "greaterThan":
            return bool(actual > bound)  # type: ignore[operator]
        if method == "greaterThanEqual":
            return bool(actual >= bound)  # type: ignore[operator]
        if method == "lessThan":
            return bool(actual < bound)  # type: ignore[operator]
        return bool(actual <= bound)  # type: ignore[operator]

    # -- writes ------------------------------------------------------------

    #: `bootstrap_appwrite_job_postings.py`'s `uq_source_pair`. Enforced so a
    #: lookup-before-write that stopped finding its row fails loudly here
    #: instead of quietly doubling the table.
    _UNIQUE_KEY = ("source", "source_id")

    def _insert(self, table: dict[str, dict[str, Any]], payload: dict[str, Any]) -> dict[str, Any]:
        stored = {key: _stored(key, value) for key, value in payload.items()}
        row_id = str(stored.get("$id") or uuid.uuid4().hex[:20])
        stored["$id"] = row_id
        if all(key in stored for key in self._UNIQUE_KEY):
            key = tuple(stored[column] for column in self._UNIQUE_KEY)
            clash = any(
                other["$id"] != row_id
                and tuple(other.get(column) for column in self._UNIQUE_KEY) == key
                for other in table.values()
            )
            if clash:
                raise AppwriteTablesError(
                    409, f"Document with the requested ID already exists: {key}"
                )
        now = _format_dt(datetime.now(UTC))
        stored.setdefault("$createdAt", now)
        stored["$updatedAt"] = now
        table[row_id] = stored
        return stored

    @staticmethod
    def _merge(row: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        for key, value in payload.items():
            if key.startswith("$"):
                continue
            # An explicit null clears the column rather than being skipped:
            # `_write_batch` sets `inactive_since: None` to reactivate a repost,
            # and `_MUTABLE_COLUMNS` sends a withdrawn salary band as None.
            row[key] = _stored(key, value)
        row["$updatedAt"] = _format_dt(datetime.now(UTC))
        return row

    def _create(
        self, table: dict[str, dict[str, Any]], rows: list[dict[str, Any]]
    ) -> dict[str, Any]:
        written = [self._insert(table, row) for row in rows]
        return {"total": len(written), "rows": [self._serve(row, None) for row in written]}

    def _upsert(
        self, table: dict[str, dict[str, Any]], rows: list[dict[str, Any]]
    ) -> dict[str, Any]:
        written: list[dict[str, Any]] = []
        for payload in rows:
            existing = table.get(str(payload.get("$id"))) if payload.get("$id") else None
            # Merge, not replace. See the module docstring: every "first_seen_at
            # survives a re-crawl" guarantee rests on this, and it is an
            # assumption about Appwrite rather than something measured here.
            written.append(
                self._merge(existing, payload) if existing else self._insert(table, payload)
            )
        return {"total": len(written), "rows": [self._serve(row, None) for row in written]}

    def _patch(
        self, table: dict[str, dict[str, Any]], body: dict[str, Any], row_id: str | None
    ) -> dict[str, Any]:
        data = dict(body.get("data") or {})
        if row_id is not None:
            row = table.get(row_id)
            if row is None:
                raise AppwriteTablesError(
                    404, f"Row with the requested ID could not be found: {row_id}"
                )
            return self._serve(self._merge(row, data), None)

        queries = self._decode_queries(list(body.get("queries") or []))
        filters, _select, _order, _limit, _offset = self._split(queries)
        touched = [row for row in table.values() if self._matches(row, filters)]
        for row in touched:
            self._merge(row, data)
        return {"total": len(touched), "rows": [self._serve(row, None) for row in touched]}
