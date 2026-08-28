"""A title hit outranks a JD-body hit, and the pool query still reads whole rows.

Unit tests against the ranking functions rather than the Appwrite-backed
`search_index` in `test_job_index_ranking.py`: the behaviour under test is
entirely local (which weight a row gets, and which columns the pool asks for),
so it should be checkable without a live table and an API key.

The bug these cover, from a live search for "software engineer intern": the
first page opened with Glean's Platform Security Engineer, a Principal
Enterprise Technology Architect, a Localization Manager, an EA to the CRO and a
Director of Litigation. None of those is titled anything like the query. They
matched because Appwrite's fulltext index is built over `search_text`, which
concatenates the title with the first 8000 characters of `jd_clean` -- so a
posting that merely mentions the internship programme in its body matched
exactly as strongly as one titled for it, and `retrieve_score` was a flat 1.0
for both.
"""
from __future__ import annotations

import inspect
import re
import uuid
from datetime import UTC, datetime, timedelta

from job_os.services.job_index import (
    BODY_ONLY_MATCH_WEIGHT,
    NO_KEYWORDS_WEIGHT,
    POOL_COLUMNS,
    TITLE_MATCH_WEIGHT,
    _apply_mix_and_rank,
    _row_to_tuple,
    _title_weight,
    ranking_constants,
)

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)


def row(
    *,
    title: str,
    company: str = "Acme",
    age_days: float = 1.0,
    jd: str = "Build and operate the service.",
) -> dict[str, object]:
    """One Appwrite `job_postings` row, with only the columns the pool selects."""
    posted = NOW - timedelta(days=age_days)
    return {
        "source_posting_id": str(uuid.uuid4()),
        "source": "greenhouse",
        "source_id": f"acme:{abs(hash(title)) % 10**7}",
        "source_url": "https://boards.greenhouse.io/acme/jobs/1",
        "title": title,
        "company_name": company,
        "company_domain": f"{company.lower()}.test",
        "location": "San Francisco, CA",
        "country_code": "US",
        "remote": False,
        "department": None,
        "employment_type": None,
        "salary_min": None,
        "salary_max": None,
        "salary_currency": None,
        "jd_hydrated": True,
        "posted_at": posted.isoformat(),
        "posted_at_basis": "published",
        "posted_at_estimated": False,
        "first_seen_at": posted.isoformat(),
        "last_seen_at": NOW.isoformat(),
        "active": True,
        "inactive_since": None,
        "repost_count": 0,
        # Deliberately not read by the pool path, and deliberately present here
        # so a test row is a realistic row.
        "jd_clean": jd,
    }


# ---------------------------------------------------------------------------
# _title_weight
# ---------------------------------------------------------------------------


def test_a_title_hit_and_a_body_only_hit_are_not_the_same_thing():
    assert _title_weight("Software Engineering Intern", ["software engineer intern"]) == (
        TITLE_MATCH_WEIGHT
    )
    assert _title_weight("Director of Litigation", ["software engineer intern"]) == (
        BODY_ONLY_MATCH_WEIGHT
    )
    assert BODY_ONLY_MATCH_WEIGHT < TITLE_MATCH_WEIGHT


def test_the_words_may_be_in_any_order_and_punctuated_however():
    # The same rule the live sources filter with and the smart-search prompt is
    # written against: every word of the phrase, anywhere in the title.
    for title in ["AI/ML Engineer Intern", "Intern, ML Engineer", "ml-engineer (intern)"]:
        assert _title_weight(title, ["ml engineer intern"]) == TITLE_MATCH_WEIGHT


def test_the_phrases_are_alternatives_so_any_one_of_them_is_enough():
    weight = _title_weight(
        "Software Engineering Co-op",
        ["software engineer intern", "software engineering co-op"],
    )
    assert weight == TITLE_MATCH_WEIGHT


def test_a_missing_word_is_a_miss_not_a_partial_credit():
    # "ai" is not in this title, so this is a body-only match, exactly as
    # `no-key-sources.ts` would decide it.
    assert _title_weight("Machine Learning Engineer Intern", ["ai engineer intern"]) == (
        BODY_ONLY_MATCH_WEIGHT
    )


def test_browsing_with_no_keywords_leaves_every_row_alone():
    assert _title_weight("Anything At All", []) == NO_KEYWORDS_WEIGHT
    assert _title_weight("Anything At All", ["", "  "]) == NO_KEYWORDS_WEIGHT
    assert NO_KEYWORDS_WEIGHT == TITLE_MATCH_WEIGHT


def test_engineering_and_engineer_are_the_same_word_for_ranking():
    # Employers write both, and a query cannot spell them both. The web app's
    # `relevance.ts` stems the same three cases the same way.
    assert _title_weight("Software Engineering Internship", ["software engineer intern"]) == (
        TITLE_MATCH_WEIGHT
    )
    assert _title_weight("Data Analyst Intern", ["data analysts interns"]) == TITLE_MATCH_WEIGHT
    # And it does not go so far as to fold unrelated words together.
    assert _title_weight("Internal Audit Associate", ["intern"]) == BODY_ONLY_MATCH_WEIGHT


def test_a_row_with_no_title_is_a_miss_rather_than_a_crash():
    assert _title_weight(None, ["software engineer intern"]) == BODY_ONLY_MATCH_WEIGHT


# ---------------------------------------------------------------------------
# the effect on rank
# ---------------------------------------------------------------------------


def rank_titles(titles: list[str], keywords: list[str], **kw: float) -> list[str]:
    rows = [row(title=t, company=t, **kw) for t in titles]  # type: ignore[arg-type]
    hits = _apply_mix_and_rank(
        [_row_to_tuple(r, NOW, keywords) for r in rows],
        limit=len(rows),
        offset=0,
        explain=True,
        matched_keywords=bool(keywords),
    )
    return [h.title for h in hits]


def test_the_qa_page_puts_the_internship_first():
    order = rank_titles(
        [
            "Platform Security Engineer",
            "Principal Enterprise Technology Architect",
            "Director of Litigation",
            "Software Engineering Intern",
        ],
        ["software engineer intern"],
    )
    assert order[0] == "Software Engineering Intern"


def test_a_week_old_title_hit_beats_a_body_hit_posted_today():
    rows = [
        row(title="Director of Litigation", company="Old", age_days=0.0),
        row(title="Software Engineering Intern", company="New", age_days=7.0),
    ]
    hits = _apply_mix_and_rank(
        [_row_to_tuple(r, NOW, ["software engineer intern"]) for r in rows],
        limit=2,
        offset=0,
        explain=False,
        matched_keywords=True,
    )
    assert [h.title for h in hits] == [
        "Software Engineering Intern",
        "Director of Litigation",
    ]


def test_a_body_only_hit_is_demoted_not_deleted():
    order = rank_titles(["Director of Litigation"], ["software engineer intern"])
    assert order == ["Director of Litigation"]


def test_freshness_still_decides_between_two_title_hits():
    rows = [
        row(title="Software Engineering Intern", company="Old", age_days=60.0),
        row(title="Software Engineer Intern", company="New", age_days=0.0),
    ]
    hits = _apply_mix_and_rank(
        [_row_to_tuple(r, NOW, ["software engineer intern"]) for r in rows],
        limit=2,
        offset=0,
        explain=False,
        matched_keywords=True,
    )
    assert [h.company_name for h in hits] == ["New", "Old"]


def test_the_explain_field_reports_the_weight_it_actually_used():
    rows = [row(title="Director of Litigation")]
    hits = _apply_mix_and_rank(
        [_row_to_tuple(r, NOW, ["software engineer intern"]) for r in rows],
        limit=1,
        offset=0,
        explain=True,
        matched_keywords=True,
    )
    assert hits[0].explain is not None
    assert hits[0].explain.retrieve_score == BODY_ONLY_MATCH_WEIGHT


def test_the_weights_are_published_so_the_web_app_need_not_copy_them():
    constants = ranking_constants()
    assert constants["title_match_weight"] == TITLE_MATCH_WEIGHT
    assert constants["body_only_match_weight"] == BODY_ONLY_MATCH_WEIGHT


# ---------------------------------------------------------------------------
# POOL_COLUMNS
# ---------------------------------------------------------------------------


def test_the_pool_query_asks_for_every_column_the_row_reader_touches():
    """The pool stopped selecting `*` so it would stop shipping `jd_clean` for
    480 rows on every search. The risk that creates is silent: a column added to
    `_row_to_tuple` and forgotten here reads as None, and the search keeps
    working while every posting's date quietly becomes the crawl time.
    """
    source = inspect.getsource(_row_to_tuple)
    read = set(re.findall(r'row\.get\("([^"]+)"\)', source))
    read |= set(re.findall(r'row\["([^"]+)"\]', source))
    missing = read - set(POOL_COLUMNS)
    assert not missing, f"_row_to_tuple reads columns the pool does not select: {missing}"


def test_the_pool_query_does_not_ask_for_the_expensive_columns():
    # The whole point: these two are the page's cost, not the pool's.
    assert "jd_clean" not in POOL_COLUMNS
    assert "enrichment" not in POOL_COLUMNS


def test_a_row_carrying_only_the_pool_columns_still_ranks():
    lean = {k: v for k, v in row(title="Software Engineering Intern").items() if k in POOL_COLUMNS}
    hits = _apply_mix_and_rank(
        [_row_to_tuple(lean, NOW, ["software engineer intern"])],
        limit=1,
        offset=0,
        explain=False,
        matched_keywords=True,
    )
    assert hits[0].title == "Software Engineering Intern"
    assert hits[0].posted_at is not None
    assert hits[0].description_available is True
