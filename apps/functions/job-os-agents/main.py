"""Appwrite Function entrypoint for Job OS resume agents.

Interactive CRUD bypasses this function and talks directly to Appwrite
TablesDB. Only expensive extraction, revision, review, and finalization work
is queued here so the browser never waits on an AI request.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime
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
from job_os.services.identity import fact_identity
from job_os.services.resume_engine import (
    generate_latex_source,
    provisional_review,
    review_resume,
    revise_resume,
    validate_json_resume_document,
)
from job_os.services.tailor import TailorBullet, TailorFact, TailorStage, run_tailor


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
    """Read a column/field off an Appwrite row across SDK shapes.

    The appwrite Python SDK (>=22) returns typed ``Row`` models, not dicts:
    system fields ($id, $createdAt) are attributes, but user-defined columns
    (owner_id, snapshot, status, ...) live under ``row.data``. Older/raw
    shapes hand back a plain dict with columns at the top level. Support both,
    so a single accessor works regardless of how a row was fetched."""
    # Plain dict (raw REST payloads, snapshots): columns at top level, with a
    # possible nested "data" bag as a fallback.
    if isinstance(row, dict):
        if key in row:
            return row[key]
        data = row.get("data")
        if isinstance(data, dict) and key in data:
            return data[key]
        raise KeyError(key)

    # Typed Row model: user columns live under .data.
    data = getattr(row, "data", None)
    if data is not None:
        if isinstance(data, dict):
            if key in data:
                return data[key]
        else:
            value = getattr(data, key, None)
            if value is not None:
                return value

    # System fields: attributes are un-prefixed ($id -> id).
    attr = key[1:] if key.startswith("$") else key
    value = getattr(row, attr, None)
    if value is not None:
        return value

    # Last resort: model_dump nests user columns under "data".
    dump = row.model_dump() if hasattr(row, "model_dump") else {}
    if isinstance(dump, dict):
        if key in dump:
            return dump[key]
        nested = dump.get("data")
        if isinstance(nested, dict) and key in nested:
            return nested[key]
    raise KeyError(key)


def _snapshot(row: Any) -> dict[str, Any]:
    raw = _field(row, "snapshot")
    return json.loads(raw)


# The resume_versions.status column is a strict Appwrite enum, so writing any
# other value fails the whole row with "Invalid document structure". The agent
# has a richer vocabulary than the column does ("needs_changes" when the
# quality/PDF pass could not clear the draft), and that detail lives in the
# snapshot JSON, which the browser actually reads. Narrow only the column value.
VERSION_STATUS_COLUMN_VALUES = frozenset({"draft", "reviewed", "final"})


def _status_column(status: str) -> str:
    """Coerce an agent status into the resume_versions.status enum."""
    return status if status in VERSION_STATUS_COLUMN_VALUES else "draft"


def _header(req: Any, name: str) -> str:
    headers = req.headers or {}
    return str(headers.get(name) or headers.get(name.lower()) or "")


def _read_payload(req: Any) -> dict[str, Any]:
    """Read the JSON request payload defensively across runtime versions.

    The open-runtimes ``body`` attribute is inconsistent: depending on the
    runtime version, whether the execution is sync or async, and the inbound
    content-type, it can arrive already parsed as a dict, as an empty dict, as
    a raw JSON string, or as bytes. ``json.loads(req.body or "{}")`` silently
    turns an empty/absent body into ``{}`` (dropping ``job_id``) or throws a
    TypeError on a pre-parsed dict, so prefer an explicitly parsed body, fall
    back to the raw string, and never hand ``json.loads`` a non-string."""
    for attr in ("body_json", "bodyJson"):
        value = getattr(req, attr, None)
        if isinstance(value, dict) and value:
            return value
    for attr in ("body_raw", "bodyRaw", "body_text", "bodyText", "body"):
        value = getattr(req, attr, None)
        if isinstance(value, (bytes, bytearray)):
            value = value.decode("utf-8")
        if isinstance(value, dict):
            if value:
                return value
            continue
        if isinstance(value, str) and value.strip():
            return json.loads(value)
    return {}


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


def _profile_key(fact: dict[str, Any]) -> tuple[str, ...]:
    """Whether this fact already exists in the vault.

    Delegates to the shared identity so importing and rendering agree on what
    counts as the same job. The old key was an exact kind/org/title match, which
    meant re-importing a resume with any rewording, or an institution spelled
    with a dash instead of a comma, registered a brand new entity. That is how
    the vault ended up with two EPAM experiences and two Northeastern degrees,
    which then rendered on the resume twice.
    """
    return fact_identity(fact)


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
        # The last progress payload written per job, so a repeated one is not
        # paid for twice. See update_job_progress.
        self._last_progress: dict[str, tuple[Any, ...]] = {}

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

    def update_job_progress(
        self,
        job_id: str,
        *,
        stage: str,
        pct: float,
        step: str | None = None,
        detail: str | None = None,
    ) -> None:
        """Persist progress onto the job snapshot without touching status.

        `stage` is the line a person reads, `step` a stable id the browser keys
        its checklist off, and `detail` one measured fact about what just
        happened. All three live inside the snapshot JSON, which is the only
        free-text column the agent_jobs table has, so this needs no migration.

        Best-effort: a progress write must never abort the run, so any failure
        here is swallowed. The browser polls the `progress` object off the job
        snapshot; the terminal status/output write still happens in update_job.

        Writing costs two REST round trips, so an event that says the same thing
        as the last one is dropped rather than paid for.
        """
        signature = (step, stage, detail, round(float(pct), 3))
        if self._last_progress.get(job_id) == signature:
            return
        try:
            job = self.job(job_id)
            job["progress"] = {
                "stage": stage,
                "pct": round(float(pct), 4),
                "step": step,
                "detail": detail,
                "updated_at": _now(),
            }
            job["updated_at"] = _now()
            # No status field passed, so the row's status column is left as-is.
            self.update_snapshot(self.jobs_table, job_id, job)
            self._last_progress[job_id] = signature
        except Exception:  # noqa: BLE001 - progress is advisory, never fatal
            pass

    def master_resume(self) -> Any | None:
        """The caller's master resume row, or None if they have not set one.

        Newest-updated first, so baseline selection for tailoring is
        deterministic rather than arbitrary should a second master ever exist.
        Scoped to owner_id, so other users' masters are never visible.
        """
        rows = self.tables.list_rows(
            self.database_id,
            self.resumes_table,
            [
                Query.equal("owner_id", self.user_id),
                Query.equal("is_master", True),
                Query.equal("archived", False),
                Query.order_desc("source_updated_at"),
                Query.limit(1),
            ],
            total=False,
        ).rows
        return rows[0] if rows else None

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
        # Join on the row columns, which are what the queries above filtered on,
        # not on the ids duplicated inside the snapshot JSON. The two agree
        # today, but bullets are the entire substance of a tailored resume: if
        # they ever drifted, a snapshot-keyed join would quietly yield a resume
        # with empty highlights and no error to explain it.
        bullets_by_fact: dict[str, list[dict[str, Any]]] = {}
        for bullet in bullets:
            payload = _snapshot(bullet)
            if not payload.get("id"):
                payload["id"] = str(_field(bullet, "$id"))
            bullets_by_fact.setdefault(
                str(_field(bullet, "fact_id")), []
            ).append(payload)
        result = []
        for fact in facts:
            payload = _snapshot(fact)
            row_id = str(_field(fact, "$id"))
            if not payload.get("id"):
                payload["id"] = row_id
            payload["bullets"] = bullets_by_fact.get(row_id, [])
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
    version_id = str(uuid4())
    is_master = bool(payload.get("is_master"))
    # Setting the master again must update the one that already exists. Creating
    # a second would make the tailoring baseline arbitrary, since a user has
    # exactly one canonical master by definition.
    existing_master = workspace.master_resume() if is_master else None
    if existing_master is not None:
        resume = _snapshot(existing_master)
        resume_id = str(resume["id"])
        resume["updated_at"] = now
        resume["source_kind"] = "upload"
        resume["source_label"] = "Resume library"
    else:
        resume_id = str(uuid4())
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
    if existing_master is not None:
        workspace.update_snapshot(
            workspace.resumes_table,
            resume_id,
            resume,
            {
                "name": str(resume.get("name") or "Master"),
                "is_master": True,
                "archived": False,
            },
        )
    else:
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

    if workspace.master_resume() is None:
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
    blocked_claims = [claim.model_dump() for claim in output.blocked_claims]
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
        "blocked_claims": blocked_claims,
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
        "blocked_claims": blocked_claims,
    }


async def _review_resume(
    workspace: Workspace,
    payload: dict[str, Any],
    *,
    finalize: bool,
) -> dict[str, Any]:
    version_id = str(payload["version_id"])
    version = _snapshot(workspace.owned_row(workspace.versions_table, version_id))
    # The reviewer needs the evidence vault to tell a verified claim from an
    # invented one. Without it, it graded the candidate's own job title and
    # coursework as fabrications.
    report, pdf_bytes = await review_resume(
        version["json_resume"], verified_facts=workspace.verified_facts()
    )
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
        {"status": _status_column(version["status"])},
    )
    return {"version": version, "review": report.model_dump(mode="json")}


def _to_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


async def _tailor_resume(
    workspace: Workspace,
    payload: dict[str, Any],
    *,
    job_id: str | None = None,
) -> dict[str, Any]:
    """Run the LangGraph tailoring agent (shared with the FastAPI backend) using
    the caller's Appwrite-stored master resume + verified facts, then persist an
    unapproved draft ResumeVersion. The JD is passed in by the browser because
    job postings still live in Postgres, not Appwrite."""
    resume_id = str(payload["resume_id"])
    jd_parsed = payload.get("jd_parsed") or {}
    jd_clean = str(payload.get("jd_clean") or "")
    spawned_from_job_id = payload.get("spawned_from_job_id")

    def on_stage(stage: TailorStage) -> None:
        """Write the agent's live stage onto the job row so the browser can poll it.

        Best-effort by way of update_job_progress, which swallows its own
        errors, so a progress write can never break the tailoring run."""
        if job_id:
            workspace.update_job_progress(
                job_id,
                stage=stage.label,
                pct=stage.pct,
                step=stage.step,
                detail=stage.detail,
            )

    def report(step: str, label: str, detail: str | None, pct: float) -> None:
        on_stage(TailorStage(step=step, label=label, detail=detail, pct=pct))

    report("load_profile", "Opening your profile", None, 0.03)

    # Target resume must belong to the caller.
    workspace.owned_row(workspace.resumes_table, resume_id)

    # Baseline is always the master resume's latest version, never a previously
    # tailored variant, so every run starts from a clean no-hallucination base.
    master_row = workspace.master_resume()
    if master_row is None:
        raise ValueError(
            "No master resume found. Import your master resume on the Profile page first."
        )
    master_id = str(_snapshot(master_row)["id"])

    version_rows = workspace.tables.list_rows(
        workspace.database_id,
        workspace.versions_table,
        [
            Query.equal("resume_id", master_id),
            Query.equal("archived", False),
            Query.limit(100),
        ],
        total=False,
    ).rows
    baselines = [_snapshot(row) for row in version_rows]
    if not baselines:
        raise ValueError(
            "Master resume has no baseline version yet. Import a resume into the master first."
        )
    baselines.sort(key=lambda v: str(v.get("created_at") or ""), reverse=True)
    master_json_resume = baselines[0]["json_resume"]

    # Verified facts + bullets already live in Appwrite; adapt them into the
    # backend-agnostic dataclasses the tailoring agent consumes.
    facts: list[TailorFact] = []
    bullets_by_fact: dict[str, list[TailorBullet]] = {}
    verified = workspace.verified_facts()
    for fact in verified:
        # Appwrite ids are opaque strings, not UUIDs: facts written by this
        # function are uuid4 strings, facts added from the browser are
        # `ID.unique()` tokens. The tailoring core takes both as strings.
        fact_id = str(fact["id"])
        facts.append(
            TailorFact(
                id=fact_id,
                kind=str(fact["kind"]),
                title=str(fact.get("title") or ""),
                org=fact.get("org"),
                start_date=_to_date(fact.get("start_date")),
                end_date=_to_date(fact.get("end_date")),
                location=fact.get("location"),
                source_url=fact.get("source_url"),
                updated_at=(
                    str(fact.get("updated_at") or "") or None
                ),
                payload=fact.get("payload") or {},
            )
        )
        bullets_by_fact[fact_id] = [
            TailorBullet(
                id=str(bullet["id"]),
                fact_id=fact_id,
                text=str(bullet.get("text") or ""),
                target_role=bullet.get("target_role"),
            )
            for bullet in (fact.get("bullets") or [])
            if bullet.get("id")
        ]

    (
        json_resume,
        provenance,
        gap_questions,
        ats_score,
        ats_report,
        agent_note,
    ) = await run_tailor(
        facts=facts,
        bullets_by_fact=bullets_by_fact,
        master_json_resume=master_json_resume,
        jd_parsed=jd_parsed,
        jd_clean=jd_clean,
        on_progress=on_stage,
    )

    now = _now()
    version_id = str(uuid4())
    status = "draft"
    review_score: str | None = None
    review_report: dict[str, Any] | None = None
    latex_source: str | None = None
    pdf_file_id: str | None = None

    # Deliberately NOT the full model review. This runtime has no LaTeX engine,
    # so the review it can run has no PDF behind it: no page count, no
    # selectable-text check, and a render_unavailable warning deducted from its
    # own score. The browser then hands the same document to the container,
    # which renders it and runs the real review, and attachReview overwrites
    # status, review_score, review_report and latex_source wholesale. Measured,
    # that model call cost ~86s of gateway time for a number with a guaranteed
    # ~100s lifespan, out of a 900s function timeout the tailor already spends
    # most of. The rules-only review is free, says plainly that it is
    # provisional, and keeps the row populated while the real one is in flight.
    try:
        report("check_draft", "Checking the draft", None, 0.95)
        review = provisional_review(json_resume)
        # The rules-only issues are worth keeping, the number is not. Writing a
        # provisional 95 that the render-backed review then corrects to 60 is the
        # score whiplash the deterministic scoring was introduced to end, only
        # now inside a single tailor. review_score stays None, which the tailor
        # view already renders as "pending" while it runs the real review.
        review_report = review.model_dump(mode="json")
        latex_source = generate_latex_source(json_resume)
        # pdf_file_id stays None on purpose. Download and the render-backed
        # review both key off it: the browser skips its own render when a PDF
        # already looks present, so writing a placeholder here would cost the
        # user the only review that can see a page count.
    except Exception as exc:  # noqa: BLE001 - the draft is usable without a score
        status = "needs_changes"
        review_score = None
        review_report = {
            "passed": False,
            "score": 0,
            "page_count": 0,
            "text_selectable": False,
            "issues": [
                {
                    "severity": "blocking",
                    "code": "review_unavailable",
                    "message": (
                        "The independent quality review could not complete. "
                        "Run Review in the resume editor before finalizing."
                    ),
                }
            ],
            "strengths": [],
            "github_projects_checked": [],
            "model_summary": str(exc)[:500],
        }

    version = {
        "id": version_id,
        "resume_id": resume_id,
        "json_resume": json_resume,
        "provenance": [p.model_dump(mode="json") for p in provenance],
        "ats_score": str(ats_score),
        "ats_report": ats_report,
        "approved_by_user": False,
        "pdf_r2_key": None,
        "docx_r2_key": None,
        "spawned_from_job_id": str(spawned_from_job_id) if spawned_from_job_id else None,
        "status": status,
        "review_score": review_score,
        "review_report": review_report,
        "parent_version_id": None,
        "source_filename": None,
        "revision_note": agent_note,
        "latex_source": latex_source,
        "finalized_at": None,
        "archived_at": None,
        "created_at": now,
        "updated_at": now,
        "source_file_id": None,
        "pdf_file_id": pdf_file_id,
        "gap_questions": [g.model_dump(mode="json") for g in gap_questions],
        "agent_note": agent_note,
    }
    report("save_draft", "Saving your draft", None, 0.98)
    workspace.create_snapshot(
        workspace.versions_table,
        row_id=version_id,
        snapshot=version,
        fields={
            "resume_id": resume_id,
            "status": _status_column(status),
            "archived": False,
        },
    )
    # Only now, with the row actually written. Reporting done before the save
    # meant a save that failed still showed the user a finished run.
    report("done", "Done", None, 1.0)
    return version


async def _dispatch(
    workspace: Workspace,
    path: str,
    payload: dict[str, Any],
    *,
    job_id: str | None = None,
) -> dict[str, Any]:
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
    if path == "/resume/tailor":
        return await _tailor_resume(workspace, payload, job_id=job_id)
    raise ValueError(f"Unsupported agent path: {path}")


async def main(context: Any) -> Any:
    try:
        workspace = Workspace(context.req)
        payload = _read_payload(context.req)
        job_id = payload.pop("job_id", None)
        if not job_id:
            body_attrs = {
                a: type(getattr(context.req, a, None)).__name__
                for a in ("body", "body_raw", "body_json", "body_text")
            }
            context.error(
                f"Missing job_id. body attr types={body_attrs} "
                f"payload keys={list(payload.keys())}"
            )
            return context.res.json(
                {"detail": "Missing job_id in request body."}, 400
            )
        job_id = str(job_id)
        workspace.update_job(job_id, status="running")
        try:
            result = await _dispatch(
                workspace, context.req.path, payload, job_id=job_id
            )
        except Exception as exc:
            workspace.update_job(job_id, status="failed", error=str(exc)[:2000])
            context.error(f"agent dispatch failed: {exc!r}")
            raise
        workspace.update_job(job_id, status="succeeded", output=result)
        return context.res.json({"job_id": job_id, "status": "succeeded"})
    except PermissionError as exc:
        return context.res.json({"detail": str(exc)}, 401)
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        context.error(f"agent 400: {exc!r}")
        return context.res.json({"detail": str(exc)}, 400)
    except Exception as exc:
        context.error(str(exc))
        return context.res.json({"detail": "Agent execution failed."}, 500)
