"""Appwrite Function entrypoint for Job OS resume agents.

Interactive CRUD bypasses this function and talks directly to Appwrite
TablesDB. Only expensive extraction, revision, review, and finalization work
is queued here so the browser never waits on an AI request.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from appwrite.client import Client
from appwrite.id import ID
from appwrite.input_file import InputFile
from appwrite.permission import Permission
from appwrite.query import Query
from appwrite.role import Role
from appwrite.services.storage import Storage
from appwrite.services.tables_db import TablesDB

from job_os.services.profile_extract import (
    extract_json_resume_from_docx,
    extract_json_resume_from_pdf,
)
from job_os.services.resume_engine import (
    generate_latex_source,
    review_resume,
    revise_resume,
    validate_json_resume_document,
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def _dump(value: Any) -> str:
    return json.dumps(value, default=_json_default, ensure_ascii=False)


def _field(row: Any, key: str) -> Any:
    if isinstance(row, dict):
        return row[key]
    value = getattr(row, key, None)
    if value is not None:
        return value
    return row.model_dump()[key]


def _snapshot(row: Any) -> dict[str, Any]:
    raw = _field(row, "snapshot")
    return json.loads(raw)


def _header(req: Any, name: str) -> str:
    headers = req.headers or {}
    return str(headers.get(name) or headers.get(name.lower()) or "")


def _profile_fact_specs(document: dict[str, Any]) -> list[tuple[dict[str, Any], list[str]]]:
    """Conservatively map JSON Resume sections into verified profile facts."""
    specs: list[tuple[dict[str, Any], list[str]]] = []

    def add(
        kind: str,
        title: str,
        *,
        org: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        location: str | None = None,
        source_url: str | None = None,
        payload: dict[str, Any] | None = None,
        bullets: list[str] | None = None,
    ) -> None:
        specs.append(
            (
                {
                    "kind": kind,
                    "title": title,
                    "org": org,
                    "start_date": start_date,
                    "end_date": end_date,
                    "location": location,
                    "payload": payload or {},
                    "verified": True,
                    "source_url": source_url,
                },
                [str(item) for item in bullets or [] if str(item).strip()],
            )
        )

    for entry in document.get("education", []) or []:
        title = f"{entry.get('studyType', '')} {entry.get('area', '')}".strip()
        add(
            "education",
            title or "Education",
            org=entry.get("institution"),
            start_date=entry.get("startDate"),
            end_date=entry.get("endDate"),
            location=entry.get("location"),
            source_url=entry.get("url"),
            payload={
                "courses": entry.get("courses", []),
                "score": entry.get("score"),
                "studyType": entry.get("studyType"),
                "area": entry.get("area"),
            },
        )
    for entry in document.get("work", []) or []:
        add(
            "experience",
            entry.get("position") or entry.get("name") or "Experience",
            org=entry.get("name"),
            start_date=entry.get("startDate"),
            end_date=entry.get("endDate"),
            location=entry.get("location"),
            source_url=entry.get("url"),
            payload={
                "summary": entry.get("summary"),
                "keywords": entry.get("keywords", []),
            },
            bullets=entry.get("highlights", []),
        )
    for entry in document.get("projects", []) or []:
        add(
            "project",
            entry.get("name") or "Project",
            start_date=entry.get("startDate"),
            end_date=entry.get("endDate"),
            source_url=entry.get("url"),
            payload={
                "description": entry.get("description"),
                "keywords": entry.get("keywords", []),
                "roles": entry.get("roles", []),
                "entity": entry.get("entity"),
                "type": entry.get("type"),
            },
            bullets=entry.get("highlights", []),
        )
    for group in document.get("skills", []) or []:
        category = group.get("name") or "Skills"
        for keyword in group.get("keywords", []) or []:
            add(
                "skill",
                str(keyword).strip(),
                org=category,
                payload={"category": category, "level": group.get("level")},
            )
    for section, kind, title_key, org_key, date_key in [
        ("certificates", "certification", "name", "issuer", "date"),
        ("publications", "publication", "name", "publisher", "releaseDate"),
        ("awards", "award", "title", "awarder", "date"),
    ]:
        for entry in document.get(section, []) or []:
            add(
                kind,
                entry.get(title_key) or kind.title(),
                org=entry.get(org_key),
                start_date=entry.get(date_key),
                source_url=entry.get("url"),
                payload={"summary": entry.get("summary")},
            )
    for entry in document.get("volunteer", []) or []:
        add(
            "volunteering",
            entry.get("position") or entry.get("organization") or "Volunteering",
            org=entry.get("organization"),
            start_date=entry.get("startDate"),
            end_date=entry.get("endDate"),
            source_url=entry.get("url"),
            payload={"summary": entry.get("summary")},
            bullets=entry.get("highlights", []),
        )
    return specs


def _profile_key(fact: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(fact["kind"]),
        str(fact.get("org") or "").strip().lower(),
        str(fact["title"]).strip().lower(),
    )


class Workspace:
    def __init__(self, req: Any):
        self.user_id = _header(req, "x-appwrite-user-id")
        api_key = _header(req, "x-appwrite-key")
        endpoint = os.environ["APPWRITE_FUNCTION_API_ENDPOINT"]
        project_id = os.environ["APPWRITE_FUNCTION_PROJECT_ID"]
        if not self.user_id:
            raise PermissionError("Authenticated Appwrite user required.")
        if not api_key:
            raise RuntimeError("Appwrite dynamic API key is unavailable.")
        client = (
            Client()
            .set_endpoint(endpoint)
            .set_project(project_id)
            .set_key(api_key)
        )
        self.tables = TablesDB(client)
        self.storage = Storage(client)
        self.database_id = os.getenv("APPWRITE_DATABASE_ID", "job-os")
        self.resumes_table = os.getenv("APPWRITE_RESUMES_TABLE_ID", "resumes")
        self.versions_table = os.getenv(
            "APPWRITE_RESUME_VERSIONS_TABLE_ID", "resume_versions"
        )
        self.messages_table = os.getenv(
            "APPWRITE_RESUME_MESSAGES_TABLE_ID", "resume_messages"
        )
        self.profile_facts_table = os.getenv(
            "APPWRITE_PROFILE_FACTS_TABLE_ID", "profile_facts"
        )
        self.fact_bullets_table = os.getenv(
            "APPWRITE_FACT_BULLETS_TABLE_ID", "fact_bullets"
        )
        self.jobs_table = os.getenv("APPWRITE_AGENT_JOBS_TABLE_ID", "agent_jobs")
        self.files_bucket = os.getenv(
            "APPWRITE_RESUME_FILES_BUCKET_ID", "resume_files"
        )

    @property
    def permissions(self) -> list[str]:
        role = Role.user(self.user_id)
        return [
            Permission.read(role),
            Permission.update(role),
            Permission.delete(role),
        ]

    def owned_row(self, table_id: str, row_id: str) -> Any:
        row = self.tables.get_row(self.database_id, table_id, row_id)
        owner_id = _field(row, "owner_id")
        if owner_id != self.user_id:
            raise PermissionError("Resource does not belong to this user.")
        return row

    def create_snapshot(
        self,
        table_id: str,
        *,
        row_id: str,
        snapshot: dict[str, Any],
        fields: dict[str, Any],
    ) -> Any:
        return self.tables.create_row(
            self.database_id,
            table_id,
            row_id,
            {
                "owner_id": self.user_id,
                "source_updated_at": snapshot.get("updated_at") or _now(),
                "snapshot": _dump(snapshot),
                **fields,
            },
            permissions=self.permissions,
        )

    def update_snapshot(
        self,
        table_id: str,
        row_id: str,
        snapshot: dict[str, Any],
        fields: dict[str, Any] | None = None,
    ) -> Any:
        self.owned_row(table_id, row_id)
        return self.tables.update_row(
            self.database_id,
            table_id,
            row_id,
            {
                "source_updated_at": snapshot.get("updated_at") or _now(),
                "snapshot": _dump(snapshot),
                **(fields or {}),
            },
        )

    def job(self, job_id: str) -> dict[str, Any]:
        return _snapshot(self.owned_row(self.jobs_table, job_id))

    def update_job(
        self,
        job_id: str,
        *,
        status: str,
        output: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        job = self.job(job_id)
        job.update(
            status=status,
            output=output,
            error=error,
            updated_at=_now(),
        )
        self.update_snapshot(
            self.jobs_table,
            job_id,
            job,
            {"status": status},
        )

    def verified_facts(self) -> list[dict[str, Any]]:
        facts = self.tables.list_rows(
            self.database_id,
            self.profile_facts_table,
            [
                Query.equal("owner_id", self.user_id),
                Query.equal("verified", True),
                Query.equal("archived", False),
                Query.limit(500),
            ],
            total=False,
        ).rows
        fact_ids = [
            str(_field(fact, "$id"))
            for fact in facts
        ]
        bullets: list[Any] = []
        for start in range(0, len(fact_ids), 100):
            ids = fact_ids[start : start + 100]
            if not ids:
                continue
            bullets.extend(
                self.tables.list_rows(
                    self.database_id,
                    self.fact_bullets_table,
                    [Query.equal("fact_id", ids), Query.limit(500)],
                    total=False,
                ).rows
            )
        bullets_by_fact: dict[str, list[dict[str, Any]]] = {}
        for bullet in bullets:
            payload = _snapshot(bullet)
            bullets_by_fact.setdefault(str(payload["fact_id"]), []).append(payload)
        result = []
        for fact in facts:
            payload = _snapshot(fact)
            payload["bullets"] = bullets_by_fact.get(str(payload["id"]), [])
            result.append(payload)
        return result


async def _import_resume(workspace: Workspace, payload: dict[str, Any]) -> dict[str, Any]:
    filename = str(payload["filename"])
    file_id = str(payload["file_id"])
    raw = workspace.storage.get_file_download(workspace.files_bucket, file_id)
    lowered = filename.lower()
    if lowered.endswith(".pdf"):
        document = await extract_json_resume_from_pdf(raw)
    elif lowered.endswith(".docx"):
        document = await extract_json_resume_from_docx(raw)
    elif lowered.endswith(".json"):
        document = json.loads(raw)
    else:
        raise ValueError("Only PDF, DOCX, and JSON resumes are supported.")
    validate_json_resume_document(document)

    now = _now()
    resume_id = str(uuid4())
    version_id = str(uuid4())
    is_master = bool(payload.get("is_master"))
    resume = {
        "id": resume_id,
        "name": str(payload.get("name") or filename.rsplit(".", 1)[0]),
        "base_role": None,
        "is_master": is_master,
        "source_kind": "upload",
        "source_label": "Resume library",
        "archived_at": None,
        "created_at": now,
        "updated_at": now,
    }
    version = {
        "id": version_id,
        "resume_id": resume_id,
        "json_resume": document,
        "provenance": [],
        "ats_score": None,
        "ats_report": None,
        "approved_by_user": False,
        "pdf_r2_key": None,
        "docx_r2_key": None,
        "spawned_from_job_id": None,
        "status": "draft",
        "review_score": None,
        "review_report": None,
        "parent_version_id": None,
        "source_filename": filename,
        "revision_note": "Imported resume",
        "latex_source": None,
        "finalized_at": None,
        "archived_at": None,
        "created_at": now,
        "updated_at": now,
        "source_file_id": file_id,
        "pdf_file_id": None,
    }
    workspace.create_snapshot(
        workspace.resumes_table,
        row_id=resume_id,
        snapshot=resume,
        fields={
            "name": resume["name"],
            "is_master": is_master,
            "archived": False,
        },
    )
    workspace.create_snapshot(
        workspace.versions_table,
        row_id=version_id,
        snapshot=version,
        fields={
            "resume_id": resume_id,
            "status": "draft",
            "archived": False,
        },
    )
    return {"resume": resume, "version": version}


async def _extract_profile(workspace: Workspace, payload: dict[str, Any]) -> dict[str, Any]:
    filename = str(payload["filename"])
    file_id = str(payload["file_id"])
    raw = workspace.storage.get_file_download(workspace.files_bucket, file_id)
    lowered = filename.lower()
    if lowered.endswith(".pdf"):
        document = await extract_json_resume_from_pdf(raw)
    elif lowered.endswith(".docx"):
        document = await extract_json_resume_from_docx(raw)
    elif lowered.endswith(".json"):
        document = json.loads(raw)
    else:
        raise ValueError("Only PDF, DOCX, and JSON resumes are supported.")
    validate_json_resume_document(document)

    existing_rows = workspace.tables.list_rows(
        workspace.database_id,
        workspace.profile_facts_table,
        [Query.equal("owner_id", workspace.user_id), Query.limit(500)],
        total=False,
    ).rows
    existing_keys = {_profile_key(_snapshot(row)) for row in existing_rows}
    facts_created = 0
    facts_skipped = 0
    bullets_created = 0
    timestamp = _now()
    for fact, bullets in _profile_fact_specs(document):
        key = _profile_key(fact)
        if key in existing_keys:
            facts_skipped += 1
            continue
        fact_id = str(uuid4())
        fact.update(
            id=fact_id,
            created_at=timestamp,
            updated_at=timestamp,
        )
        workspace.create_snapshot(
            workspace.profile_facts_table,
            row_id=fact_id,
            snapshot=fact,
            fields={"verified": True, "archived": False},
        )
        existing_keys.add(key)
        facts_created += 1
        for text in bullets:
            bullet_id = str(uuid4())
            bullet = {
                "id": bullet_id,
                "fact_id": fact_id,
                "text": text,
                "target_role": None,
                "metric_verified": True,
                "created_at": timestamp,
                "updated_at": timestamp,
            }
            workspace.create_snapshot(
                workspace.fact_bullets_table,
                row_id=bullet_id,
                snapshot=bullet,
                fields={"fact_id": fact_id},
            )
            bullets_created += 1

    master_rows = workspace.tables.list_rows(
        workspace.database_id,
        workspace.resumes_table,
        [
            Query.equal("owner_id", workspace.user_id),
            Query.equal("is_master", True),
            Query.limit(1),
        ],
        total=False,
    ).rows
    if not master_rows:
        resume_id = str(uuid4())
        version_id = str(uuid4())
        resume = {
            "id": resume_id,
            "name": "Master",
            "base_role": "master",
            "is_master": True,
            "source_kind": "upload",
            "source_label": "Career profile",
            "archived_at": None,
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        version = {
            "id": version_id,
            "resume_id": resume_id,
            "json_resume": document,
            "provenance": [],
            "ats_score": None,
            "ats_report": None,
            "approved_by_user": True,
            "pdf_r2_key": None,
            "docx_r2_key": None,
            "spawned_from_job_id": None,
            "status": "draft",
            "review_score": None,
            "review_report": None,
            "parent_version_id": None,
            "source_filename": filename,
            "revision_note": "Imported career profile",
            "latex_source": None,
            "finalized_at": None,
            "archived_at": None,
            "created_at": timestamp,
            "updated_at": timestamp,
            "source_file_id": file_id,
            "pdf_file_id": None,
        }
        workspace.create_snapshot(
            workspace.resumes_table,
            row_id=resume_id,
            snapshot=resume,
            fields={"name": "Master", "is_master": True, "archived": False},
        )
        workspace.create_snapshot(
            workspace.versions_table,
            row_id=version_id,
            snapshot=version,
            fields={"resume_id": resume_id, "status": "draft", "archived": False},
        )

    return {
        "facts_created": facts_created,
        "facts_skipped": facts_skipped,
        "bullets_created": bullets_created,
        "notes": ["Imported into the Appwrite evidence vault."],
    }


async def _revise_resume(workspace: Workspace, payload: dict[str, Any]) -> dict[str, Any]:
    resume_id = str(payload["resume_id"])
    version_id = str(payload["version_id"])
    message = str(payload["message"])
    version = _snapshot(workspace.owned_row(workspace.versions_table, version_id))
    if version["resume_id"] != resume_id:
        raise ValueError("Version does not belong to the selected resume.")
    output = await revise_resume(
        version["json_resume"],
        message=message,
        verified_facts=workspace.verified_facts(),
    )
    now = _now()
    user_message = {
        "id": str(uuid4()),
        "resume_id": resume_id,
        "resume_version_id": version_id,
        "role": "user",
        "content": message,
        "suggestions": [],
        "proposed_json_resume": None,
        "applied": False,
        "created_at": now,
        "updated_at": now,
    }
    assistant_message = {
        "id": str(uuid4()),
        "resume_id": resume_id,
        "resume_version_id": version_id,
        "role": "assistant",
        "content": output.assistant_message,
        "suggestions": output.suggestions,
        "proposed_json_resume": output.json_resume,
        "applied": False,
        "created_at": now,
        "updated_at": now,
    }
    for item in [user_message, assistant_message]:
        workspace.create_snapshot(
            workspace.messages_table,
            row_id=item["id"],
            snapshot=item,
            fields={"resume_id": resume_id, "version_id": version_id},
        )
    return {
        "message": output.assistant_message,
        "suggestions": output.suggestions,
        "proposal_id": assistant_message["id"],
        "proposed_json_resume": output.json_resume,
    }


async def _review_resume(
    workspace: Workspace,
    payload: dict[str, Any],
    *,
    finalize: bool,
) -> dict[str, Any]:
    version_id = str(payload["version_id"])
    version = _snapshot(workspace.owned_row(workspace.versions_table, version_id))
    report, pdf_bytes = await review_resume(version["json_resume"])
    now = _now()
    version.update(
        status="final" if finalize and report.passed else "reviewed",
        review_score=str(report.score),
        review_report=report.model_dump(mode="json"),
        updated_at=now,
    )
    if finalize:
        if not report.passed:
            raise ValueError("Resume did not pass the final quality gate.")
        pdf_file_id = ID.unique()
        workspace.storage.create_file(
            workspace.files_bucket,
            pdf_file_id,
            InputFile.from_bytes(pdf_bytes, f"{version_id}.pdf"),
            permissions=workspace.permissions,
        )
        version.update(
            approved_by_user=True,
            finalized_at=now,
            latex_source=generate_latex_source(version["json_resume"]),
            pdf_file_id=pdf_file_id,
        )
    workspace.update_snapshot(
        workspace.versions_table,
        version_id,
        version,
        {"status": version["status"]},
    )
    return {"version": version, "review": report.model_dump(mode="json")}


async def _dispatch(workspace: Workspace, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    if path == "/resume/import":
        return await _import_resume(workspace, payload)
    if path == "/profile/extract":
        return await _extract_profile(workspace, payload)
    if path == "/resume/revise":
        return await _revise_resume(workspace, payload)
    if path == "/resume/review":
        return await _review_resume(workspace, payload, finalize=False)
    if path == "/resume/finalize":
        return await _review_resume(workspace, payload, finalize=True)
    raise ValueError(f"Unsupported agent path: {path}")


async def main(context: Any) -> Any:
    try:
        workspace = Workspace(context.req)
        payload = json.loads(context.req.body or "{}")
        job_id = str(payload.pop("job_id"))
        workspace.update_job(job_id, status="running")
        try:
            result = await _dispatch(workspace, context.req.path, payload)
        except Exception as exc:
            workspace.update_job(job_id, status="failed", error=str(exc)[:2000])
            raise
        workspace.update_job(job_id, status="succeeded", output=result)
        return context.res.json({"job_id": job_id, "status": "succeeded"})
    except PermissionError as exc:
        return context.res.json({"detail": str(exc)}, 401)
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        return context.res.json({"detail": str(exc)}, 400)
    except Exception as exc:
        context.error(str(exc))
        return context.res.json({"detail": "Agent execution failed."}, 500)
