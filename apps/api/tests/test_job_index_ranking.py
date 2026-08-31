"""Ranking order on the indexed read path, against a real Postgres.

`rank = retrieve_score * freshness_weight * mix_weight`, and the multiplication is
the whole point. With an additive score a perfect title match from eight months
ago outranks a good match from this morning, which is the wrong answer for a job
search because the old one is probably filled. Multiplying means a stale posting
has to be substantially more relevant to win, not marginally.

Every query here is scoped with `sources=[...]` to a namespace unique per test.
The index is a shared table by design, so a ranking assertion that did not scope
would be answered partly by whatever else had been crawled.

Back on a real database after two weeks against an in-memory Appwrite fake.
Nothing here is a preference: half of what this file asserts is behaviour only
Postgres has. `ts_rank_cd` over a weighted tsvector is what makes a stale exact
match score higher than a fresh weaker one (the fake could only answer
match/no-match, so that test had to assert equality instead of a difference);
`to_tsvector` on the title alone is what keeps `title_keywords` out of the JD
body; `ILIKE` is what makes `location` a substring filter. Every test runs
inside a transaction that is rolled back.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from job_os.ingest.providers import RawPosting
from job_os.ingest.upsert import upsert_postings
from job_os.services.job_index import (
    COMPANY_DIVERSITY_DECAY,
    MIN_FRESHNESS_WEIGHT,
    IndexQuery,
    search_index,
)

pytestmark = pytest.mark.asyncio

NOW = datetime.now(UTC)


@pytest.fixture
def source() -> str:
    return f"rank_{uuid.uuid4().hex[:12]}"


def posting(
    *,
    source: str,
    external_id: str,
    title: str,
    company: str = "Acme",
    domain: str | None = "acme.test",
    description: str = "Build and operate the service.",
    age_days: float = 0.0,
    basis: str = "published",
    location: str | None = "San Francisco, CA",
    remote: bool = False,
) -> RawPosting:
    return RawPosting(
        source=source,
        board_token="board",
        external_id=external_id,
        title=title,
        company_name=company,
        company_domain=domain,
        source_url=f"https://example.test/{external_id}",
        jd_clean=description,
        location=location,
        country_code="US",
        remote=remote,
        posted_at=NOW - timedelta(days=age_days),
        posted_at_basis=basis,
    )


async def write(session: AsyncSession, *postings: RawPosting) -> None:
    await upsert_postings(session, list(postings), seen_at=NOW)


async def search(session: AsyncSession, source: str, **kwargs: object) -> list:
    query = IndexQuery(sources=[source], explain=True, **kwargs)  # type: ignore[arg-type]
    return (await search_index(session, query)).hits


# ---------------------------------------------------------------------------
# the formula
# ---------------------------------------------------------------------------


async def test_rank_is_the_product_of_its_three_components(
    db_session: AsyncSession, source: str
) -> None:
    """The EXPLAIN field has to reconcile, or it is decoration rather than debug."""
    await write(db_session, posting(source=source, external_id="1", title="Data Engineer"))

    hits = await search(db_session, source, title_keywords=["data engineer"])

    assert len(hits) == 1
    explain = hits[0].explain
    assert explain is not None
    expected = explain.retrieve_score * explain.freshness_weight * explain.mix_weight
    assert explain.rank == pytest.approx(expected)
    assert hits[0].rank == pytest.approx(expected)


async def test_explain_is_absent_unless_asked_for(
    db_session: AsyncSession, source: str
) -> None:
    await write(db_session, posting(source=source, external_id="1", title="Data Engineer"))

    result = await search_index(db_session, IndexQuery(sources=[source], explain=False))

    assert result.hits[0].explain is None


async def test_browsing_with_no_keywords_scores_purely_on_freshness_and_mix(
    db_session: AsyncSession, source: str
) -> None:
    """`retrieve_score` is 1.0 when nothing was searched for, so it drops out."""
    await write(db_session, posting(source=source, external_id="1", title="Anything"))

    hits = await search(db_session, source)

    explain = hits[0].explain
    assert explain is not None
    assert explain.retrieve_score == 1.0
    assert explain.matched_keywords is False


# ---------------------------------------------------------------------------
# freshness
# ---------------------------------------------------------------------------


async def test_fresh_beats_stale_at_equal_relevance(
    db_session: AsyncSession, source: str
) -> None:
    await write(
        db_session,
        posting(source=source, external_id="old", title="Platform Engineer",
                company="Old Co", domain="old.test", age_days=120),
        posting(source=source, external_id="new", title="Platform Engineer",
                company="New Co", domain="new.test", age_days=0),
    )

    hits = await search(db_session, source, title_keywords=["platform engineer"])

    assert [hit.source_id for hit in hits] == ["board:new", "board:old"]


async def test_freshness_decays_by_half_every_fortnight(
    db_session: AsyncSession, source: str
) -> None:
    await write(
        db_session,
        posting(source=source, external_id="fresh", title="Engineer",
                company="A", domain="a.test", age_days=0),
        posting(source=source, external_id="fortnight", title="Engineer",
                company="B", domain="b.test", age_days=14),
    )

    hits = {hit.source_id: hit.explain for hit in await search(db_session, source)}

    fresh = hits["board:fresh"]
    fortnight = hits["board:fortnight"]
    assert fresh is not None and fortnight is not None
    assert fortnight.freshness_weight == pytest.approx(fresh.freshness_weight / 2, rel=0.02)


async def test_an_ancient_posting_is_demoted_not_deleted(
    db_session: AsyncSession, source: str
) -> None:
    """Deciding a posting is gone is the crawl's job via `active`, not the ranker's.

    A five-year-old posting that a board still lists is still a real job, so the
    weight floors rather than reaching zero.
    """
    await write(
        db_session,
        posting(source=source, external_id="ancient", title="Engineer", age_days=1825),
    )

    hits = await search(db_session, source)

    assert len(hits) == 1
    explain = hits[0].explain
    assert explain is not None
    assert explain.freshness_weight == pytest.approx(MIN_FRESHNESS_WEIGHT)


async def test_a_stale_exact_match_loses_to_a_fresh_weaker_match(
    db_session: AsyncSession, source: str
) -> None:
    """The multiplicative property, stated as the outcome it exists to produce.

    The stale row is the better keyword match. It still loses, because freshness
    multiplies rather than adding a bounded bonus.
    """
    await write(
        db_session,
        posting(
            source=source,
            external_id="stale-exact",
            title="Machine Learning Engineer",
            company="Stale Co",
            domain="stale.test",
            description="Machine learning engineer working on machine learning systems.",
            age_days=240,
        ),
        posting(
            source=source,
            external_id="fresh-weaker",
            title="Machine Learning Engineer, Platform",
            company="Fresh Co",
            domain="fresh.test",
            description="Platform work supporting a variety of teams.",
            age_days=0,
        ),
    )

    hits = await search(db_session, source, title_keywords=["machine learning engineer"])

    stale = next(h for h in hits if h.source_id == "board:stale-exact")
    fresh = next(h for h in hits if h.source_id == "board:fresh-weaker")
    assert stale.explain is not None and fresh.explain is not None
    # The premise: the stale row really is the stronger textual match.
    assert stale.explain.retrieve_score > fresh.explain.retrieve_score
    # The conclusion: it loses anyway.
    assert hits[0].source_id == "board:fresh-weaker"


# ---------------------------------------------------------------------------
# company diversity
# ---------------------------------------------------------------------------


async def test_one_company_cannot_own_the_whole_page(
    db_session: AsyncSession, source: str
) -> None:
    """The nth posting from one employer is progressively discounted.

    Without this a company mid-hiring-spree fills the first page and the search
    stops being a search.
    """
    await write(
        db_session,
        *[
            posting(
                source=source,
                external_id=f"spree-{i}",
                title="Backend Engineer",
                company="Spree Corp",
                domain="spree.test",
            )
            for i in range(5)
        ],
        posting(
            source=source,
            external_id="other",
            title="Backend Engineer",
            company="Other Co",
            domain="other.test",
            age_days=1,
        ),
    )

    hits = await search(db_session, source, title_keywords=["backend engineer"])

    # The other company is slightly staler, so on freshness alone it would sit
    # last. Diversity lifts it above the spree's later postings.
    positions = [hit.company_name for hit in hits]
    assert positions.index("Other Co") < len(positions) - 1


async def test_a_companys_first_posting_is_never_penalized(
    db_session: AsyncSession, source: str
) -> None:
    await write(
        db_session,
        posting(source=source, external_id="1", title="Engineer",
                company="Solo", domain="solo.test"),
    )

    hits = await search(db_session, source)

    explain = hits[0].explain
    assert explain is not None
    assert explain.company_rank == 0
    assert explain.mix_weight == 1.0


async def test_the_diversity_discount_compounds_per_position(
    db_session: AsyncSession, source: str
) -> None:
    await write(
        db_session,
        *[
            posting(source=source, external_id=f"n-{i}", title="Engineer",
                    company="Many", domain="many.test")
            for i in range(3)
        ],
    )

    hits = await search(db_session, source)
    weights = [h.explain.mix_weight for h in hits if h.explain is not None]

    assert weights[0] == pytest.approx(1.0)
    assert weights[1] == pytest.approx(COMPANY_DIVERSITY_DECAY)
    assert weights[2] == pytest.approx(COMPANY_DIVERSITY_DECAY**2)


# ---------------------------------------------------------------------------
# honest freshness on the way out
# ---------------------------------------------------------------------------


async def test_both_first_seen_and_last_seen_are_exposed(
    db_session: AsyncSession, source: str
) -> None:
    """The differentiator. "First seen 3 weeks ago, still listed 1 hour ago" needs
    both, and a UI given only one of them cannot say it."""
    first_crawl = NOW - timedelta(days=21)
    await upsert_postings(
        db_session,
        [posting(source=source, external_id="1", title="Engineer")],
        seen_at=first_crawl,
    )
    await upsert_postings(
        db_session,
        [posting(source=source, external_id="1", title="Engineer")],
        seen_at=NOW,
    )

    hits = await search(db_session, source)

    hit = hits[0]
    assert hit.first_seen_at == first_crawl
    assert hit.last_seen_at == NOW
    assert hit.first_seen_at < hit.last_seen_at


async def test_a_crawled_date_is_reported_as_estimated(
    db_session: AsyncSession, source: str
) -> None:
    """`updated` and `first_crawl` are upper bounds, not posting dates, and the
    read path has to say so rather than presenting them as employer-stated."""
    await write(
        db_session,
        posting(source=source, external_id="real", title="Engineer", basis="published"),
        posting(source=source, external_id="guess", title="Engineer", basis="updated"),
        posting(source=source, external_id="none", title="Engineer", basis="first_crawl"),
    )

    hits = {hit.source_id: hit for hit in await search(db_session, source)}

    assert hits["board:real"].posted_at_estimated is False
    assert hits["board:guess"].posted_at_estimated is True
    assert hits["board:none"].posted_at_estimated is True
    assert hits["board:guess"].posted_at_basis == "updated"


async def test_posted_within_days_excludes_estimated_dates(
    db_session: AsyncSession, source: str
) -> None:
    """Stricter than max_age_days on purpose: this filter promises a real date."""
    await write(
        db_session,
        posting(source=source, external_id="real", title="Engineer",
                basis="published", age_days=2),
        posting(source=source, external_id="guess", title="Engineer",
                basis="updated", age_days=2),
    )

    hits = await search(db_session, source, posted_within_days=7)

    assert [hit.source_id for hit in hits] == ["board:real"]


async def test_max_age_days_judges_a_dateless_posting_by_first_sight(
    db_session: AsyncSession, source: str
) -> None:
    """A board that gave no date must not mean "keep forever"."""
    stale = RawPosting(
        source=source,
        board_token="board",
        external_id="dateless",
        title="Engineer",
        company_name="Acme",
        source_url="https://example.test/dateless",
        jd_clean="text",
        posted_at=None,
        posted_at_basis="first_crawl",
    )
    await upsert_postings(db_session, [stale], seen_at=NOW - timedelta(days=90))

    within = await search(db_session, source, max_age_days=30)
    without = await search(db_session, source)

    assert within == []
    assert len(without) == 1


# ---------------------------------------------------------------------------
# title_keywords is title-only; free-text `query` is not
# ---------------------------------------------------------------------------


async def test_title_keywords_does_not_match_the_jd_body(
    db_session: AsyncSession, source: str
) -> None:
    """A title search is a title search, not "these words somewhere in the JD".

    `search_vector` also carries company_name, location and the JD body, so a
    title_keywords query that matched against the whole vector (as opposed to
    the title alone) would surface postings like this: none of "ai", "engineer"
    or "intern" appear anywhere near each other in the title, only scattered
    through the body text.
    """
    await write(
        db_session,
        posting(
            source=source,
            external_id="unrelated-title",
            title="Head of IT",
            description=(
                "We are hiring an AI engineer to mentor our summer intern "
                "cohort and modernize internal tooling."
            ),
        ),
    )

    hits = await search(db_session, source, title_keywords=["ai engineer intern"])

    assert hits == []


async def test_free_text_query_still_matches_the_jd_body(
    db_session: AsyncSession, source: str
) -> None:
    """The other half of the same fix: `query` (technology_slugs, folded in by
    the caller) is meant to search the whole posting, unlike title_keywords."""
    await write(
        db_session,
        posting(
            source=source,
            external_id="body-match",
            title="Backend Engineer",
            description="We build everything in Rust and deploy on bare metal.",
        ),
    )

    hits = await search(db_session, source, query="rust")

    assert [hit.source_id for hit in hits] == ["board:body-match"]


async def test_title_keywords_and_free_text_are_alternatives_not_a_conjunction(
    db_session: AsyncSession, source: str
) -> None:
    """Consistent with the rest of this module's "search wider" stance: a posting
    can qualify on a title hit alone, a body hit alone, or both."""
    await write(
        db_session,
        posting(source=source, external_id="title-only", title="AI Engineer Intern",
                 description="Nothing about Rust here."),
        posting(source=source, external_id="body-only", title="Backend Engineer",
                 description="We build everything in Rust."),
    )

    hits = await search(
        db_session, source, title_keywords=["ai engineer intern"], query="rust"
    )

    assert {hit.source_id for hit in hits} == {"board:title-only", "board:body-only"}


# ---------------------------------------------------------------------------
# what the default query does and does not show
# ---------------------------------------------------------------------------


async def test_inactive_postings_are_hidden_by_default_and_shown_on_request(
    db_session: AsyncSession, source: str
) -> None:
    """A closure is a fact worth being able to show, not a row that vanishes."""
    from job_os.db.models.ingest import CrawlRun
    from job_os.ingest.upsert import deactivate_missing

    run = CrawlRun(status="running", providers=["test"])
    db_session.add(run)
    await db_session.flush()

    await write(db_session, posting(source=source, external_id="closed", title="Engineer"))
    await deactivate_missing(db_session, source=source, board_token="board", run_id=run.id)

    default = await search(db_session, source)
    including = await search(db_session, source, include_inactive=True)

    assert default == []
    assert len(including) == 1
    assert including[0].active is False


async def test_duplicates_are_hidden_by_default(
    db_session: AsyncSession, source: str
) -> None:
    from sqlalchemy import select

    from job_os.db.models.job_posting import JobPosting
    from job_os.ingest.upsert import mark_duplicates

    await write(
        db_session,
        posting(source=source, external_id="canonical", title="Engineer"),
        posting(source=source, external_id="dupe", title="Engineer",
                location="New York, NY"),
    )
    rows = (
        await db_session.execute(select(JobPosting).where(JobPosting.source == source))
    ).scalars()
    by_external = {row.external_id: row.id for row in rows}
    await mark_duplicates(
        db_session, [(by_external["dupe"], by_external["canonical"], "exact_key", None)]
    )

    default = await search(db_session, source)
    including = await search(db_session, source, include_duplicates=True)

    assert [hit.source_id for hit in default] == ["board:canonical"]
    assert len(including) == 2


async def test_unhydrated_rows_are_not_presented_as_having_a_description(
    db_session: AsyncSession, source: str
) -> None:
    """A SmartRecruiters listing body is provider metadata, not a JD."""
    listing = RawPosting(
        source=source,
        board_token="board",
        external_id="thin",
        title="Engineer",
        company_name="Acme",
        source_url="https://example.test/thin",
        jd_clean="Engineer\nAcme\nBoston",
        jd_hydrated=False,
        posted_at=NOW,
        posted_at_basis="published",
    )
    await upsert_postings(db_session, [listing], seen_at=NOW)

    hits = await search(db_session, source)
    filtered = await search(db_session, source, require_description=True)

    assert hits[0].snippet, "the snippet still carries whatever text there was"
    assert hits[0].description_available is False
    assert filtered == []


async def test_a_hydrated_row_with_an_empty_body_is_also_honest(
    db_session: AsyncSession, source: str
) -> None:
    """The other direction of the same flag, so the fix is a conjunction rather
    than a swap: `jd_hydrated` true but nothing actually there is still no
    description to show."""
    empty = RawPosting(
        source=source,
        board_token="board",
        external_id="empty",
        title="Engineer",
        company_name="Acme",
        source_url="https://example.test/empty",
        jd_clean="",
        jd_hydrated=True,
        posted_at=NOW,
        posted_at_basis="published",
    )
    await upsert_postings(db_session, [empty], seen_at=NOW)

    hits = await search(db_session, source)

    assert hits[0].description_available is False


# ---------------------------------------------------------------------------
# what a multi-word search means
# ---------------------------------------------------------------------------


async def test_a_multi_word_query_requires_every_word_not_any(
    db_session: AsyncSession, source: str
) -> None:
    """The live bug that broke a real search, restated for this store.

    Under Appwrite, an unquoted multi-word search ran MariaDB's fulltext in
    natural-language mode: satisfied by ANY of the words appearing anywhere in
    the concatenated body. A search for "software engineer intern" matched most
    of the table and opened with an Account Executive posting whose JD happened
    to say "software". `_tsquery` joins a phrase's words with `&`, so every one
    of them has to be present -- which is stricter than the quoting fix that
    was applied there, and is what this path always did.
    """
    await write(
        db_session,
        posting(
            source=source,
            external_id="one-word-only",
            title="Account Executive",
            company="Sales Co",
            domain="sales.test",
            description="You will sell our software to enterprise buyers.",
        ),
        posting(
            source=source,
            external_id="all-three",
            title="Growth Lead",
            company="Other Co",
            domain="other.test",
            description="We hire a software engineer intern every summer.",
        ),
    )

    hits = await search(db_session, source, query="software engineer intern")

    assert [hit.source_id for hit in hits] == ["board:all-three"]


async def test_location_and_company_narrow_rather_than_define_the_match(
    db_session: AsyncSession, source: str
) -> None:
    """`location` and `company` are ANDed filters, not more alternatives.

    Folding them into the same OR group as `title_keywords`/`query` would widen
    a search instead of narrowing it: a posting in Boston would match a search
    for engineers in Boston without being an engineering job. They are also
    substring matches (`ILIKE`), which is why "Boston" finds "Boston, MA".
    """
    await write(
        db_session,
        posting(
            source=source,
            external_id="right-role-right-city",
            title="Backend Engineer",
            company="Acme",
            domain="acme.test",
            location="Boston, MA",
        ),
        posting(
            source=source,
            external_id="right-role-wrong-city",
            title="Backend Engineer",
            company="Acme",
            domain="acme.test",
            location="Austin, TX",
        ),
        posting(
            source=source,
            external_id="wrong-role-right-city",
            title="Staff Accountant",
            company="Acme",
            domain="acme.test",
            location="Boston, MA",
        ),
    )

    hits = await search(
        db_session, source, title_keywords=["backend engineer"], location="Boston"
    )

    assert [hit.source_id for hit in hits] == ["board:right-role-right-city"]


# ---------------------------------------------------------------------------
# what reaches the full-text index, and what must not be shortened with it
# ---------------------------------------------------------------------------


async def test_only_the_first_600_characters_of_a_body_are_searchable(
    db_session: AsyncSession, source: str
) -> None:
    """The storage change, asserted as the behaviour it actually buys and costs.

    `FTS_DESCRIPTION_CHARS` is the slice of `jd_clean` that reaches
    `search_vector`. Rebuilding that column at both lengths over the same 2,550
    real crawled postings measured 22,780 bytes a row at 8,000 against 14,800
    at 600. The cost is exactly this: a word that first appears deep in a body
    is no longer a way to FIND the posting.
    """
    from job_os.db.models.job_posting import FTS_DESCRIPTION_CHARS

    assert FTS_DESCRIPTION_CHARS == 600
    body = "Filler sentence about the team and the work. " * 40
    assert len(body) > FTS_DESCRIPTION_CHARS
    await write(
        db_session,
        posting(
            source=source,
            external_id="deep",
            title="Engineer",
            description=body + " We work primarily in Erlang.",
        ),
    )

    early = await search(db_session, source, query="filler")
    deep = await search(db_session, source, query="erlang")

    assert [hit.source_id for hit in early] == ["board:deep"]
    assert deep == [], "past the slice, a word cannot be searched for"


async def test_the_whole_body_is_still_stored_even_though_it_is_not_all_indexed(
    db_session: AsyncSession, source: str
) -> None:
    """The half of that trade which must NOT follow the other half.

    `jd_clean` is the input to `job_enrich.enrich_job`, which produces the
    document the fit score reads. Truncating the stored body to match the
    indexed slice would degrade every future fit score silently, while every
    already-enriched row went on looking fine. So this asserts the body is
    whole, right next to the test that asserts the index is not.
    """
    from sqlalchemy import select

    from job_os.db.models.job_posting import JobPosting

    body = "Filler sentence about the team and the work. " * 40
    tail = " We work primarily in Erlang."
    await write(
        db_session,
        posting(source=source, external_id="deep", title="Engineer", description=body + tail),
    )

    stored = await db_session.scalar(
        select(JobPosting.jd_clean).where(JobPosting.source == source)
    )

    assert stored is not None
    assert stored.endswith(tail)
    assert len(stored) == len(body + tail)


async def test_results_are_ordered_by_descending_rank(
    db_session: AsyncSession, source: str
) -> None:
    """The contract the UI relies on, asserted directly rather than inferred."""
    await write(
        db_session,
        *[
            posting(
                source=source,
                external_id=f"p-{i}",
                title="Engineer",
                company=f"Co {i}",
                domain=f"co{i}.test",
                age_days=i * 5,
            )
            for i in range(6)
        ],
    )

    hits = await search(db_session, source)
    ranks = [hit.rank for hit in hits]

    assert ranks == sorted(ranks, reverse=True)
