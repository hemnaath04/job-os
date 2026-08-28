"""The single-job route carries the description; the list route does not.

Jobs live in Postgres and the tailor agent runs as an Appwrite Function, so the
browser is the only thing that can carry a JD from one to the other. It could not
read the text here at all -- `JobRead` never exposed `jd_clean` -- so every
production run on the Appwrite path sent the empty string, and the agent's writer
and analyst saw the parsed JSON plus an empty `<jd>` block: no requirement in the
employer's own phrasing to match wording against, and no location-in-title.

Kept off `JobRead` rather than added to it because `list_jobs` returns up to 200
of those to fill a picker, and a full description on each is megabytes for
nothing.
"""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from job_os.schemas.jobs import JD_CLEAN_MAX_CHARS, JobDetailRead, JobRead


def _row(jd_clean: str | None) -> SimpleNamespace:
    """A job row as the ORM hands one over, thin enough for from_attributes."""
    now = datetime.now(UTC)
    return SimpleNamespace(
        id=uuid4(),
        created_at=now,
        updated_at=now,
        company=None,
        title="Software Engineer Intern (AI Platform) 2027 Summer",
        level=None,
        function=None,
        location=None,
        remote=None,
        salary_min=None,
        salary_max=None,
        salary_currency="USD",
        source="url",
        source_url="https://jobs.bytedance.com/en/position/1",
        posted_at=None,
        closes_at=None,
        active=True,
        jd_parsed=None,
        jd_clean=jd_clean,
    )


def test_the_detail_schema_carries_the_posting_text() -> None:
    detail = JobDetailRead.model_validate(_row("Beijing, China. You will write Go."))
    assert detail.jd_clean == "Beijing, China. You will write Go."


def test_the_list_schema_still_does_not() -> None:
    # The reason this is two schemas. A picker asking for 200 jobs must not
    # also be asking for 200 job descriptions.
    assert "jd_clean" not in JobRead.model_fields
    assert "jd_clean" in JobDetailRead.model_fields


def test_a_row_with_no_description_reads_as_empty_not_null() -> None:
    # `jd_clean` is NOT NULL on the table, but a URL import writes "" into it
    # before the background fetch lands, and the callers here all treat the
    # field as a plain string.
    assert JobDetailRead.model_validate(_row("")).jd_clean == ""
    assert JobDetailRead.model_validate(_row(None)).jd_clean == ""


def test_a_scraped_page_is_capped_at_what_the_prompt_would_read() -> None:
    # A scraped posting arrives with a whole site footer attached, and the only
    # caller that wants this field is carrying it to a prompt that truncates it.
    long_jd = "x" * (JD_CLEAN_MAX_CHARS + 5000)
    assert len(JobDetailRead.model_validate(_row(long_jd)).jd_clean) == JD_CLEAN_MAX_CHARS


def test_the_cap_matches_the_prompt_it_exists_for() -> None:
    """The two constants are kept equal by hand, so something has to check.

    schemas/jobs.py deliberately does not import the tailor service (it pulls in
    the Anthropic client and the whole agent), which is the only reason this is
    two constants rather than one.
    """
    from job_os.services.tailor import JD_CLEAN_PROMPT_CHARS

    assert JD_CLEAN_MAX_CHARS == JD_CLEAN_PROMPT_CHARS
