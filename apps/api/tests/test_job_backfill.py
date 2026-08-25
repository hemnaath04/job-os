"""Backfilling a thin job from a description pasted by hand.

The whole risk in this feature is doing too much. It runs against a job row
somebody is already tracking, so the tests that matter are the ones that pin
down what it must NOT touch: a value already there, the title and company the
job is recognised by, and a real parse that a later empty one would erase.
"""
from __future__ import annotations

from types import SimpleNamespace

from job_os.services.job_backfill import parse_has_signal, plan_enrichment

FULL_PARSE = {
    "location": "Lake Buena Vista, FL",
    "remote": "onsite",
    "level": "internship",
    "function": "engineering",
    "salary_min": 30,
    "salary_max": 40,
    "salary_currency": "USD",
    "required_skills": ["python", "sql"],
    "preferred_skills": [],
    "technologies": ["aws"],
    "responsibilities": [],
    "qualifications": [],
    "keywords": ["intern"],
    "title": "Parsed Title",
    "company": "Parsed Company",
}

EMPTY_PARSE = {
    "required_skills": [],
    "preferred_skills": [],
    "technologies": [],
    "responsibilities": [],
    "qualifications": [],
    "keywords": [],
}


def thin_job(**overrides):
    """A job as a sparse URL import leaves it: a title, and little else."""
    base = {
        "title": "WDW Computer Science Interns, Spring 2027 at DISNEY - Disney Careers",
        "location": None,
        "remote": None,
        "level": None,
        "function": None,
        "salary_min": None,
        "salary_max": None,
        "salary_currency": "USD",
        "jd_parsed": {},
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class TestParseSignal:
    def test_an_empty_parse_carries_no_signal(self):
        assert parse_has_signal(EMPTY_PARSE) is False
        assert parse_has_signal({}) is False
        assert parse_has_signal(None) is False

    def test_one_filled_list_is_enough(self):
        assert parse_has_signal({"keywords": ["intern"]}) is True

    def test_scalar_fields_alone_are_not_signal(self):
        # A parse that found only a location did not learn enough to be worth
        # replacing a stored parse with.
        assert parse_has_signal({"location": "Boston"}) is False


class TestBackfill:
    def test_fills_the_blanks_a_thin_import_left(self):
        plan = plan_enrichment(thin_job(), FULL_PARSE, "the description")

        assert plan.updates["location"] == "Lake Buena Vista, FL"
        assert plan.updates["remote"] == "onsite"
        assert plan.updates["level"] == "internship"
        assert plan.parse_replaced is True
        assert plan.filled == ["Location", "Work type", "Job type", "Function", "Salary"]

    def test_never_overwrites_a_value_that_is_already_there(self):
        job = thin_job(location="Boston, MA", remote="hybrid")
        plan = plan_enrichment(job, FULL_PARSE, "the description")

        assert "location" not in plan.updates
        assert "remote" not in plan.updates
        assert "Location" not in plan.filled
        assert plan.updates["level"] == "internship"

    def test_leaves_the_title_and_company_alone(self):
        # Both are how the job is recognised in the list. An ugly title is a
        # smaller surprise than a tracked job quietly renaming itself.
        plan = plan_enrichment(thin_job(), FULL_PARSE, "the description")

        assert "title" not in plan.updates
        assert "company" not in plan.updates
        assert "company_id" not in plan.updates

    def test_treats_a_blank_string_as_a_blank(self):
        plan = plan_enrichment(thin_job(location="   "), FULL_PARSE, "the description")
        assert plan.updates["location"] == "Lake Buena Vista, FL"

    def test_reports_one_salary_not_two(self):
        plan = plan_enrichment(thin_job(), FULL_PARSE, "the description")
        assert plan.filled.count("Salary") == 1
        assert plan.updates["salary_min"] == 30
        assert plan.updates["salary_max"] == 40

    def test_currency_only_rides_along_with_a_salary(self):
        parse = {**FULL_PARSE, "salary_min": None, "salary_max": None}
        plan = plan_enrichment(thin_job(), parse, "the description")

        assert "salary_currency" not in plan.updates
        assert "Salary" not in plan.filled


class TestParseIsNeverDestructive:
    def test_stores_the_description_even_when_nothing_parsed(self):
        # The point of the paste. It is what the tailor reads and what a later
        # attempt would re-parse, so it survives a parse that learned nothing.
        plan = plan_enrichment(thin_job(), EMPTY_PARSE, "the description")

        assert plan.updates["jd_raw"] == "the description"
        assert plan.updates["jd_clean"] == "the description"
        assert plan.filled == []
        assert plan.parse_replaced is False

    def test_stores_the_description_when_the_parse_failed_outright(self):
        # What the route passes when parse_jd timed out or raised.
        plan = plan_enrichment(thin_job(), {}, "the description")

        assert plan.updates["jd_clean"] == "the description"
        assert plan.parse_replaced is False

    def test_an_empty_parse_does_not_erase_a_real_one(self):
        job = thin_job(jd_parsed={"required_skills": ["python"], "keywords": ["intern"]})
        plan = plan_enrichment(job, EMPTY_PARSE, "the description")

        assert "jd_parsed" not in plan.updates
        assert job.jd_parsed == {"required_skills": ["python"], "keywords": ["intern"]}

    def test_a_real_parse_replaces_a_stored_one(self):
        job = thin_job(jd_parsed={"required_skills": ["cobol"]})
        plan = plan_enrichment(job, FULL_PARSE, "the description")

        assert plan.updates["jd_parsed"] == FULL_PARSE
        assert plan.parse_replaced is True
