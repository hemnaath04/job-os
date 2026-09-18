"""One over-long vendor string must not cost the whole sweep.

Observed 2026-09-17: the crawl recorded no run for eleven hours while 19,354
boards sat due. A board answered one bounded `varchar` column with a longer
value, Postgres raised `StringDataRightTruncationError`, and because the upsert
writes a batch inside one transaction, every statement after it failed with
`InFailedSQLTransactionError`. The sweep wrote nothing at all.

The columns are ours; the data is not. A board is free to answer
`employment_type` with a sentence or `country_code` with "United Kingdom", and
the row still describes a real job. So the fix is to clamp at the boundary
rather than widen the columns: there is no width a third party cannot exceed,
and a slightly worse row beats no rows.
"""

from __future__ import annotations

from datetime import UTC, datetime

from job_os.ingest.providers import RawPosting
from job_os.ingest.upsert import _VARCHAR_WIDTHS, _clamp, to_row

NOW = datetime(2026, 9, 17, tzinfo=UTC)


def _posting(**overrides: object) -> RawPosting:
    base = {
        "source": "ashby",
        "board_token": "elliptic",
        "external_id": "abc",
        "title": "Software Engineer Intern",
        "company_name": "Elliptic",
        "source_url": "https://jobs.ashbyhq.com/elliptic/abc",
        "jd_clean": "Build things.",
    }
    base.update(overrides)
    return RawPosting(**base)  # type: ignore[arg-type]


def test_an_over_long_value_is_cut_to_its_column() -> None:
    assert len(_clamp("employment_type", "x" * 200)) == 64
    assert len(_clamp("workplace_type", "y" * 200)) == 32
    assert len(_clamp("salary_interval", "z" * 200)) == 16


def test_a_country_that_is_not_a_code_is_dropped_rather_than_cut() -> None:
    """The first two characters of "United Kingdom" are "Un", which is not a
    country. This column exists to be matched exactly, so a wrong value is
    worse than a missing one."""
    assert _clamp("country_code", "United Kingdom") is None
    assert _clamp("country_code", "GB") == "GB"


def test_uncapped_columns_are_left_alone() -> None:
    """`title` and `jd_clean` are `text`. Clamping them would silently damage
    the fields the index actually searches."""
    assert len(_clamp("title", "a" * 5_000)) == 5_000
    assert _clamp("jd_clean", "b" * 50_000) == "b" * 50_000


def test_non_strings_pass_through_untouched() -> None:
    assert _clamp("salary_min", 120_000) == 120_000
    assert _clamp("workplace_type", None) is None


def test_every_clamped_width_matches_the_column_it_names() -> None:
    """A width that drifts from the schema is worse than no clamp: it would
    silently shorten good data while still letting the real overflow through.
    """
    from job_os.db.models.job_posting import JobPosting

    for column, width in _VARCHAR_WIDTHS.items():
        attribute = getattr(JobPosting, column)
        length = getattr(attribute.type, "length", None)
        assert length == width, f"{column}: model says {length}, clamp says {width}"


def test_a_row_built_from_a_hostile_posting_fits_every_column() -> None:
    row = to_row(
        _posting(
            employment_type="Full time, permanent, with a six month probation " * 5,
            workplace_type="Hybrid: three days in the London office each week",
            country_code="United Kingdom",
            salary_currency="Pounds Sterling",
            salary_interval="per annum before bonus",
        ),
        run_id=None,
        seen_at=NOW,
    )
    for column, width in _VARCHAR_WIDTHS.items():
        value = row.get(column)
        if isinstance(value, str):
            assert len(value) <= width, f"{column} is {len(value)} > {width}"
    assert row["country_code"] is None
    assert row["title"] == "Software Engineer Intern", "the real content survived"
