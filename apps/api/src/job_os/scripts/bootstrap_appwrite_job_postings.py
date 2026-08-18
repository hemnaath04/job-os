"""Create the Appwrite `job_postings` table: the crawl-index cache moving off Neon.

Idempotent, like `bootstrap_appwrite.py` (reuses its `ensure_column`/`ensure_index`
helpers rather than duplicating them). Run once:

    .venv/bin/python -m job_os.scripts.bootstrap_appwrite_job_postings

No row-level permissions and no `row_security`: unlike the resume workspace
tables, `job_postings` has no owning user at all -- it is written by the ingest
crawler and read by `search_index()`, both server-side through the API key,
which bypasses row permissions entirely. Granting end-user permissions here
would just be dead weight on every one of ~35k rows.

`jd_parsed` has no native JSON column type in this Appwrite SDK version, so it
is stored as a JSON-encoded longtext string, the same convention the resume
workspace tables already use for `snapshot` (see `mirrorResumeVersionCard` in
apps/web's Appwrite lib). `search_vector` and the generated `posted_at_estimated`
column do not exist here: the former is replaced by fulltext columns + Python
scoring (see `job_index.py`), the latter is cheap to derive from
`posted_at_basis` at read time instead of storing it.
"""

from __future__ import annotations

from appwrite.enums.order_by import OrderBy
from appwrite.enums.tables_db_index_type import TablesDBIndexType
from appwrite.services.tables_db import TablesDB

from job_os.scripts.appwrite_common import AppwriteAdminConfig
from job_os.scripts.bootstrap_appwrite import ensure_column, ensure_index, ignore_conflict

#: (key, size, required, default) for every plain string/longtext column.
_STRING_COLUMNS: list[tuple[str, int, bool, str | None]] = [
    ("source", 32, True, None),
    ("source_id", 255, True, None),
    ("board_token", 255, True, None),
    ("external_id", 255, True, None),
    ("source_url", 2048, True, None),
    ("company_name", 255, True, None),
    ("company_domain", 255, False, None),
    ("company_id", 36, False, None),
    ("title", 500, True, None),
    ("location", 255, False, None),
    ("country_code", 2, False, None),
    ("workplace_type", 32, False, None),
    ("employment_type", 64, False, None),
    ("department", 255, False, None),
    ("level", 32, False, None),
    ("function", 64, False, None),
    ("salary_currency", 3, False, None),
    ("salary_interval", 16, False, None),
    ("content_hash", 64, True, None),
    ("dedupe_key", 500, True, None),
    ("canonical_id", 36, False, None),
    ("duplicate_reason", 32, False, None),
    ("posted_at_basis", 16, True, "first_crawl"),
    ("last_crawl_run_id", 36, False, None),
]

_BOOLEAN_COLUMNS: list[tuple[str, bool]] = [
    ("remote", False),
    ("anywhere", False),
    ("jd_hydrated", True),
    ("active", True),
]

_DATETIME_COLUMNS = [
    "posted_at",
    "closes_at",
    "first_seen_at",
    "last_seen_at",
    "inactive_since",
]

_INTEGER_COLUMNS = ["salary_min", "salary_max", "repost_count"]

#: (key, index_type, columns, orders)
_INDEXES: list[tuple[str, TablesDBIndexType, list[str], list[OrderBy] | None]] = [
    ("uq_source_pair", TablesDBIndexType.UNIQUE, ["source", "source_id"], None),
    ("idx_active", TablesDBIndexType.KEY, ["active"], None),
    ("idx_canonical_id", TablesDBIndexType.KEY, ["canonical_id"], None),
    ("idx_country_code", TablesDBIndexType.KEY, ["country_code"], None),
    ("idx_last_seen_at", TablesDBIndexType.KEY, ["last_seen_at"], [OrderBy.DESC]),
    ("idx_posted_at", TablesDBIndexType.KEY, ["posted_at"], [OrderBy.DESC]),
    ("idx_board", TablesDBIndexType.KEY, ["source", "board_token"], None),
    ("ft_title", TablesDBIndexType.FULLTEXT, ["title"], None),
    ("ft_company_name", TablesDBIndexType.FULLTEXT, ["company_name"], None),
    ("ft_location", TablesDBIndexType.FULLTEXT, ["location"], None),
    ("ft_jd_clean", TablesDBIndexType.FULLTEXT, ["jd_clean"], None),
]


def ensure_job_postings_table(tables: TablesDB, config: AppwriteAdminConfig) -> None:
    database_id = config.database_id
    table_id = config.job_postings_table_id

    ignore_conflict(
        lambda: tables.create_table(
            database_id,
            table_id,
            "Job postings (crawl index)",
            row_security=False,
            enabled=True,
        )
    )

    for key, size, required, default in _STRING_COLUMNS:
        ensure_column(
            tables,
            database_id=database_id,
            table_id=table_id,
            key=key,
            create=lambda key=key, size=size, required=required, default=default: (
                tables.create_string_column(
                    database_id, table_id, key, size, required, default
                )
            ),
        )

    for key, default in _BOOLEAN_COLUMNS:
        ensure_column(
            tables,
            database_id=database_id,
            table_id=table_id,
            key=key,
            create=lambda key=key, default=default: tables.create_boolean_column(
                database_id, table_id, key, True, default
            ),
        )

    for key in _DATETIME_COLUMNS:
        ensure_column(
            tables,
            database_id=database_id,
            table_id=table_id,
            key=key,
            create=lambda key=key: tables.create_datetime_column(
                database_id, table_id, key, False
            ),
        )

    for key in _INTEGER_COLUMNS:
        ensure_column(
            tables,
            database_id=database_id,
            table_id=table_id,
            key=key,
            create=lambda key=key: tables.create_integer_column(
                database_id, table_id, key, key == "repost_count", 0 if key == "repost_count" else None
            ),
        )

    ensure_column(
        tables,
        database_id=database_id,
        table_id=table_id,
        key="duplicate_score",
        create=lambda: tables.create_float_column(database_id, table_id, "duplicate_score", False),
    )

    # jd_raw/jd_clean/jd_parsed: longtext, not varchar -- job descriptions and
    # their parsed-JSON sidecar routinely exceed the 500-char-ish comfort zone
    # of a string column (measured on the live table: jd_raw+jd_clean average
    # ~10.5KB/row combined).
    for key, required, default in [
        ("jd_raw", False, None),
        ("jd_clean", True, ""),
        ("jd_parsed", True, "{}"),
    ]:
        ensure_column(
            tables,
            database_id=database_id,
            table_id=table_id,
            key=key,
            create=lambda key=key, required=required, default=default: (
                tables.create_longtext_column(database_id, table_id, key, required, default)
            ),
        )

    for key, index_type, columns, orders in _INDEXES:
        ensure_index(
            tables,
            database_id=database_id,
            table_id=table_id,
            key=key,
            create=lambda key=key, index_type=index_type, columns=columns, orders=orders: (
                tables.create_index(database_id, table_id, key, index_type, columns, orders)
            ),
        )


def main() -> None:
    config = AppwriteAdminConfig.from_environment()
    tables = TablesDB(config.client())
    ensure_job_postings_table(tables, config)
    print(f"job_postings table ready: {config.database_id}/{config.job_postings_table_id}")


if __name__ == "__main__":
    main()
