"""The stateless planner behind paste-to-enrich.

It exists because the sibling `/{job_id}/description` writes a Postgres row and
is therefore only correct when the job lives there. The live pipeline keeps
applications in Appwrite, and a card created there has no Postgres `jobs` row
at all, so writing by id answers 404 for a job the person can see on their
board. Measured on the real data: 14 of 40 active cards have no Postgres row.

This one takes the job as the caller holds it and hands back what changed.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from job_os.routers import jobs as jobs_router
from job_os.schemas.jobs import JobDescriptionParse, JobFieldsForEnrich

PARSE = {
    "location": "New York, NY",
    "remote": "onsite",
    "level": "internship",
    "function": "engineering",
    "salary_min": 52000,
    "salary_max": 110000,
    "salary_currency": "USD",
    "required_skills": ["python", "javascript"],
    "keywords": ["intern"],
}

USER = SimpleNamespace(id="user-1")


@pytest.fixture
def stub_parse(monkeypatch):
    def _install(result: dict):
        async def _fake(text: str, *, title_hint: str | None = None) -> dict:
            return result

        import job_os.services.jd_parse as jd_parse

        monkeypatch.setattr(jd_parse, "parse_jd", _fake)

    return _install


async def test_plans_a_backfill_without_needing_a_row(stub_parse):
    # No job id anywhere in the call. That is the point: an Appwrite-only card
    # has no row to name.
    stub_parse(PARSE)

    plan = await jobs_router.parse_description(
        JobDescriptionParse(jd_text="a real description", job=JobFieldsForEnrich()),
        _user=USER,
    )

    assert plan.parse_used is True
    assert plan.filled == ["Location", "Work type", "Job type", "Function", "Salary"]
    assert plan.updates["location"] == "New York, NY"
    assert plan.updates["salary_min"] == 52000
    assert plan.updates["jd_parsed"]["required_skills"] == ["python", "javascript"]


async def test_does_not_return_the_description_text(stub_parse):
    # Nothing on an Appwrite card reads it, and a full JD in every card
    # snapshot is weight for nothing.
    stub_parse(PARSE)

    plan = await jobs_router.parse_description(
        JobDescriptionParse(jd_text="a real description", job=JobFieldsForEnrich()),
        _user=USER,
    )

    assert "jd_raw" not in plan.updates
    assert "jd_clean" not in plan.updates


async def test_keeps_what_the_caller_already_has(stub_parse):
    # Same restraint as the row-writing path: fill blanks, never overwrite.
    stub_parse(PARSE)

    plan = await jobs_router.parse_description(
        JobDescriptionParse(
            jd_text="a real description",
            job=JobFieldsForEnrich(location="Boston, MA", remote="hybrid"),
        ),
        _user=USER,
    )

    assert "location" not in plan.updates
    assert "remote" not in plan.updates
    assert plan.updates["level"] == "internship"
    assert "Location" not in plan.filled


async def test_a_parse_that_learns_nothing_changes_nothing(stub_parse):
    stub_parse({})

    plan = await jobs_router.parse_description(
        JobDescriptionParse(jd_text="a real description", job=JobFieldsForEnrich()),
        _user=USER,
    )

    assert plan.updates == {}
    assert plan.filled == []
    assert plan.parse_used is False


async def test_an_already_full_job_reports_nothing_to_do(stub_parse):
    # What a second paste looks like. parse_used is true and filled is empty,
    # which is why the toast must not claim a rescore.
    stub_parse(PARSE)

    plan = await jobs_router.parse_description(
        JobDescriptionParse(
            jd_text="a real description",
            job=JobFieldsForEnrich(
                location="New York, NY",
                remote="onsite",
                level="internship",
                function="engineering",
                salary_min=52000,
                salary_max=110000,
                jd_parsed={"required_skills": ["python"]},
            ),
        ),
        _user=USER,
    )

    assert plan.filled == []
    assert plan.parse_used is True


async def test_an_empty_paste_is_refused(stub_parse):
    from fastapi import HTTPException

    stub_parse(PARSE)
    with pytest.raises(HTTPException) as exc:
        await jobs_router.parse_description(
            JobDescriptionParse(jd_text="   ", job=JobFieldsForEnrich()),
            _user=USER,
        )
    assert exc.value.status_code == 422
