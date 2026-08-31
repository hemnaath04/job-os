"""A title hit outranks a JD-body hit, and the pool query still avoids the body.

The bug these cover came from a live search for "software engineer intern": the
first page opened with a Platform Security Engineer, a Principal Enterprise
Technology Architect, a Localization Manager, an EA to the CRO and a Director of
Litigation. None of those is titled anything like the query. They matched
because the store at the time (Appwrite) had one fulltext index over a single
concatenated `search_text` field and answered it match-or-no-match, so a posting
that merely mentioned the internship programme in its body scored exactly as
highly as one titled for it: `retrieve_score` was a flat 1.0 for both.

Postgres does not need a Python re-scoring pass to tell those apart, so there is
not one any more. Two mechanisms do it, and this file pins both:

  * `title_keywords` is matched against `to_tsvector(title)` alone, so a
    body-only mention is not even a candidate.
  * `search_vector` is weighted `setweight(..., 'A')` on the title down to `'D'`
    on the body, and `ts_rank_cd` reads those weights, so within the rows that
    do match, a title hit scores above a body hit by construction.

Both need a real database to mean anything, which is why this file no longer
tests a Python helper.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from job_os.ingest.providers import RawPosting
from job_os.ingest.upsert import upsert_postings
from job_os.services.job_index import IndexQuery, search_index

pytestmark = pytest.mark.asyncio

NOW = datetime.now(UTC)

#: The body every "distractor" posting carries: it mentions each word of the
#: query, scattered, exactly as the real Director of Litigation posting did.
DISTRACTOR_BODY = (
    "You will partner with the software organisation, report to a senior "
    "engineer on matters of process, and help run our summer intern cohort."
)


@pytest.fixture
def source() -> str:
    return f"tw_{uuid.uuid4().hex[:12]}"


def posting(
    *,
    source: str,
    external_id: str,
    title: str,
    company: str,
    description: str = "Build and operate the service.",
    age_days: float = 1.0,
) -> RawPosting:
    return RawPosting(
        source=source,
        board_token="board",
        external_id=external_id,
        title=title,
        company_name=company,
        company_domain=f"{external_id}.test",
        source_url=f"https://example.test/{external_id}",
        jd_clean=description,
        location="San Francisco, CA",
        country_code="US",
        posted_at=NOW - timedelta(days=age_days),
        posted_at_basis="published",
    )


async def search(session: AsyncSession, source: str, **kwargs: object) -> list:
    query = IndexQuery(sources=[source], explain=True, **kwargs)  # type: ignore[arg-type]
    return (await search_index(session, query)).hits


async def test_the_qa_page_puts_the_internship_first(
    db_session: AsyncSession, source: str
) -> None:
    """The exact page that was wrong, asserted as the page it should be."""
    await upsert_postings(
        db_session,
        [
            posting(
                source=source,
                external_id="security",
                title="Platform Security Engineer",
                company="Glean",
                description=DISTRACTOR_BODY,
            ),
            posting(
                source=source,
                external_id="architect",
                title="Principal Enterprise Technology Architect",
                company="Bigco",
                description=DISTRACTOR_BODY,
            ),
            posting(
                source=source,
                external_id="litigation",
                title="Director of Litigation",
                company="Lawfirm",
                description=DISTRACTOR_BODY,
            ),
            posting(
                source=source,
                external_id="intern",
                title="Software Engineer Intern",
                company="Realco",
                description="Join the team for the summer.",
            ),
        ],
        seen_at=NOW,
    )

    hits = await search(db_session, source, query="software engineer intern")

    assert hits[0].source_id == "board:intern"


async def test_a_title_hit_scores_higher_than_a_body_only_hit(
    db_session: AsyncSession, source: str
) -> None:
    """The mechanism behind the ordering above, asserted on its own.

    Both rows match the same tsquery, so both are candidates. `ts_rank_cd`
    reads the tsvector's A-versus-D weighting and separates them, which is the
    graded relevance the Appwrite fulltext match could not express at all.
    """
    await upsert_postings(
        db_session,
        [
            posting(
                source=source,
                external_id="in-title",
                title="Software Engineer Intern",
                company="Realco",
                description="Join the team for the summer.",
            ),
            posting(
                source=source,
                external_id="in-body",
                title="Director of Litigation",
                company="Lawfirm",
                description=DISTRACTOR_BODY,
            ),
        ],
        seen_at=NOW,
    )

    hits = {hit.source_id: hit for hit in await search(db_session, source, query="software engineer intern")}

    in_title = hits["board:in-title"].explain
    in_body = hits["board:in-body"].explain
    assert in_title is not None and in_body is not None
    assert in_title.retrieve_score > in_body.retrieve_score


async def test_a_body_only_hit_is_demoted_not_dropped(
    db_session: AsyncSession, source: str
) -> None:
    """A free-text search is allowed to reach the body. It has to be, or a
    search for a technology named only in the requirements finds nothing.

    So the body-only posting still comes back; it just does not come first.
    `title_keywords` is the caller's way of asking for the stricter thing, and
    `test_title_keywords_does_not_match_the_jd_body` in
    `test_job_index_ranking.py` pins that.
    """
    await upsert_postings(
        db_session,
        [
            posting(
                source=source,
                external_id="litigation",
                title="Director of Litigation",
                company="Lawfirm",
                description=DISTRACTOR_BODY,
            )
        ],
        seen_at=NOW,
    )

    hits = await search(db_session, source, query="software engineer intern")

    assert [hit.source_id for hit in hits] == ["board:litigation"]


async def test_a_week_old_title_hit_beats_a_body_hit_posted_today(
    db_session: AsyncSession, source: str
) -> None:
    """Relevance and freshness multiply, so neither wins on its own.

    A body-only match from this morning must not outrank a real title match
    from last week, which is the ordering the whole weighting exists to fix.
    """
    await upsert_postings(
        db_session,
        [
            posting(
                source=source,
                external_id="fresh-body",
                title="Director of Litigation",
                company="Lawfirm",
                description=DISTRACTOR_BODY,
                age_days=0.0,
            ),
            posting(
                source=source,
                external_id="week-old-title",
                title="Software Engineer Intern",
                company="Realco",
                description="Join the team for the summer.",
                age_days=7.0,
            ),
        ],
        seen_at=NOW,
    )

    hits = await search(db_session, source, query="software engineer intern")

    assert [hit.source_id for hit in hits] == ["board:week-old-title", "board:fresh-body"]


async def test_freshness_still_decides_between_two_title_hits(
    db_session: AsyncSession, source: str
) -> None:
    """Two equally-titled postings are separated by date, not by luck."""
    await upsert_postings(
        db_session,
        [
            posting(
                source=source,
                external_id="old",
                title="Software Engineer Intern",
                company="Oldco",
                age_days=60.0,
            ),
            posting(
                source=source,
                external_id="new",
                title="Software Engineer Intern",
                company="Newco",
                age_days=0.0,
            ),
        ],
        seen_at=NOW,
    )

    hits = await search(db_session, source, query="software engineer intern")

    assert [hit.source_id for hit in hits] == ["board:new", "board:old"]


async def test_the_pool_query_does_not_carry_the_body(
    db_session: AsyncSession, source: str
) -> None:
    """The measurement this module's docstring rests on, kept enforceable.

    Selecting `jd_clean` for the whole candidate pool was 87.4ms against 8.5ms
    for the narrow columns, because the column is TOASTed and has to be fetched
    and decompressed per row. A future edit that added it to the pool `select`
    would be silent, so the source is checked directly: the body may only be
    named inside the second, page-sized query.
    """
    import inspect

    from job_os.services import job_index

    pool = inspect.getsource(job_index.search_index)
    assert "JobPosting.jd_clean" not in pool, (
        "the candidate pool must not select jd_clean; _fetch_page is where the "
        "body is allowed to be read, and only for the page"
    )
    assert "JobPosting.jd_clean" in inspect.getsource(job_index._fetch_page)
