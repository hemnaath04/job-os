"""The Appwrite permission model is the only thing separating two users' resumes.

The browser talks to Appwrite TablesDB directly with the end user's own session,
so nothing server-side is standing in the way of a read. What keeps user A out of
user B's rows is entirely the permission model these tables are created with, and
that model has two halves that only work together:

  1. Table-level permissions grant CREATE to `users` and nothing else. No
     table-wide read, so being signed in is not enough to read anyone's rows.
  2. Row security is ON, which is what makes the per-row owner permissions
     (written by the app and the agent function) actually enforced.

Half of that is worthless alone, and the dangerous direction is silent: with row
security OFF, Appwrite ignores per-row permissions entirely and falls back to the
table-level ones. Nothing errors. The app keeps working for the person testing it,
because they only ever read their own rows.

These tests do not talk to Appwrite. They pin the arguments the bootstrap makes,
so a table added later without row security fails here rather than in production.
A real two-user read test needs live credentials and belongs in an integration
suite; this is the part that can run on every commit.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from job_os.scripts import bootstrap_appwrite as boot


@dataclass
class _RecordedTable:
    database_id: str
    table_id: str
    name: str
    kwargs: dict[str, Any]


@dataclass
class _FakeTables:
    """Records create_table calls and answers the "does it exist" reads."""

    created: list[_RecordedTable] = field(default_factory=list)

    def create_table(
        self, database_id: str, table_id: str, name: str, **kwargs: Any
    ) -> dict[str, Any]:
        self.created.append(
            _RecordedTable(
                database_id=database_id, table_id=table_id, name=name, kwargs=kwargs
            )
        )
        return {"$id": table_id}


def _create_one(table_id: str = "resumes") -> _RecordedTable:
    tables = _FakeTables()
    boot.ensure_private_table(
        tables,  # type: ignore[arg-type]
        database_id="job-os",
        table_id=table_id,
        name="Test table",
    )
    assert len(tables.created) == 1
    return tables.created[0]


def test_row_security_is_on() -> None:
    """Without this, per-row owner permissions are ignored by Appwrite."""
    assert _create_one().kwargs.get("row_security") is True


def test_table_grants_create_only() -> None:
    """One permission, and it is CREATE for the users role.

    Any read, update or delete at the table level would apply to every signed-in
    user's rows at once, which is the whole failure this model exists to avoid.
    """
    permissions = _create_one().kwargs.get("permissions")
    assert isinstance(permissions, list)
    assert len(permissions) == 1

    granted = str(permissions[0])
    assert granted.startswith("create(")
    assert "users" in granted
    for verb in ("read(", "update(", "delete("):
        assert verb not in granted


@pytest.mark.parametrize("verb", ["read", "update", "delete"])
def test_no_table_wide_access_of_any_kind(verb: str) -> None:
    """Spelled out per verb so a failure names which one leaked."""
    permissions = [str(p) for p in _create_one().kwargs.get("permissions", [])]
    assert not any(p.startswith(f"{verb}(") for p in permissions)


def test_usage_counters_uses_the_private_helper() -> None:
    """The spend-cap table holds no resume text, but it is still per-user data.

    It was added later than the workspace tables, which is exactly when a table
    tends to get created with its own hand-written permissions.
    """
    tables = _FakeTables()
    calls: list[dict[str, Any]] = []

    original = boot.ensure_private_table

    def _spy(t: Any, **kwargs: Any) -> None:
        calls.append(kwargs)
        return original(t, **kwargs)

    boot.ensure_private_table = _spy  # type: ignore[assignment]
    try:
        # ensure_column needs a real client; stop after the table is created.
        with pytest.raises(Exception):
            boot.ensure_usage_counters(tables, "job-os")  # type: ignore[arg-type]
    finally:
        boot.ensure_private_table = original  # type: ignore[assignment]

    assert calls, "usage_counters must be created through ensure_private_table"
    assert tables.created[0].kwargs.get("row_security") is True


def test_create_table_has_exactly_one_call_site() -> None:
    """A structural check, because the behavioural ones can be side-stepped.

    Every table has to be born through `ensure_private_table`, so that helper is
    the single place the permission model is decided. A second `create_table`
    call anywhere in this module would be a table whose permissions nobody
    reviewed, and this test is what makes that visible in a diff.
    """
    import inspect

    source = inspect.getsource(boot)
    occurrences = source.count("tables.create_table(")
    assert occurrences == 1, (
        f"found {occurrences} create_table call sites; every table must be "
        "created through ensure_private_table so the permission model is "
        "decided in exactly one place"
    )
