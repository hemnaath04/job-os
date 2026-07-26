"""Create the idempotent Appwrite schema for the fast application pipeline."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from appwrite.enums.order_by import OrderBy
from appwrite.enums.tables_db_index_type import TablesDBIndexType
from appwrite.exception import AppwriteException
from appwrite.permission import Permission
from appwrite.role import Role
from appwrite.services.tables_db import TablesDB

from job_os.scripts.appwrite_common import AppwriteAdminConfig


def status_value(status: Any) -> str:
    """Normalize SDK fields that vary between string and enum response models."""
    return str(getattr(status, "value", status)).lower()


def ignore_conflict(operation: Callable[[], Any]) -> Any:
    try:
        return operation()
    except AppwriteException as error:
        if error.code == 409:
            return None
        raise


def wait_for_column(tables: TablesDB, *, database_id: str, table_id: str, key: str) -> None:
    for _ in range(60):
        result = tables.list_columns(database_id, table_id, total=False)
        column = next((item for item in result.columns if item.key == key), None)
        if column is not None and status_value(column.status) == "available":
            return
        if column is not None and status_value(column.status) == "failed":
            raise RuntimeError(f"Appwrite failed to create {key}: {column.error}")
        time.sleep(1)
    raise TimeoutError(f"Timed out waiting for Appwrite column {key}")


def ensure_column(
    tables: TablesDB,
    *,
    database_id: str,
    table_id: str,
    key: str,
    create: Callable[[], Any],
) -> None:
    columns = tables.list_columns(database_id, table_id, total=False).columns
    if not any(column.key == key for column in columns):
        ignore_conflict(create)
    wait_for_column(tables, database_id=database_id, table_id=table_id, key=key)


def wait_for_index(tables: TablesDB, *, database_id: str, table_id: str, key: str) -> None:
    for _ in range(60):
        result = tables.list_indexes(database_id, table_id, total=False)
        index = next((item for item in result.indexes if item.key == key), None)
        if index is not None and status_value(index.status) == "available":
            return
        if index is not None and status_value(index.status) == "failed":
            raise RuntimeError(f"Appwrite failed to create index {key}: {index.error}")
        time.sleep(1)
    raise TimeoutError(f"Timed out waiting for Appwrite index {key}")


def ensure_index(
    tables: TablesDB,
    *,
    database_id: str,
    table_id: str,
    key: str,
    create: Callable[[], Any],
) -> None:
    indexes = tables.list_indexes(database_id, table_id, total=False).indexes
    if not any(index.key == key for index in indexes):
        ignore_conflict(create)
    wait_for_index(tables, database_id=database_id, table_id=table_id, key=key)


def main() -> None:
    config = AppwriteAdminConfig.from_environment()
    tables = TablesDB(config.client())
    database_id = config.database_id
    table_id = config.applications_table_id

    ignore_conflict(lambda: tables.create(database_id, "job-os", enabled=True))
    ignore_conflict(
        lambda: tables.create_table(
            database_id,
            table_id,
            "Application cards",
            permissions=[Permission.create(Role.users())],
            row_security=True,
            enabled=True,
        )
    )

    ensure_column(
        tables,
        database_id=database_id,
        table_id=table_id,
        key="owner_id",
        create=lambda: tables.create_varchar_column(
            database_id, table_id, "owner_id", 36, True
        ),
    )
    ensure_column(
        tables,
        database_id=database_id,
        table_id=table_id,
        key="status",
        create=lambda: tables.create_enum_column(
            database_id,
            table_id,
            "status",
            [
                "wishlist",
                "ready_to_apply",
                "applied",
                "oa_received",
                "interview_scheduled",
                "offer",
                "accepted",
                "rejected",
                "withdrawn",
                "ghosted",
            ],
            True,
        ),
    )
    ensure_column(
        tables,
        database_id=database_id,
        table_id=table_id,
        key="archived",
        create=lambda: tables.create_boolean_column(
            database_id, table_id, "archived", True
        ),
    )
    ensure_column(
        tables,
        database_id=database_id,
        table_id=table_id,
        key="snapshot",
        create=lambda: tables.create_text_column(
            database_id, table_id, "snapshot", True
        ),
    )
    ensure_column(
        tables,
        database_id=database_id,
        table_id=table_id,
        key="source_updated_at",
        create=lambda: tables.create_datetime_column(
            database_id, table_id, "source_updated_at", True
        ),
    )
    ensure_column(
        tables,
        database_id=database_id,
        table_id=table_id,
        key="migrated_at",
        create=lambda: tables.create_datetime_column(
            database_id, table_id, "migrated_at", True
        ),
    )

    ensure_index(
        tables,
        database_id=database_id,
        table_id=table_id,
        key="pipeline_order",
        create=lambda: tables.create_index(
            database_id,
            table_id,
            "pipeline_order",
            TablesDBIndexType.KEY,
            ["archived", "source_updated_at"],
            [OrderBy.ASC, OrderBy.DESC],
        ),
    )
    ensure_index(
        tables,
        database_id=database_id,
        table_id=table_id,
        key="owner_lookup",
        create=lambda: tables.create_index(
            database_id,
            table_id,
            "owner_lookup",
            TablesDBIndexType.KEY,
            ["owner_id"],
            [OrderBy.ASC],
        ),
    )
    print(f"Appwrite bootstrap complete: {database_id}/{table_id}")  # noqa: T201


if __name__ == "__main__":
    main()
