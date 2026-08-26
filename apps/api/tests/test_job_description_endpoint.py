"""The paste-a-description endpoint, through the route, against a real session.

These exist because the unit tests for `plan_enrichment` could not have caught
the bug that shipped: the planning was correct and the write was correct, and
the endpoint still returned a 500, because it fell over serialising its own
answer. That failure only exists once a real flush has run against a real
database, so a test that never touches one cannot see it.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from job_os.db.models import Company, Job
from job_os.routers.jobs import add_description
from job_os.schemas.jobs import JobDescriptionPaste

JD_TEXT = (
    "Software Engineering Intern, Spring 2027. Boston, MA, hybrid. "
    "You will write Python and SQL and work with Docker."
)

PARSE_RESULT = {
    "location": "Boston, MA",
    "remote": "hybrid",
    "level": "internship",
    "function": "engineering",
    "required_skills": ["python", "sql"],
    "technologies": ["docker"],
    "keywords": ["intern"],
}


async def _thin_job(session) -> Job:
    """A job as a sparse URL import leaves it: a title, and little else."""
    company = Company(name=f"Test Co {uuid.uuid4().hex[:8]}")
    session.add(company)
    await session.flush()

    job = Job(
        company_id=company.id,
        title="Software Engineering Intern at Example - Careers",
        jd_raw="",
        jd_clean="",
        jd_parsed={},
        source="url",
        source_url=f"https://example.com/{uuid.uuid4().hex}",
    )
    session.add(job)
    await session.flush()
    return job


@pytest.fixture
def stub_parse(monkeypatch):
    """`parse_jd` is imported inside the handler, so patch the module attribute."""

    def _install(result: dict):
        async def fake_parse_jd(text: str, **_kwargs: object) -> dict:
            return result

        import job_os.services.jd_parse as jd_parse

        monkeypatch.setattr(jd_parse, "parse_jd", fake_parse_jd)

    return _install


async def test_returns_the_enriched_job_instead_of_failing_to_serialise_it(
    db_session, stub_parse
):
    # The regression. This endpoint UPDATEs a row where the others INSERT, and
    # `updated_at` carries onupdate=func.now(), so the flush leaves it expired.
    # Reading it back while serialising raised MissingGreenlet, which reached
    # the user as a bare 500 AFTER the work had already been done and then
    # rolled back. Touching every field of the response is the assertion.
    job = await _thin_job(db_session)
    stub_parse(PARSE_RESULT)

    result = await add_description(
        job.id,
        JobDescriptionPaste(jd_text=JD_TEXT),
        _user=SimpleNamespace(id=uuid.uuid4()),
        session=db_session,
    )

    assert result.job.updated_at is not None
    assert result.job.created_at is not None
    assert result.job.company is not None
    assert result.parse_used is True
    assert result.filled == ["Location", "Work type", "Job type", "Function"]


async def test_the_paste_is_actually_written_to_the_row(db_session, stub_parse):
    job = await _thin_job(db_session)
    stub_parse(PARSE_RESULT)

    result = await add_description(
        job.id,
        JobDescriptionPaste(jd_text=JD_TEXT),
        _user=SimpleNamespace(id=uuid.uuid4()),
        session=db_session,
    )

    assert result.job.location == "Boston, MA"
    assert result.job.remote == "hybrid"
    assert job.jd_clean == JD_TEXT
    assert job.jd_parsed["required_skills"] == ["python", "sql"]


async def test_a_parse_that_learns_nothing_still_saves_the_description(
    db_session, stub_parse
):
    # The honest path: the description is the durable half and is stored even
    # when nothing could be read out of it, and the response says so rather
    # than dressing it up.
    job = await _thin_job(db_session)
    stub_parse({})

    result = await add_description(
        job.id,
        JobDescriptionPaste(jd_text=JD_TEXT),
        _user=SimpleNamespace(id=uuid.uuid4()),
        session=db_session,
    )

    assert result.filled == []
    assert result.parse_used is False
    assert result.job.updated_at is not None
    assert job.jd_clean == JD_TEXT


async def test_a_missing_job_is_a_404_not_a_500(db_session, stub_parse):
    from fastapi import HTTPException

    stub_parse(PARSE_RESULT)
    with pytest.raises(HTTPException) as exc:
        await add_description(
            uuid.uuid4(),
            JobDescriptionPaste(jd_text=JD_TEXT),
            _user=SimpleNamespace(id=uuid.uuid4()),
            session=db_session,
        )
    assert exc.value.status_code == 404
