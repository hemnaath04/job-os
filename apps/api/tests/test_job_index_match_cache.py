"""The fit score attached to a search result, and the cache underneath it.

`search_index(..., candidate=...)` scores every hit on the page it returns, and
enriches any posting nobody has enriched yet. Enrichment is the one real LLM
call the whole design rests on, so where its result is written is not an
implementation detail: a cache that misses means a Sonnet call per posting per
search, which is what `MAX_ENRICH_PER_SEARCH` and `ENRICH_DEADLINE_SECONDS`
exist to bound and what once produced an H12 on the whole search.

The column moved with the store. In Appwrite the cache lived in a bespoke
`enrichment` column and `job_index` had to wrap its raw value in a fake
`{"enrichment": ...}` dict just to reuse `job_enrich.load_enrichment`, which was
written against `jobs.jd_parsed`. `job_postings.jd_parsed` is that same JSONB
column and that same shape, so the real `store_enrichment`/`load_enrichment`
pair runs here now, unwrapped. This file covers both halves of that: a cached
document is read back and scored without an LLM call, and a newly computed one
is written where the next search will find it.

`enrich_job` is faked. It is the network, and its own behaviour is covered in
`test_job_enrich.py`.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from job_os.db.models.job_posting import JobPosting
from job_os.ingest.providers import RawPosting
from job_os.ingest.upsert import upsert_postings
from job_os.schemas.enrichment import JobEnrichment, SkillRequirement
from job_os.services import job_enrich, job_index
from job_os.services.job_index import IndexQuery, search_index
from job_os.services.job_match import CandidateProfile

pytestmark = pytest.mark.asyncio

NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


@pytest.fixture
def source() -> str:
    return f"match_{uuid.uuid4().hex[:12]}"


def enrichment(title: str = "Backend Engineer") -> JobEnrichment:
    return JobEnrichment(
        core_job_title=title,
        skills=[
            SkillRequirement(skill="python", importance=3, necessity="required"),
            SkillRequirement(skill="postgres", importance=3, necessity="required"),
            SkillRequirement(skill="kubernetes", importance=2, necessity="preferred"),
            SkillRequirement(skill="terraform", importance=1, necessity="preferred"),
        ],
    )


def posting(source: str, external_id: str = "1") -> RawPosting:
    return RawPosting(
        source=source,
        board_token="board",
        external_id=external_id,
        title="Backend Engineer",
        company_name="Acme",
        company_domain="acme.test",
        source_url=f"https://example.test/{external_id}",
        jd_clean="We run Python services against Postgres and deploy on Kubernetes.",
        location="Boston, MA",
        posted_at=NOW,
        posted_at_basis="published",
    )


async def stored_jd_parsed(session: AsyncSession, source: str) -> dict:
    value = await session.scalar(
        select(JobPosting.jd_parsed).where(JobPosting.source == source)
    )
    assert value is not None
    return value


async def test_a_cached_enrichment_scores_the_hit_without_an_llm_call(
    db_session: AsyncSession, source: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point of writing the document back. If this misses, every
    search pays for enrichment again."""

    async def _must_not_run(*_args: object, **_kwargs: object) -> JobEnrichment:
        raise AssertionError("a cached posting must not be enriched again")

    monkeypatch.setattr(job_index.job_enrich, "enrich_job", _must_not_run)

    await upsert_postings(db_session, [posting(source)], seen_at=NOW)
    await db_session.execute(
        JobPosting.__table__.update()
        .where(JobPosting.source == source)
        .values(jd_parsed=job_enrich.store_enrichment({}, enrichment()))
    )

    result = await search_index(
        db_session,
        IndexQuery(sources=[source]),
        candidate=CandidateProfile.build(skills=["python", "postgres"]),
    )

    assert len(result.hits) == 1
    match = result.hits[0].match
    assert match is not None
    assert "python" in match.matched_skills
    assert "kubernetes" in match.missing_skills


async def test_a_freshly_enriched_posting_is_written_back_for_the_next_search(
    db_session: AsyncSession, source: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Written to `jd_parsed`, in the shape `load_enrichment` reads.

    Asserting the column and the shape rather than just "a score came back",
    because the failure this guards against is silent: an enrichment written
    somewhere the reader does not look scores this search correctly and every
    later search pays for it again.
    """
    calls = 0

    async def _fake_enrich(*_args: object, **_kwargs: object) -> JobEnrichment:
        nonlocal calls
        calls += 1
        return enrichment()

    monkeypatch.setattr(job_index.job_enrich, "enrich_job", _fake_enrich)

    await upsert_postings(db_session, [posting(source)], seen_at=NOW)
    candidate = CandidateProfile.build(skills=["python", "postgres"])

    first = await search_index(db_session, IndexQuery(sources=[source]), candidate=candidate)
    assert first.hits[0].match is not None
    assert calls == 1

    stored = await stored_jd_parsed(db_session, source)
    assert job_enrich.load_enrichment(stored) is not None

    second = await search_index(db_session, IndexQuery(sources=[source]), candidate=candidate)
    assert second.hits[0].match is not None
    assert calls == 1, "the second search must read the cache, not the gateway"


async def test_the_provider_payload_survives_an_enrichment_write(
    db_session: AsyncSession, source: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`jd_parsed` has two writers and they must not overwrite each other.

    `ingest/hydrate.py` keeps its per-row attempt counter in this column, and
    the sweep writes the provider's own `extra` blob into it. `store_enrichment`
    merges rather than replaces, which is exactly why it is reused here instead
    of a `values(jd_parsed={...})` that looked simpler.
    """

    async def _fake_enrich(*_args: object, **_kwargs: object) -> JobEnrichment:
        return enrichment()

    monkeypatch.setattr(job_index.job_enrich, "enrich_job", _fake_enrich)

    row = posting(source)
    row.extra = {"external_path": "/job/loc/E_JR1", "hydrate_attempts": 2}
    await upsert_postings(db_session, [row], seen_at=NOW)

    await search_index(
        db_session,
        IndexQuery(sources=[source]),
        candidate=CandidateProfile.build(skills=["python"]),
    )

    stored = await stored_jd_parsed(db_session, source)
    assert stored["external_path"] == "/job/loc/E_JR1"
    assert stored["hydrate_attempts"] == 2
    assert job_enrich.load_enrichment(stored) is not None


async def test_no_candidate_means_no_score_and_no_llm_call(
    db_session: AsyncSession, source: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A signed-out caller, or one whose profile has no usable signal.

    `match` stays None and the frontend's own lexicon fallback renders instead.
    Scoring anyway would spend a real gateway call per posting to produce a
    number that says nothing.
    """

    async def _must_not_run(*_args: object, **_kwargs: object) -> JobEnrichment:
        raise AssertionError("no candidate means no enrichment")

    monkeypatch.setattr(job_index.job_enrich, "enrich_job", _must_not_run)

    await upsert_postings(db_session, [posting(source)], seen_at=NOW)

    result = await search_index(db_session, IndexQuery(sources=[source]))

    assert result.hits[0].match is None


async def test_the_body_is_only_fetched_when_something_needs_it(
    db_session: AsyncSession, source: str
) -> None:
    """`_fetch_page`'s `want_body` split, asserted where it is observable.

    The snippet is `left(jd_clean, 400)` computed in SQL; the whole body is
    only selected when there is a candidate to enrich against, because that is
    the only thing that reads it. A browse of 200 results should not move 200
    full descriptions to render 200 previews of them.
    """
    long_body = "Sentence about the work. " * 200
    row = posting(source)
    row.jd_clean = long_body
    await upsert_postings(db_session, [row], seen_at=NOW)

    page = await job_index._fetch_page(
        db_session,
        (await search_index(db_session, IndexQuery(sources=[source]))).hits,
        want_body=False,
    )
    only = next(iter(page.values()))

    assert only.jd_clean is None
    assert len(only.snippet) == job_index.SNIPPET_CHARS
