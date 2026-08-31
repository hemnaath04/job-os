"""Ranking order on the indexed read path, against an in-memory Appwrite.

`rank = retrieve_score * freshness_weight * mix_weight`, and the multiplication is
the whole point. With an additive score a perfect title match from eight months
ago outranks a good match from this morning, which is the wrong answer for a job
search because the old one is probably filled. Multiplying means a stale posting
has to be substantially more relevant to win, not marginally.

Every query here is scoped with `sources=[...]` to a namespace unique per test.
That mattered when this file ran against the shared production table and left its
`rank_...` rows behind in it -- 123 of them were still there when this was
written. It is kept because the scoping is also what makes each test's assertion
about its own rows and nothing else, which is worth having whatever the store is.

This file used to be marked `requires_appwrite_key`, so it skipped in CI and hit
production locally. It now runs against `fake_appwrite` (see
`tests/_fake_appwrite.py`): no credentials, no network, no Postgres either --
`search_index` and `upsert_postings` both open with `del session`.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from _fake_appwrite import FakeAppwriteTables
from job_os.ingest.providers import RawPosting
from job_os.ingest.upsert import upsert_postings
from job_os.services.job_index import (
    COMPANY_DIVERSITY_DECAY,
    MIN_FRESHNESS_WEIGHT,
    IndexQuery,
    search_index,
)

pytestmark = pytest.mark.asyncio

#: See `NO_SESSION` in test_ingest_upsert.py: the session parameter survives on
#: these functions only so their callers did not need a signature change.
NO_SESSION: Any = None

#: Millisecond precision on purpose. Appwrite's datetime columns carry three
#: decimal places, so a `datetime.now(UTC)` with microseconds does not survive a
#: round trip and `test_both_first_seen_and_last_seen_are_exposed` would be
#: asserting on precision the column never promised. Truncating here keeps that
#: test an exact-equality test rather than a tolerance one.
NOW = datetime.now(UTC).replace(microsecond=datetime.now(UTC).microsecond // 1000 * 1000)


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


async def write(*postings: RawPosting) -> None:
    await upsert_postings(NO_SESSION, list(postings), seen_at=NOW)


async def search(source: str, **kwargs: object) -> list:
    query = IndexQuery(sources=[source], explain=True, **kwargs)  # type: ignore[arg-type]
    return (await search_index(NO_SESSION, query)).hits


# ---------------------------------------------------------------------------
# the formula
# ---------------------------------------------------------------------------


async def test_rank_is_the_product_of_its_three_components(
    fake_appwrite: FakeAppwriteTables, source: str
) -> None:
    """The EXPLAIN field has to reconcile, or it is decoration rather than debug."""
    await write(posting(source=source, external_id="1", title="Data Engineer"))

    hits = await search(source, title_keywords=["data engineer"])

    assert len(hits) == 1
    explain = hits[0].explain
    assert explain is not None
    expected = explain.retrieve_score * explain.freshness_weight * explain.mix_weight
    assert explain.rank == pytest.approx(expected)
    assert hits[0].rank == pytest.approx(expected)


async def test_explain_is_absent_unless_asked_for(
    fake_appwrite: FakeAppwriteTables, source: str
) -> None:
    await write(posting(source=source, external_id="1", title="Data Engineer"))

    result = await search_index(NO_SESSION, IndexQuery(sources=[source], explain=False))

    assert result.hits[0].explain is None


async def test_browsing_with_no_keywords_scores_purely_on_freshness_and_mix(
    fake_appwrite: FakeAppwriteTables, source: str
) -> None:
    """`retrieve_score` is 1.0 when nothing was searched for, so it drops out."""
    await write(posting(source=source, external_id="1", title="Anything"))

    hits = await search(source)

    explain = hits[0].explain
    assert explain is not None
    assert explain.retrieve_score == 1.0
    assert explain.matched_keywords is False


# ---------------------------------------------------------------------------
# freshness
# ---------------------------------------------------------------------------


async def test_fresh_beats_stale_at_equal_relevance(
    fake_appwrite: FakeAppwriteTables, source: str
) -> None:
    await write(
        posting(source=source, external_id="old", title="Platform Engineer",
                company="Old Co", domain="old.test", age_days=120),
        posting(source=source, external_id="new", title="Platform Engineer",
                company="New Co", domain="new.test", age_days=0),
    )

    hits = await search(source, title_keywords=["platform engineer"])

    assert [hit.source_id for hit in hits] == ["board:new", "board:old"]


async def test_freshness_decays_by_half_every_fortnight(
    fake_appwrite: FakeAppwriteTables, source: str
) -> None:
    await write(
        posting(source=source, external_id="fresh", title="Engineer",
                company="A", domain="a.test", age_days=0),
        posting(source=source, external_id="fortnight", title="Engineer",
                company="B", domain="b.test", age_days=14),
    )

    hits = {hit.source_id: hit.explain for hit in await search(source)}

    fresh = hits["board:fresh"]
    fortnight = hits["board:fortnight"]
    assert fresh is not None and fortnight is not None
    assert fortnight.freshness_weight == pytest.approx(fresh.freshness_weight / 2, rel=0.02)


async def test_an_ancient_posting_is_demoted_not_deleted(
    fake_appwrite: FakeAppwriteTables, source: str
) -> None:
    """Deciding a posting is gone is the crawl's job via `active`, not the ranker's.

    A five-year-old posting that a board still lists is still a real job, so the
    weight floors rather than reaching zero.
    """
    await write(posting(source=source, external_id="ancient", title="Engineer", age_days=1825))

    hits = await search(source)

    assert len(hits) == 1
    explain = hits[0].explain
    assert explain is not None
    assert explain.freshness_weight == pytest.approx(MIN_FRESHNESS_WEIGHT)


async def test_a_stale_exact_match_loses_to_a_fresh_weaker_match(
    fake_appwrite: FakeAppwriteTables, source: str
) -> None:
    """The multiplicative property, stated as the outcome it exists to produce.

    The stale row is the better textual match to a reader: it repeats the whole
    phrase in its body, where the fresh row's body is about something else. It
    loses anyway, because freshness multiplies rather than adding a bounded bonus.

    The premise assertion below used to read `stale.retrieve_score >
    fresh.retrieve_score`, and that stopped being true when the index moved off
    Postgres -- not silently, and not as a bug. `ts_rank_cd` over a weighted
    tsvector gave a graded score, so "repeats the phrase in the body" was
    visible to the ranker. Appwrite's fulltext match is pass/fail, and
    `retrieve_score` now carries only `_title_weight`'s one distinction: did the
    searched words land in the TITLE, or only in the body. Both of these titles
    contain the phrase, so both score TITLE_MATCH_WEIGHT and retrieval has
    nothing left to say between them. `search_index`'s own docstring lists that
    loss as an accepted cost of the migration.

    So the premise is asserted as what it now is -- equal, not merely
    not-greater -- which pins the fact that relevance is carrying none of this
    result. Freshness decides it alone, and the conclusion is unchanged.
    """
    await write(
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

    hits = await search(source, title_keywords=["machine learning engineer"])

    stale = next(h for h in hits if h.source_id == "board:stale-exact")
    fresh = next(h for h in hits if h.source_id == "board:fresh-weaker")
    assert stale.explain is not None and fresh.explain is not None
    # The premise: retrieval cannot separate them, so it is not what decides this.
    assert stale.explain.retrieve_score == fresh.explain.retrieve_score
    assert stale.explain.freshness_weight < fresh.explain.freshness_weight
    # The conclusion: the stale one loses anyway.
    assert hits[0].source_id == "board:fresh-weaker"


# ---------------------------------------------------------------------------
# company diversity
# ---------------------------------------------------------------------------


async def test_one_company_cannot_own_the_whole_page(
    fake_appwrite: FakeAppwriteTables, source: str
) -> None:
    """The nth posting from one employer is progressively discounted.

    Without this a company mid-hiring-spree fills the first page and the search
    stops being a search.
    """
    await write(
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

    hits = await search(source, title_keywords=["backend engineer"])

    # The other company is slightly staler, so on freshness alone it would sit
    # last. Diversity lifts it above the spree's later postings.
    positions = [hit.company_name for hit in hits]
    assert positions.index("Other Co") < len(positions) - 1


async def test_a_companys_first_posting_is_never_penalized(
    fake_appwrite: FakeAppwriteTables, source: str
) -> None:
    await write(
        posting(source=source, external_id="1", title="Engineer",
                company="Solo", domain="solo.test"),
    )

    hits = await search(source)

    explain = hits[0].explain
    assert explain is not None
    assert explain.company_rank == 0
    assert explain.mix_weight == 1.0


async def test_the_diversity_discount_compounds_per_position(
    fake_appwrite: FakeAppwriteTables, source: str
) -> None:
    await write(
        *[
            posting(source=source, external_id=f"n-{i}", title="Engineer",
                    company="Many", domain="many.test")
            for i in range(3)
        ],
    )

    hits = await search(source)
    weights = [h.explain.mix_weight for h in hits if h.explain is not None]

    assert weights[0] == pytest.approx(1.0)
    assert weights[1] == pytest.approx(COMPANY_DIVERSITY_DECAY)
    assert weights[2] == pytest.approx(COMPANY_DIVERSITY_DECAY**2)


# ---------------------------------------------------------------------------
# honest freshness on the way out
# ---------------------------------------------------------------------------


async def test_both_first_seen_and_last_seen_are_exposed(
    fake_appwrite: FakeAppwriteTables, source: str
) -> None:
    """The differentiator. "First seen 3 weeks ago, still listed 1 hour ago" needs
    both, and a UI given only one of them cannot say it."""
    first_crawl = NOW - timedelta(days=21)
    await upsert_postings(
        NO_SESSION,
        [posting(source=source, external_id="1", title="Engineer")],
        seen_at=first_crawl,
    )
    await upsert_postings(
        NO_SESSION,
        [posting(source=source, external_id="1", title="Engineer")],
        seen_at=NOW,
    )

    hits = await search(source)

    hit = hits[0]
    assert hit.first_seen_at == first_crawl
    assert hit.last_seen_at == NOW
    assert hit.first_seen_at < hit.last_seen_at


async def test_a_crawled_date_is_reported_as_estimated(
    fake_appwrite: FakeAppwriteTables, source: str
) -> None:
    """`updated` and `first_crawl` are upper bounds, not posting dates, and the
    read path has to say so rather than presenting them as employer-stated."""
    await write(
        posting(source=source, external_id="real", title="Engineer", basis="published"),
        posting(source=source, external_id="guess", title="Engineer", basis="updated"),
        posting(source=source, external_id="none", title="Engineer", basis="first_crawl"),
    )

    hits = {hit.source_id: hit for hit in await search(source)}

    assert hits["board:real"].posted_at_estimated is False
    assert hits["board:guess"].posted_at_estimated is True
    assert hits["board:none"].posted_at_estimated is True
    assert hits["board:guess"].posted_at_basis == "updated"


async def test_posted_within_days_excludes_estimated_dates(
    fake_appwrite: FakeAppwriteTables, source: str
) -> None:
    """Stricter than max_age_days on purpose: this filter promises a real date."""
    await write(
        posting(source=source, external_id="real", title="Engineer",
                basis="published", age_days=2),
        posting(source=source, external_id="guess", title="Engineer",
                basis="updated", age_days=2),
    )

    hits = await search(source, posted_within_days=7)

    assert [hit.source_id for hit in hits] == ["board:real"]


async def test_max_age_days_judges_a_dateless_posting_by_first_sight(
    fake_appwrite: FakeAppwriteTables, source: str
) -> None:
    """A board that gave no date must not mean "keep forever".

    On Postgres this filtered `COALESCE(posted_at, first_seen_at)`. Appwrite
    cannot express that COALESCE, so `max_age_days` filters `last_seen_at`
    instead (see `search_index`'s docstring). For a posting seen exactly once
    those are the same instant, which is what this fixture arranges, so the
    assertion still means what its name says.
    """
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
    await upsert_postings(NO_SESSION, [stale], seen_at=NOW - timedelta(days=90))

    within = await search(source, max_age_days=30)
    without = await search(source)

    assert within == []
    assert len(without) == 1


# ---------------------------------------------------------------------------
# title_keywords is title-only; free-text `query` is not
# ---------------------------------------------------------------------------


async def test_title_keywords_does_not_match_the_jd_body(
    fake_appwrite: FakeAppwriteTables, source: str
) -> None:
    """A title search is a title search, not "these words somewhere in the JD".

    `search_text` also carries company_name, location and the JD body, so a
    title_keywords query that matched against the whole vector (as opposed to
    the title alone) would surface postings like this: none of "ai", "engineer"
    or "intern" appear anywhere near each other in the title, only scattered
    through the body text.

    Since the move to Appwrite the mechanism is `_quote_phrase` rather than a
    separate title-only tsvector: the phrase has to appear intact, so the words
    scattered through this body do not match it. The promise the test makes is
    the same one; what enforces it is not.
    """
    await write(
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

    hits = await search(source, title_keywords=["ai engineer intern"])

    assert hits == []


async def test_free_text_query_still_matches_the_jd_body(
    fake_appwrite: FakeAppwriteTables, source: str
) -> None:
    """The other half of the same fix: `query` (technology_slugs, folded in by
    the caller) is meant to search the whole posting, unlike title_keywords."""
    await write(
        posting(
            source=source,
            external_id="body-match",
            title="Backend Engineer",
            description="We build everything in Rust and deploy on bare metal.",
        ),
    )

    hits = await search(source, query="rust")

    assert [hit.source_id for hit in hits] == ["board:body-match"]


async def test_title_keywords_and_free_text_are_alternatives_not_a_conjunction(
    fake_appwrite: FakeAppwriteTables, source: str
) -> None:
    """Consistent with the rest of this module's "search wider" stance: a posting
    can qualify on a title hit alone, a body hit alone, or both."""
    await write(
        posting(source=source, external_id="title-only", title="AI Engineer Intern",
                description="Nothing here about the language we build in."),
        posting(source=source, external_id="body-only", title="Backend Engineer",
                description="We build everything in Rust."),
    )

    hits = await search(source, title_keywords=["ai engineer intern"], query="rust")

    assert {hit.source_id for hit in hits} == {"board:title-only", "board:body-only"}


# ---------------------------------------------------------------------------
# what the default query does and does not show
# ---------------------------------------------------------------------------


async def test_inactive_postings_are_hidden_by_default_and_shown_on_request(
    fake_appwrite: FakeAppwriteTables, source: str
) -> None:
    """A closure is a fact worth being able to show, not a row that vanishes."""
    from job_os.ingest.upsert import deactivate_missing

    # Two runs, because that is what deactivation means: this board was read
    # again and the posting was not in it. The write has to carry the earlier
    # run's id -- `deactivate_missing` selects on `last_crawl_run_id != <this
    # run>`, and a NULL there matches nothing (see
    # `test_a_row_no_crawl_ever_stamped_is_left_alone`). This test used to pass
    # no run id at all on the write, which is not a crawl the sweep can produce.
    first_run, second_run = uuid.uuid4(), uuid.uuid4()
    await upsert_postings(
        NO_SESSION,
        [posting(source=source, external_id="closed", title="Engineer")],
        run_id=first_run,
        seen_at=NOW,
    )
    await deactivate_missing(
        NO_SESSION, source=source, board_token="board", run_id=second_run
    )

    default = await search(source)
    including = await search(source, include_inactive=True)

    assert default == []
    assert len(including) == 1
    assert including[0].active is False


async def test_duplicates_are_hidden_by_default(
    fake_appwrite: FakeAppwriteTables, source: str
) -> None:
    from job_os.ingest.upsert import mark_duplicates

    await write(
        posting(source=source, external_id="canonical", title="Engineer"),
        posting(source=source, external_id="dupe", title="Engineer",
                location="New York, NY"),
    )
    # `mark_duplicates` keys on `source_posting_id`, the stable identity
    # `to_row` mints, not on Appwrite's `$id`.
    by_external = {
        row["external_id"]: uuid.UUID(row["source_posting_id"])
        for row in fake_appwrite.all_rows()
        if row["source"] == source
    }
    await mark_duplicates(
        NO_SESSION, [(by_external["dupe"], by_external["canonical"], "exact_key", None)]
    )

    default = await search(source)
    including = await search(source, include_duplicates=True)

    assert [hit.source_id for hit in default] == ["board:canonical"]
    assert len(including) == 2


async def test_unhydrated_rows_are_not_presented_as_having_a_description(
    fake_appwrite: FakeAppwriteTables, source: str
) -> None:
    """A SmartRecruiters listing body is provider metadata, not a JD.

    This is the assertion that caught a real bug rather than a stale test, and
    it could not fail while the file was skipped. `_attach_snippets` was
    overwriting `description_available` with "is there any text in the
    snippet", which for an unhydrated listing is "Engineer\\nAcme\\nBoston" --
    non-empty, so the flag flipped to True and the read path advertised
    provider metadata as a job description. `require_description=True` filters
    on `jd_hydrated` and was always right; only the flag on the way out was
    wrong, which is the worse half, because it is the one a user sees.
    """
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
    await upsert_postings(NO_SESSION, [listing], seen_at=NOW)

    hits = await search(source)
    filtered = await search(source, require_description=True)

    assert hits[0].snippet, "the snippet still carries whatever text there was"
    assert hits[0].description_available is False
    assert filtered == []


async def test_a_hydrated_row_with_an_empty_body_is_also_honest(
    fake_appwrite: FakeAppwriteTables, source: str
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
    await upsert_postings(NO_SESSION, [empty], seen_at=NOW)

    hits = await search(source)

    assert hits[0].description_available is False


async def test_results_are_ordered_by_descending_rank(
    fake_appwrite: FakeAppwriteTables, source: str
) -> None:
    """The contract the UI relies on, asserted directly rather than inferred."""
    await write(
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

    hits = await search(source)
    ranks = [hit.rank for hit in hits]

    assert ranks == sorted(ranks, reverse=True)
