"""Migrate the Appwrite `templates` table to LaTeX, and seed the eight builtins.

Idempotent, and additive by design. It adds the columns the LaTeX engine needs
and leaves `html_source` and `css_source` in place: those columns hold two rows
the user created under the old HTML renderer, and dropping a column would delete
that content for good. Instead the old rows are tagged `legacy_html` so the
picker can stop offering a look that can no longer render, without losing what
they were.

Each builtin gets a row with a stable id, and a real preview: the template
compiled with obviously invented sample data, stored as a PDF plus a first-page
PNG. The preview is the render, so a preview can never promise a look the
renderer does not produce.

    APPWRITE_PROJECT_ID=... APPWRITE_API_KEY=... \
      python -m job_os.scripts.seed_latex_templates [--dry-run]

Needs a tectonic binary, since seeding means rendering. Never touches resumes,
versions or vault facts.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from appwrite.exception import AppwriteException
from appwrite.input_file import InputFile
from appwrite.permission import Permission
from appwrite.query import Query
from appwrite.role import Role
from appwrite.services.storage import Storage
from appwrite.services.tables_db import TablesDB

from job_os.scripts.appwrite_common import AppwriteAdminConfig
from job_os.scripts.bootstrap_appwrite import ensure_column
from job_os.services.latex_catalog import BUILTIN_TEMPLATES, SAMPLE_RESUME, BuiltinTemplate
from job_os.services.latex_render import (
    build_render_model,
    builtin_directory,
    compile_pdf,
    fill_template,
    load_builtin_source,
    tectonic_binary,
)

TEMPLATES_TABLE = "templates"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _field(row: Any, key: str, default: Any = None) -> Any:
    """Read a column off an Appwrite row across the SDK's two shapes.

    Typed Row models keep user columns under `.data`; raw dict payloads keep
    them at the top level.
    """
    if isinstance(row, dict):
        if key in row:
            return row[key]
        data = row.get("data")
        if isinstance(data, dict) and key in data:
            return data[key]
        return default
    data = getattr(row, "data", None)
    if isinstance(data, dict) and key in data:
        return data[key]
    return getattr(row, key, default)


def _row_id(row: Any) -> str:
    if isinstance(row, dict):
        return str(row.get("$id") or row.get("id") or "")
    return str(getattr(row, "id", "") or getattr(row, "$id", ""))


def ensure_latex_columns(tables: TablesDB, *, database_id: str) -> None:
    """Add what a LaTeX template needs. Nothing existing is altered or removed."""
    additions: list[tuple[str, Any]] = [
        # The template source, with placeholders. Empty for a builtin: the
        # container already has the file, and a copy in the database would be a
        # second version of it to keep in sync.
        (
            "latex_source",
            lambda: tables.create_longtext_column(
                database_id, TEMPLATES_TABLE, "latex_source", False
            ),
        ),
        # builtin or custom, plus legacy_html for the two pre-LaTeX rows.
        (
            "kind",
            lambda: tables.create_varchar_column(
                database_id, TEMPLATES_TABLE, "kind", 16, False, default="custom"
            ),
        ),
        # Which engine renders it. One value today; recorded so a row is never
        # ambiguous about what it is if that changes.
        (
            "engine",
            lambda: tables.create_varchar_column(
                database_id, TEMPLATES_TABLE, "engine", 32, False, default="tectonic"
            ),
        ),
        # For a builtin, which vendored directory it names.
        (
            "builtin_key",
            lambda: tables.create_varchar_column(
                database_id, TEMPLATES_TABLE, "builtin_key", 64, False
            ),
        ),
        # The sample render itself, alongside the PNG in preview_file_id.
        (
            "preview_pdf_file_id",
            lambda: tables.create_varchar_column(
                database_id, TEMPLATES_TABLE, "preview_pdf_file_id", 64, False
            ),
        ),
    ]
    for key, create in additions:
        ensure_column(
            tables,
            database_id=database_id,
            table_id=TEMPLATES_TABLE,
            key=key,
            create=create,
        )
        print(f"  column {key}: ready")


def rasterize_first_page(pdf_bytes: bytes) -> bytes | None:
    """A PNG of page one, for the picker's grid. None if no rasteriser is around.

    Six PDFs embedded in a page is slow and inconsistent across browsers, and a
    thumbnail has to be an image to be laid out like one. pdftoppm on Linux,
    sips on macOS, and no thumbnail rather than a fake one if neither exists.
    """
    with tempfile.TemporaryDirectory(prefix="preview-") as raw_tmp:
        tmp = Path(raw_tmp)
        source = tmp / "preview.pdf"
        source.write_bytes(pdf_bytes)

        pdftoppm = shutil.which("pdftoppm")
        if pdftoppm:
            subprocess.run(  # noqa: S603 - fixed argv, no shell
                [pdftoppm, "-png", "-r", "110", "-f", "1", "-l", "1",
                 str(source), str(tmp / "out")],
                check=True,
                capture_output=True,
            )
            rendered = sorted(tmp.glob("out*.png"))
            if rendered:
                return rendered[0].read_bytes()

        sips = shutil.which("sips")
        if sips:
            target = tmp / "out.png"
            subprocess.run(  # noqa: S603 - fixed argv, no shell
                [sips, "-s", "format", "png", "-Z", "1400", "--out", str(target),
                 str(source)],
                check=True,
                capture_output=True,
            )
            if target.is_file():
                return target.read_bytes()

    return None


def _upload(
    storage: Storage,
    *,
    bucket_id: str,
    file_id: str,
    data: bytes,
    filename: str,
) -> str:
    """Replace a preview file, so re-running the seed does not pile up copies.

    Read by any signed-in user, because a builtin's preview belongs to the app
    rather than to whoever happened to run this.
    """
    try:
        storage.delete_file(bucket_id, file_id)
    except AppwriteException as error:
        if error.code != 404:
            raise
    storage.create_file(
        bucket_id,
        file_id,
        InputFile.from_bytes(data, filename),
        permissions=[Permission.read(Role.users())],
    )
    return file_id


def _snapshot(
    spec: BuiltinTemplate,
    *,
    row_id: str,
    preview_file_id: str | None,
    preview_pdf_file_id: str,
    timestamp: str,
) -> dict[str, Any]:
    """What the browser reads. Keep in step with ResumeTemplate in the web app."""
    return {
        "id": row_id,
        "name": spec.name,
        "description": spec.description,
        "kind": "builtin",
        "engine": "tectonic",
        "builtin_key": spec.key,
        "columns": spec.columns,
        "ats_note": spec.ats_note,
        "tags": list(spec.tags),
        "author": spec.author,
        "licence": spec.licence,
        "upstream": spec.upstream,
        "changes": spec.changes,
        "preview_file_id": preview_file_id,
        "preview_pdf_file_id": preview_pdf_file_id,
        "created_from_resume_id": None,
        "source_file_id": None,
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def seed_builtins(
    tables: TablesDB,
    storage: Storage,
    config: AppwriteAdminConfig,
    *,
    owner_id: str,
    dry_run: bool,
) -> None:
    model = build_render_model(SAMPLE_RESUME)
    for spec in BUILTIN_TEMPLATES:
        row_id = f"builtin-{spec.key}"
        pdf_bytes = compile_pdf(
            fill_template(load_builtin_source(spec.key), model),
            assets_dir=builtin_directory(spec.key),
        )
        png_bytes = rasterize_first_page(pdf_bytes)
        print(
            f"  {spec.key}: compiled {len(pdf_bytes)} bytes"
            + (f", thumbnail {len(png_bytes)} bytes" if png_bytes else ", no thumbnail")
        )
        if dry_run:
            continue

        preview_pdf_file_id = _upload(
            storage,
            bucket_id=config.resume_files_bucket_id,
            file_id=f"preview-{spec.key}-pdf",
            data=pdf_bytes,
            filename=f"{spec.key}-preview.pdf",
        )
        preview_file_id = (
            _upload(
                storage,
                bucket_id=config.resume_files_bucket_id,
                file_id=f"preview-{spec.key}-png",
                data=png_bytes,
                filename=f"{spec.key}-preview.png",
            )
            if png_bytes
            else None
        )

        timestamp = _now()
        snapshot = _snapshot(
            spec,
            row_id=row_id,
            preview_file_id=preview_file_id,
            preview_pdf_file_id=preview_pdf_file_id,
            timestamp=timestamp,
        )
        data = {
            "owner_id": owner_id,
            "name": spec.name,
            "archived": False,
            "kind": "builtin",
            "engine": "tectonic",
            "builtin_key": spec.key,
            # The LaTeX itself stays in the container. A copy here would be a
            # second version of the same file, free to drift from the one that
            # actually renders.
            "latex_source": "",
            # Required columns from the retired HTML renderer. Left empty rather
            # than deleted: the two rows that still have real content in them
            # are the user's, and this migration does not destroy them.
            "html_source": "",
            "css_source": "",
            "preview_file_id": preview_file_id,
            "preview_pdf_file_id": preview_pdf_file_id,
            "source_updated_at": timestamp,
            "snapshot": json.dumps(snapshot),
        }
        # Readable by any signed-in user, and writable by none of them: a builtin
        # is part of the app, not somebody's document.
        permissions = [Permission.read(Role.users())]
        try:
            tables.create_row(
                config.database_id, TEMPLATES_TABLE, row_id, data, permissions=permissions
            )
            print(f"  {spec.key}: row created")
        except AppwriteException as error:
            if error.code != 409:
                raise
            tables.update_row(
                config.database_id, TEMPLATES_TABLE, row_id, data, permissions=permissions
            )
            print(f"  {spec.key}: row updated")


def tag_legacy_html_rows(
    tables: TablesDB, config: AppwriteAdminConfig, *, dry_run: bool
) -> None:
    """Mark the pre-LaTeX rows so the picker stops offering them.

    Not archived and not deleted. Their html_source is still there, and if the
    HTML renderer ever came back so would they; meanwhile a template that cannot
    render should not be selectable.
    """
    rows = tables.list_rows(
        config.database_id,
        TEMPLATES_TABLE,
        queries=[Query.limit(200)],
        total=False,
    ).rows
    for row in rows:
        row_id = _row_id(row)
        if row_id.startswith("builtin-"):
            continue
        if str(_field(row, "kind") or "") in {"legacy_html", "custom"}:
            continue
        if not str(_field(row, "html_source") or ""):
            continue
        print(f"  legacy row {row_id} ({_field(row, 'name')}): tagging legacy_html")
        if dry_run:
            continue
        tables.update_row(
            config.database_id,
            TEMPLATES_TABLE,
            row_id,
            {"kind": "legacy_html", "engine": "weasyprint"},
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compile the previews and report, but write nothing to Appwrite.",
    )
    parser.add_argument(
        "--owner-id",
        default=None,
        help="Appwrite user id recorded as the owner of the builtin rows. "
        "Defaults to the owner of an existing row, since owner_id is required.",
    )
    args = parser.parse_args()

    if tectonic_binary() is None:
        print(
            "No tectonic binary on PATH. Seeding renders every preview, so it "
            "needs one: brew install tectonic",
            file=sys.stderr,
        )
        return 2

    config = AppwriteAdminConfig.from_environment()
    client = config.client()
    tables = TablesDB(client)
    storage = Storage(client)

    print(f"templates table: {config.database_id}/{TEMPLATES_TABLE}")
    if not args.dry_run:
        ensure_latex_columns(tables, database_id=config.database_id)

    owner_id = args.owner_id
    if owner_id is None:
        existing = tables.list_rows(
            config.database_id, TEMPLATES_TABLE, queries=[Query.limit(1)], total=False
        ).rows
        if existing:
            owner_id = _field(existing[0], "owner_id")
        else:
            print(
                "No existing template row to take an owner id from. Pass "
                "--owner-id.",
                file=sys.stderr,
            )
            return 2
    print(f"owner_id for builtin rows: {owner_id}")

    seed_builtins(tables, storage, config, owner_id=str(owner_id), dry_run=args.dry_run)
    tag_legacy_html_rows(tables, config, dry_run=args.dry_run)
    print("done" + (" (dry run, nothing written)" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
