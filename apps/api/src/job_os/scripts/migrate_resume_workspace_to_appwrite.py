"""Copy the complete resume workspace from Neon to Appwrite and verify counts.

This is intentionally additive. It never deletes or updates Neon rows, and it
uses stable UUIDs so the migration can be safely re-run.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from appwrite.input_file import InputFile
from appwrite.permission import Permission
from appwrite.query import Query
from appwrite.role import Role
from appwrite.services.storage import Storage
from appwrite.services.tables_db import TablesDB
from sqlalchemy import select

from job_os.db.models import (
    FactBullet,
    ProfileFact,
    Resume,
    ResumeRevisionMessage,
    ResumeVersion,
    User,
)
from job_os.db.session import async_session
from job_os.scripts.appwrite_common import (
    AppwriteAdminConfig,
    appwrite_user_id_for_clerk,
)


def json_value(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {key: json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    return value


def timestamp(value: datetime | None) -> str:
    if value is None:
        return datetime.now().astimezone().isoformat()
    return value.isoformat()


def permissions(owner_id: str) -> list[str]:
    role = Role.user(owner_id)
    return [
        Permission.read(role),
        Permission.update(role),
        Permission.delete(role),
    ]


def resume_snapshot(resume: Resume) -> dict[str, Any]:
    return {
        "id": str(resume.id),
        "name": resume.name,
        "base_role": resume.base_role,
        "is_master": resume.is_master,
        "source_kind": resume.source_kind,
        "source_label": resume.source_label,
        "archived_at": json_value(resume.archived_at),
        "created_at": timestamp(resume.created_at),
        "updated_at": timestamp(resume.updated_at),
    }


def version_snapshot(version: ResumeVersion, pdf_file_id: str | None) -> dict[str, Any]:
    return {
        "id": str(version.id),
        "resume_id": str(version.resume_id),
        "json_resume": json_value(version.json_resume),
        "provenance": json_value(version.provenance),
        "ats_score": json_value(version.ats_score),
        "ats_report": json_value(version.ats_report),
        "approved_by_user": version.approved_by_user,
        "pdf_r2_key": version.pdf_r2_key,
        "docx_r2_key": version.docx_r2_key,
        "spawned_from_job_id": json_value(version.spawned_from_job_id),
        "status": version.status,
        "review_score": json_value(version.review_score),
        "review_report": json_value(version.review_report),
        "parent_version_id": json_value(version.parent_version_id),
        "source_filename": version.source_filename,
        "revision_note": version.revision_note,
        "latex_source": version.latex_source,
        "finalized_at": json_value(version.finalized_at),
        "archived_at": json_value(version.archived_at),
        "created_at": timestamp(version.created_at),
        "updated_at": timestamp(version.updated_at),
        "pdf_file_id": pdf_file_id,
    }


def message_snapshot(
    message: ResumeRevisionMessage,
    resume_id: UUID,
) -> dict[str, Any]:
    return {
        "id": str(message.id),
        "resume_id": str(resume_id),
        "resume_version_id": str(message.resume_version_id),
        "role": message.role,
        "content": message.content,
        "suggestions": json_value(message.suggestions),
        "proposed_json_resume": json_value(message.proposed_json_resume),
        "applied": message.applied,
        "created_at": timestamp(message.created_at),
        "updated_at": timestamp(message.updated_at),
    }


def fact_snapshot(fact: ProfileFact) -> dict[str, Any]:
    return {
        "id": str(fact.id),
        "kind": fact.kind,
        "title": fact.title,
        "org": fact.org,
        "start_date": json_value(fact.start_date),
        "end_date": json_value(fact.end_date),
        "location": fact.location,
        "payload": json_value(fact.payload),
        "verified": fact.verified,
        "source_url": fact.source_url,
        "created_at": timestamp(fact.created_at),
        "updated_at": timestamp(fact.updated_at),
    }


def bullet_snapshot(bullet: FactBullet) -> dict[str, Any]:
    return {
        "id": str(bullet.id),
        "fact_id": str(bullet.fact_id),
        "text": bullet.text,
        "target_role": bullet.target_role,
        "metric_verified": bullet.metric_verified,
        "created_at": timestamp(bullet.created_at),
        "updated_at": timestamp(bullet.updated_at),
    }


def upsert_snapshot(
    tables: TablesDB,
    config: AppwriteAdminConfig,
    *,
    table_id: str,
    row_id: str,
    owner_id: str,
    snapshot: dict[str, Any],
    fields: dict[str, Any],
) -> None:
    tables.upsert_row(
        config.database_id,
        table_id,
        row_id,
        {
            "owner_id": owner_id,
            "source_updated_at": snapshot["updated_at"],
            "snapshot": json.dumps(json_value(snapshot), ensure_ascii=False),
            **fields,
        },
        permissions=permissions(owner_id),
    )


async def migrate_user(
    user: User,
    *,
    tables: TablesDB,
    storage: Storage,
    config: AppwriteAdminConfig,
) -> dict[str, int]:
    owner_id = appwrite_user_id_for_clerk(user.clerk_id)
    counts = {
        "resumes": 0,
        "versions": 0,
        "messages": 0,
        "profile_facts": 0,
        "fact_bullets": 0,
    }
    async with async_session() as session:
        resumes = list(
            (
                await session.execute(select(Resume).where(Resume.user_id == user.id))
            ).scalars()
        )
        for resume in resumes:
            snapshot = resume_snapshot(resume)
            upsert_snapshot(
                tables,
                config,
                table_id=config.resumes_table_id,
                row_id=str(resume.id),
                owner_id=owner_id,
                snapshot=snapshot,
                fields={
                    "name": resume.name,
                    "is_master": resume.is_master,
                    "archived": resume.archived_at is not None,
                },
            )
            counts["resumes"] += 1

        resume_ids = [resume.id for resume in resumes]
        versions = (
            list(
                (
                    await session.execute(
                        select(ResumeVersion).where(
                            ResumeVersion.resume_id.in_(resume_ids)
                        )
                    )
                ).scalars()
            )
            if resume_ids
            else []
        )
        version_to_resume = {version.id: version.resume_id for version in versions}
        for version in versions:
            pdf_file_id: str | None = None
            if version.pdf_bytes:
                pdf_file_id = f"pdf_{str(version.id).replace('-', '')[:30]}"
                try:
                    storage.create_file(
                        config.resume_files_bucket_id,
                        pdf_file_id,
                        InputFile.from_bytes(
                            version.pdf_bytes,
                            f"{version.id}.pdf",
                        ),
                        permissions=permissions(owner_id),
                    )
                except Exception as exc:
                    if getattr(exc, "code", None) != 409:
                        raise
            snapshot = version_snapshot(version, pdf_file_id)
            upsert_snapshot(
                tables,
                config,
                table_id=config.resume_versions_table_id,
                row_id=str(version.id),
                owner_id=owner_id,
                snapshot=snapshot,
                fields={
                    "resume_id": str(version.resume_id),
                    "status": (
                        version.status
                        if version.status in {"draft", "reviewed", "final"}
                        else "reviewed"
                    ),
                    "archived": version.archived_at is not None,
                },
            )
            counts["versions"] += 1

        version_ids = list(version_to_resume)
        messages = (
            list(
                (
                    await session.execute(
                        select(ResumeRevisionMessage).where(
                            ResumeRevisionMessage.resume_version_id.in_(version_ids)
                        )
                    )
                ).scalars()
            )
            if version_ids
            else []
        )
        for message in messages:
            resume_id = version_to_resume[message.resume_version_id]
            snapshot = message_snapshot(message, resume_id)
            upsert_snapshot(
                tables,
                config,
                table_id=config.resume_messages_table_id,
                row_id=str(message.id),
                owner_id=owner_id,
                snapshot=snapshot,
                fields={
                    "resume_id": str(resume_id),
                    "version_id": str(message.resume_version_id),
                },
            )
            counts["messages"] += 1

        facts = list(
            (
                await session.execute(
                    select(ProfileFact).where(ProfileFact.user_id == user.id)
                )
            ).scalars()
        )
        for fact in facts:
            snapshot = fact_snapshot(fact)
            upsert_snapshot(
                tables,
                config,
                table_id=config.profile_facts_table_id,
                row_id=str(fact.id),
                owner_id=owner_id,
                snapshot=snapshot,
                fields={
                    "verified": fact.verified,
                    "archived": False,
                },
            )
            counts["profile_facts"] += 1

        fact_ids = [fact.id for fact in facts]
        bullets = (
            list(
                (
                    await session.execute(
                        select(FactBullet).where(FactBullet.fact_id.in_(fact_ids))
                    )
                ).scalars()
            )
            if fact_ids
            else []
        )
        for bullet in bullets:
            snapshot = bullet_snapshot(bullet)
            upsert_snapshot(
                tables,
                config,
                table_id=config.fact_bullets_table_id,
                row_id=str(bullet.id),
                owner_id=owner_id,
                snapshot=snapshot,
                fields={"fact_id": str(bullet.fact_id)},
            )
            counts["fact_bullets"] += 1

    return counts


def appwrite_count(
    tables: TablesDB,
    config: AppwriteAdminConfig,
    table_id: str,
    owner_id: str,
) -> int:
    result = tables.list_rows(
        config.database_id,
        table_id,
        [Query.equal("owner_id", owner_id), Query.limit(1)],
        total=True,
    )
    return int(result.total)


async def main() -> None:
    config = AppwriteAdminConfig.from_environment()
    client = config.client()
    tables = TablesDB(client)
    storage = Storage(client)
    requested_clerk_id = os.getenv("MIGRATION_CLERK_ID")
    async with async_session() as session:
        query = select(User)
        if requested_clerk_id:
            query = query.where(User.clerk_id == requested_clerk_id)
        users = list((await session.execute(query)).scalars())
    if not users:
        raise RuntimeError("No Neon users matched the migration request.")

    for user in users:
        expected = await migrate_user(
            user,
            tables=tables,
            storage=storage,
            config=config,
        )
        owner_id = appwrite_user_id_for_clerk(user.clerk_id)
        actual = {
            "resumes": appwrite_count(
                tables, config, config.resumes_table_id, owner_id
            ),
            "versions": appwrite_count(
                tables, config, config.resume_versions_table_id, owner_id
            ),
            "messages": appwrite_count(
                tables, config, config.resume_messages_table_id, owner_id
            ),
            "profile_facts": appwrite_count(
                tables, config, config.profile_facts_table_id, owner_id
            ),
            "fact_bullets": appwrite_count(
                tables, config, config.fact_bullets_table_id, owner_id
            ),
        }
        if expected != actual:
            raise RuntimeError(
                f"Appwrite verification failed for {owner_id}: "
                f"expected={expected}, actual={actual}"
            )
        print(f"Verified Appwrite workspace for {owner_id}: {actual}")  # noqa: T201


if __name__ == "__main__":
    asyncio.run(main())
