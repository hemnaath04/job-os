"""The indexed slice is one number in two files, and they have to agree.

`db/models/job_posting.py::FTS_DESCRIPTION_CHARS` is what SQLAlchemy thinks
`search_vector` is generated from. The migration writes what the database
actually generates it from. Those are separate literals on purpose -- a
migration that imported the model's constant would silently rewrite its own
meaning the next time the model changed -- which means nothing but a test
notices when they drift.

Drift here is close to invisible in practice. The column still exists, searches
still return results, and the only symptom is that recall does not match what
the code says it is. So this file also asserts the shape of the two things that
made the change safe in the first place: that the body itself is not truncated
anywhere, and that both of the migration's paths produce the same expression.
"""
from __future__ import annotations

import re
from pathlib import Path

from job_os.db.models.job_posting import (
    FTS_DESCRIPTION_CHARS,
    SEARCH_VECTOR_SQL,
)

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "20260831_0000_postings_back_to_postgres.py"
)


def _migration_source() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_the_model_and_the_migration_index_the_same_slice() -> None:
    source = _migration_source()
    match = re.search(r"^FTS_DESCRIPTION_CHARS = (\d+)$", source, re.MULTILINE)

    assert match is not None, "the migration must state the slice it writes"
    assert int(match.group(1)) == FTS_DESCRIPTION_CHARS


def test_the_slice_is_the_600_the_storage_budget_was_computed_from() -> None:
    """Asserted as a literal rather than left implicit.

    The measurement that justified this change (22,780 bytes a row at 8,000
    against 14,800 at 600, on 2,550 real crawled postings) is a function of
    this exact number. Changing it is allowed; changing it without revisiting
    `docs/ingest-index.md` is what this line is here to make awkward.
    """
    assert FTS_DESCRIPTION_CHARS == 600


def test_the_vector_still_weights_the_body_lowest() -> None:
    """The claim that a shorter slice costs deep-body recall AND NOTHING ELSE
    rests on the body being the D-weighted zone. If the body were ever weighted
    above the title, shortening it would change ranking, not just matching."""
    assert "coalesce(title, '')), 'A')" in SEARCH_VECTOR_SQL
    assert "coalesce(company_name, '')), 'B')" in SEARCH_VECTOR_SQL
    assert "coalesce(location, '')), 'C')" in SEARCH_VECTOR_SQL
    assert f"left(coalesce(jd_clean, ''), {FTS_DESCRIPTION_CHARS})), 'D')" in SEARCH_VECTOR_SQL


def test_the_write_path_does_not_truncate_the_body_itself() -> None:
    """`jd_clean` is the input to `job_enrich.enrich_job`, which produces the
    document the fit score reads. A "tidy-up" that made the stored body match
    the indexed slice would degrade every future fit score while every
    already-enriched row went on looking fine.

    The only bound applied to a body anywhere on the write path is
    `normalize.MAX_DESCRIPTION_CHARS`, which is orders of magnitude larger and
    exists so one pathological posting cannot dominate a row.
    """
    import inspect

    from job_os.ingest import hydrate, normalize, upsert

    assert normalize.MAX_DESCRIPTION_CHARS > FTS_DESCRIPTION_CHARS * 10
    for module in (upsert, hydrate):
        source = inspect.getsource(module)
        assert "FTS_DESCRIPTION_CHARS" not in source, (
            f"{module.__name__} must not slice the body to the indexed length; "
            "search_vector is a generated column and does that itself"
        )


def test_both_migration_paths_build_the_expression_the_same_way() -> None:
    """One database has the table and one does not, and the two branches have
    to converge. A `create_table` that inlined a different expression from the
    `ALTER` path would leave production and CI with differently indexed
    tables, which no test that runs on one of them could see."""
    source = _migration_source()

    assert source.count("def _search_vector_sql(") == 1
    assert "_search_vector_sql(FTS_DESCRIPTION_CHARS)" in source
    assert "_rebuild_search_vector(FTS_DESCRIPTION_CHARS)" in source
    # And the downgrade goes back to the old slice rather than dropping the
    # table, which would delete several hundred thousand crawled rows to undo a
    # column expression.
    assert "_rebuild_search_vector(PREVIOUS_FTS_DESCRIPTION_CHARS)" in source
    assert "drop_table" not in source
