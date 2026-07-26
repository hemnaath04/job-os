"""Import and verify the Neon application snapshot in Appwrite."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from appwrite.exception import AppwriteException
from appwrite.permission import Permission
from appwrite.query import Query
from appwrite.role import Role
from appwrite.services.tables_db import TablesDB
from appwrite.services.users import Users

from job_os.scripts.appwrite_common import (
    AppwriteAdminConfig,
    appwrite_user_id_for_clerk,
)


def canonical_json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def ensure_user(users: Users, owner: dict[str, Any]) -> str:
    user_id = appwrite_user_id_for_clerk(owner["clerk_id"])
    try:
        users.get(user_id)
    except AppwriteException as error:
        if error.code != 404:
            raise
        users.create(
            user_id,
            email=owner["email"],
            name=owner.get("display_name"),
        )
    return user_id


def list_all_rows(tables: TablesDB, *, database_id: str, table_id: str) -> list[Any]:
    rows: list[Any] = []
    cursor: str | None = None
    while True:
        queries = [Query.limit(100), Query.order_asc("$id")]
        if cursor:
            queries.append(Query.cursor_after(cursor))
        page = tables.list_rows(
            database_id,
            table_id,
            queries=queries,
            total=False,
            ttl=0,
        )
        rows.extend(page.rows)
        if len(page.rows) < 100:
            return rows
        cursor = page.rows[-1].id


def import_snapshot(snapshot_path: Path) -> int:
    config = AppwriteAdminConfig.from_environment()
    client = config.client()
    users = Users(client)
    tables = TablesDB(client)
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    entries = snapshot.get("applications")
    if snapshot.get("schema_version") != 1 or not isinstance(entries, list):
        raise RuntimeError("Unsupported Appwrite migration snapshot")

    migrated_at = datetime.now(UTC).isoformat()
    for entry in entries:
        user_id = ensure_user(users, entry["owner"])
        application = entry["application"]
        role = Role.user(user_id)
        tables.upsert_row(
            config.database_id,
            config.applications_table_id,
            application["id"],
            data={
                "owner_id": user_id,
                "status": application["status"],
                "archived": application["archived"],
                "snapshot": canonical_json(application),
                "source_updated_at": application["updated_at"],
                "migrated_at": migrated_at,
            },
            permissions=[
                Permission.read(role),
                Permission.update(role),
                Permission.delete(role),
            ],
        )

    imported = {
        row.id: row
        for row in list_all_rows(
            tables,
            database_id=config.database_id,
            table_id=config.applications_table_id,
        )
    }
    failures: list[str] = []
    for entry in entries:
        expected = entry["application"]
        actual = imported.get(expected["id"])
        if actual is None:
            failures.append(f"{expected['id']}: missing")
            continue
        expected_hash = hashlib.sha256(canonical_json(expected).encode()).hexdigest()
        actual_hash = hashlib.sha256(actual.data["snapshot"].encode()).hexdigest()
        if (
            expected_hash != actual_hash
            or expected["status"] != actual.data["status"]
            or expected["archived"] != actual.data["archived"]
        ):
            failures.append(f"{expected['id']}: content mismatch")

    if failures:
        raise RuntimeError("Import verification failed:\n" + "\n".join(failures))
    return len(entries)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot", type=Path)
    args = parser.parse_args()
    count = import_snapshot(args.snapshot.expanduser().resolve())
    print(f"Imported and verified {count} application cards.")  # noqa: T201


if __name__ == "__main__":
    main()
