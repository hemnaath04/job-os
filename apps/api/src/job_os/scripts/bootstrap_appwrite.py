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
from appwrite.services.storage import Storage
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


def ensure_private_table(
    tables: TablesDB,
    *,
    database_id: str,
    table_id: str,
    name: str,
) -> None:
    ignore_conflict(
        lambda: tables.create_table(
            database_id,
            table_id,
            name,
            permissions=[Permission.create(Role.users())],
            row_security=True,
            enabled=True,
        )
    )


def ensure_resume_workspace(tables: TablesDB, config: AppwriteAdminConfig) -> None:
    database_id = config.database_id
    table_specs = [
        (config.resumes_table_id, "Resumes"),
        (config.resume_versions_table_id, "Resume versions"),
        (config.resume_messages_table_id, "Resume revision messages"),
        (config.profile_facts_table_id, "Verified profile facts"),
        (config.fact_bullets_table_id, "Verified fact bullets"),
        (config.agent_jobs_table_id, "Agent jobs"),
    ]
    for table_id, name in table_specs:
        ensure_private_table(
            tables,
            database_id=database_id,
            table_id=table_id,
            name=name,
        )

    common_tables = [
        config.resumes_table_id,
        config.resume_versions_table_id,
        config.resume_messages_table_id,
        config.profile_facts_table_id,
        config.fact_bullets_table_id,
        config.agent_jobs_table_id,
    ]
    for table_id in common_tables:
        ensure_column(
            tables,
            database_id=database_id,
            table_id=table_id,
            key="owner_id",
            create=lambda table_id=table_id: tables.create_varchar_column(
                database_id, table_id, "owner_id", 36, True
            ),
        )
        ensure_column(
            tables,
            database_id=database_id,
            table_id=table_id,
            key="source_updated_at",
            create=lambda table_id=table_id: tables.create_datetime_column(
                database_id, table_id, "source_updated_at", True
            ),
        )
        ensure_index(
            tables,
            database_id=database_id,
            table_id=table_id,
            key="owner_updated",
            create=lambda table_id=table_id: tables.create_index(
                database_id,
                table_id,
                "owner_updated",
                TablesDBIndexType.KEY,
                ["owner_id", "source_updated_at"],
                [OrderBy.ASC, OrderBy.DESC],
            ),
        )

    for table_id in [
        config.resumes_table_id,
        config.resume_versions_table_id,
        config.profile_facts_table_id,
    ]:
        ensure_column(
            tables,
            database_id=database_id,
            table_id=table_id,
            key="archived",
            create=lambda table_id=table_id: tables.create_boolean_column(
                database_id, table_id, "archived", True
            ),
        )

    resume_table = config.resumes_table_id
    for key, create in [
        (
            "name",
            lambda: tables.create_varchar_column(database_id, resume_table, "name", 256, True),
        ),
        (
            "is_master",
            lambda: tables.create_boolean_column(
                database_id, resume_table, "is_master", True
            ),
        ),
        (
            "snapshot",
            lambda: tables.create_mediumtext_column(
                database_id, resume_table, "snapshot", True
            ),
        ),
    ]:
        ensure_column(
            tables,
            database_id=database_id,
            table_id=resume_table,
            key=key,
            create=create,
        )
    ensure_index(
        tables,
        database_id=database_id,
        table_id=resume_table,
        key="resume_library",
        create=lambda: tables.create_index(
            database_id,
            resume_table,
            "resume_library",
            TablesDBIndexType.KEY,
            ["owner_id", "archived", "source_updated_at"],
            [OrderBy.ASC, OrderBy.ASC, OrderBy.DESC],
        ),
    )

    version_table = config.resume_versions_table_id
    for key, create in [
        (
            "resume_id",
            lambda: tables.create_varchar_column(
                database_id, version_table, "resume_id", 36, True
            ),
        ),
        (
            "status",
            lambda: tables.create_enum_column(
                database_id,
                version_table,
                "status",
                ["draft", "reviewed", "final"],
                True,
            ),
        ),
        (
            "snapshot",
            lambda: tables.create_longtext_column(
                database_id, version_table, "snapshot", True
            ),
        ),
    ]:
        ensure_column(
            tables,
            database_id=database_id,
            table_id=version_table,
            key=key,
            create=create,
        )
    ensure_index(
        tables,
        database_id=database_id,
        table_id=version_table,
        key="resume_history",
        create=lambda: tables.create_index(
            database_id,
            version_table,
            "resume_history",
            TablesDBIndexType.KEY,
            ["resume_id", "archived", "source_updated_at"],
            [OrderBy.ASC, OrderBy.ASC, OrderBy.DESC],
        ),
    )

    message_table = config.resume_messages_table_id
    for key, create in [
        (
            "resume_id",
            lambda: tables.create_varchar_column(
                database_id, message_table, "resume_id", 36, True
            ),
        ),
        (
            "version_id",
            lambda: tables.create_varchar_column(
                database_id, message_table, "version_id", 36, True
            ),
        ),
        (
            "snapshot",
            lambda: tables.create_longtext_column(
                database_id, message_table, "snapshot", True
            ),
        ),
    ]:
        ensure_column(
            tables,
            database_id=database_id,
            table_id=message_table,
            key=key,
            create=create,
        )
    ensure_index(
        tables,
        database_id=database_id,
        table_id=message_table,
        key="revision_history",
        create=lambda: tables.create_index(
            database_id,
            message_table,
            "revision_history",
            TablesDBIndexType.KEY,
            ["version_id", "source_updated_at"],
            [OrderBy.ASC, OrderBy.ASC],
        ),
    )

    fact_table = config.profile_facts_table_id
    for key, create in [
        (
            "verified",
            lambda: tables.create_boolean_column(
                database_id, fact_table, "verified", True
            ),
        ),
        (
            "snapshot",
            lambda: tables.create_mediumtext_column(
                database_id, fact_table, "snapshot", True
            ),
        ),
    ]:
        ensure_column(
            tables,
            database_id=database_id,
            table_id=fact_table,
            key=key,
            create=create,
        )

    bullet_table = config.fact_bullets_table_id
    for key, create in [
        (
            "fact_id",
            lambda: tables.create_varchar_column(
                database_id, bullet_table, "fact_id", 36, True
            ),
        ),
        (
            "snapshot",
            lambda: tables.create_mediumtext_column(
                database_id, bullet_table, "snapshot", True
            ),
        ),
    ]:
        ensure_column(
            tables,
            database_id=database_id,
            table_id=bullet_table,
            key=key,
            create=create,
        )
    ensure_index(
        tables,
        database_id=database_id,
        table_id=bullet_table,
        key="fact_bullets",
        create=lambda: tables.create_index(
            database_id,
            bullet_table,
            "fact_bullets",
            TablesDBIndexType.KEY,
            ["fact_id", "source_updated_at"],
            [OrderBy.ASC, OrderBy.ASC],
        ),
    )

    job_table = config.agent_jobs_table_id
    for key, create in [
        (
            "kind",
            lambda: tables.create_enum_column(
                database_id,
                job_table,
                "kind",
                [
                    "resume_import",
                    "resume_revision",
                    "resume_review",
                    "resume_finalize",
                    "resume_tailor",
                    "profile_extract",
                    "job_parse",
                    "job_discovery",
                ],
                True,
            ),
        ),
        (
            "status",
            lambda: tables.create_enum_column(
                database_id,
                job_table,
                "status",
                ["queued", "running", "succeeded", "failed"],
                True,
            ),
        ),
        (
            "snapshot",
            lambda: tables.create_longtext_column(
                database_id, job_table, "snapshot", True
            ),
        ),
    ]:
        ensure_column(
            tables,
            database_id=database_id,
            table_id=job_table,
            key=key,
            create=create,
        )
    ensure_index(
        tables,
        database_id=database_id,
        table_id=job_table,
        key="job_queue",
        create=lambda: tables.create_index(
            database_id,
            job_table,
            "job_queue",
            TablesDBIndexType.KEY,
            ["owner_id", "status", "source_updated_at"],
            [OrderBy.ASC, OrderBy.ASC, OrderBy.DESC],
        ),
    )


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
    ensure_resume_workspace(tables, config)

    storage = Storage(config.client())
    ignore_conflict(
        lambda: storage.create_bucket(
            config.resume_files_bucket_id,
            "Resume files",
            permissions=[Permission.create(Role.users())],
            file_security=True,
            enabled=True,
            maximum_file_size=15 * 1024 * 1024,
            allowed_file_extensions=["pdf", "docx", "json"],
            encryption=True,
            antivirus=True,
        )
    )
    print(f"Appwrite bootstrap complete: {database_id}/{table_id}")  # noqa: T201


if __name__ == "__main__":
    main()
