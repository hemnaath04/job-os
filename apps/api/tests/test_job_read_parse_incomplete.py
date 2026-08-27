"""JobRead has to carry the flag that says the parse failed.

A timed-out parse stores `{"parse_incomplete": True}` and nothing else. JobParsed
did not declare that field, so the response schema dropped it and Pydantic filled
in its own defaults, and the API served six empty lists and two nulls: a
confident "this posting asks for nothing" in place of "we could not read it".

That is how a real incident (2026-08-27, job 40853001) was misread as the text
importer never calling the parser at all. The parser had run, timed out, and
reported itself honestly; the schema is where the honesty was lost. The scorer
was never fooled, because tailor.py reads the column off the ORM, so this is
about everything downstream of the API: the web app and the MCP tools.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from job_os.schemas.jobs import JobParsed, JobRead


def _job(jd_parsed: dict) -> dict:
    now = datetime.now(UTC)
    return {
        "id": uuid.uuid4(),
        "created_at": now,
        "updated_at": now,
        "title": "Untitled",
        "source": "text",
        "active": True,
        "jd_parsed": jd_parsed,
    }


def test_a_failed_parse_reaches_the_client_as_incomplete() -> None:
    job = JobRead.model_validate(_job({"parse_incomplete": True}))

    assert job.jd_parsed is not None
    assert job.jd_parsed.parse_incomplete is True


def test_a_real_parse_is_not_labelled_incomplete() -> None:
    job = JobRead.model_validate(
        _job({"required_skills": ["python"], "parse_incomplete": False})
    )

    assert job.jd_parsed is not None
    assert job.jd_parsed.parse_incomplete is False
    assert job.jd_parsed.required_skills == ["python"]


def test_the_flag_survives_serialisation() -> None:
    # The client reads JSON, not the model, and a field that exists on the
    # model but is excluded on the way out helps nobody.
    job = JobRead.model_validate(_job({"parse_incomplete": True}))

    assert job.model_dump()["jd_parsed"]["parse_incomplete"] is True


def test_an_older_row_without_the_flag_is_not_reported_as_failed() -> None:
    # Every job parsed before this field existed stores no such key, and a
    # missing key means the parse was fine, not that it failed.
    parsed = JobParsed.model_validate({"required_skills": ["python"]})

    assert parsed.parse_incomplete is False
