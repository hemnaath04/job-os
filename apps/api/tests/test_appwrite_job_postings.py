"""The Appwrite-backed halves of the job_postings pipeline, mocked.

`job_index.search_index` and `ingest.upsert.upsert_postings`/
`deactivate_missing` no longer touch Postgres (see each module's own
docstring for the full migration rationale and the tradeoffs it accepted).
Real, live-Appwrite verification of every scenario here was done by hand
against the actual `job_postings` table during the rewrite; these tests pin
the same contracts against a mocked `appwrite_tables` module so they run
without a live service or a local Postgres instance, matching how
`test_discovery_sources.py` mocks its own external calls.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from job_os.ingest import upsert
from job_os.ingest.providers import RawPosting
from job_os.services import job_index
from job_os.services.job_index import IndexQuery


def make_posting(**overrides) -> RawPosting:
    defaults = dict(
        source="greenhouse",
        board_token="acme",
        external_id="1",
        title="Software Engineer",
        company_name="Acme",
        company_domain="acme.test",
        source_url="https://example.test/acme/1",
        jd_clean="Build things. Ship them. Learn from users.",
        posted_at_basis="published",
    )
    defaults.update(overrides)
    return RawPosting(**defaults)


class TestUpsertPostings:
    async def test_new_posting_is_inserted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: list[dict] = []

        async def fake_list_rows(**kwargs):
            return []

        async def fake_upsert_rows(rows):
            captured.extend(rows)

        monkeypatch.setattr(upsert.appwrite_tables, "list_rows", fake_list_rows)
        monkeypatch.setattr(upsert.appwrite_tables, "upsert_rows", fake_upsert_rows)

        stats = await upsert.upsert_postings(None, [make_posting()], seen_at=datetime.now(UTC))

        assert stats.inserted == 1
        assert stats.updated == stats.unchanged == stats.reactivated == 0
        assert "$id" not in captured[0]
        assert captured[0]["source_id"] == "acme:1"
        assert "Acme" in captured[0]["search_text"]

    async def test_unchanged_content_preserves_first_seen_at(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        original_first_seen = "2026-01-01T00:00:00+00:00"
        posting = make_posting()
        existing_hash = upsert.to_row(posting, run_id=None, seen_at=datetime.now(UTC))["content_hash"]

        async def fake_list_rows(**kwargs):
            return [
                {
                    "$id": "row1",
                    "source": "greenhouse",
                    "source_id": "acme:1",
                    "content_hash": existing_hash,
                    "active": True,
                    "first_seen_at": original_first_seen,
                    "repost_count": 0,
                }
            ]

        captured: list[dict] = []

        async def fake_upsert_rows(rows):
            captured.extend(rows)

        monkeypatch.setattr(upsert.appwrite_tables, "list_rows", fake_list_rows)
        monkeypatch.setattr(upsert.appwrite_tables, "upsert_rows", fake_upsert_rows)

        stats = await upsert.upsert_postings(None, [posting], seen_at=datetime.now(UTC))

        assert stats.unchanged == 1
        assert stats.updated == stats.inserted == 0
        payload = captured[0]
        assert payload["$id"] == "row1"
        # No mutable columns re-sent when nothing changed -- and critically,
        # first_seen_at is never even a candidate key here at all.
        assert "first_seen_at" not in payload
        assert "title" not in payload

    async def test_changed_content_updates_and_reactivates(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        posting = make_posting(title="Senior Software Engineer")

        async def fake_list_rows(**kwargs):
            return [
                {
                    "$id": "row1",
                    "source": "greenhouse",
                    "source_id": "acme:1",
                    "content_hash": "stale-hash-does-not-match",
                    "active": False,
                    "first_seen_at": "2026-01-01T00:00:00+00:00",
                    "repost_count": 2,
                }
            ]

        captured: list[dict] = []

        async def fake_upsert_rows(rows):
            captured.extend(rows)

        monkeypatch.setattr(upsert.appwrite_tables, "list_rows", fake_list_rows)
        monkeypatch.setattr(upsert.appwrite_tables, "upsert_rows", fake_upsert_rows)

        stats = await upsert.upsert_postings(None, [posting], seen_at=datetime.now(UTC))

        assert stats.updated == 1
        assert stats.reactivated == 1
        payload = captured[0]
        assert payload["active"] is True
        assert payload["repost_count"] == 3
        assert payload["title"] == "Senior Software Engineer"

    async def test_duplicate_identity_in_one_batch_is_skipped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def fake_list_rows(**kwargs):
            return []

        async def fake_upsert_rows(rows):
            pass

        monkeypatch.setattr(upsert.appwrite_tables, "list_rows", fake_list_rows)
        monkeypatch.setattr(upsert.appwrite_tables, "upsert_rows", fake_upsert_rows)

        stats = await upsert.upsert_postings(
            None, [make_posting(), make_posting()], seen_at=datetime.now(UTC)
        )
        assert stats.inserted == 1
        assert stats.skipped == 1


class TestDeactivateMissing:
    async def test_calls_update_rows_with_the_right_filters(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen = {}

        async def fake_update_rows(*, filters, data):
            seen["filters"] = filters
            seen["data"] = data
            return 3

        monkeypatch.setattr(upsert.appwrite_tables, "update_rows", fake_update_rows)

        run_id = uuid.uuid4()
        result = await upsert.deactivate_missing(
            None, source="greenhouse", board_token="acme", run_id=run_id
        )

        assert result == 3
        assert "source=greenhouse" in seen["filters"]
        assert "board_token=acme" in seen["filters"]
        assert "active=true" in seen["filters"]
        assert f"last_crawl_run_id!={run_id}" in seen["filters"]
        assert seen["data"]["active"] is False


class TestSearchIndex:
    async def test_ranks_by_freshness_and_attaches_snippets(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        now = datetime.now(UTC)
        rows = [
            {
                "source_posting_id": str(uuid.uuid4()),
                "source": "greenhouse",
                "source_id": "acme:1",
                "source_url": "https://example.test/1",
                "title": "Old Engineer Role",
                "company_name": "Acme",
                "jd_clean": "old posting description",
                "jd_hydrated": True,
                "posted_at": None,
                "posted_at_basis": "first_crawl",
                "posted_at_estimated": True,
                "first_seen_at": "2020-01-01T00:00:00+00:00",
                "last_seen_at": "2020-01-01T00:00:00+00:00",
                "active": True,
                "repost_count": 0,
            },
            {
                "source_posting_id": str(uuid.uuid4()),
                "source": "greenhouse",
                "source_id": "acme:2",
                "source_url": "https://example.test/2",
                "title": "Fresh Engineer Role",
                "company_name": "Acme",
                "jd_clean": "fresh posting description",
                "jd_hydrated": True,
                "posted_at": now.isoformat(),
                "posted_at_basis": "published",
                "posted_at_estimated": False,
                "first_seen_at": now.isoformat(),
                "last_seen_at": now.isoformat(),
                "active": True,
                "repost_count": 0,
            },
        ]

        async def fake_list_rows(**kwargs):
            return rows

        monkeypatch.setattr(job_index.appwrite_tables, "list_rows", fake_list_rows)

        result = await job_index.search_index(None, IndexQuery(query="engineer", limit=10))

        assert result.candidates_considered == 2
        assert result.hits[0].title == "Fresh Engineer Role"
        assert result.hits[0].rank > result.hits[1].rank
        assert result.hits[0].snippet == "fresh posting description"
        assert result.keyword_query == "engineer"

    async def test_no_keywords_is_a_pure_freshness_browse(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured = {}

        async def fake_list_rows(**kwargs):
            captured.update(kwargs)
            return []

        monkeypatch.setattr(job_index.appwrite_tables, "list_rows", fake_list_rows)

        result = await job_index.search_index(None, IndexQuery(limit=5))

        assert result.keyword_query is None
        assert captured["queries"] == [{"method": "isNull", "attribute": "canonical_id"}]
        assert "active=true" in captured["filters"]
