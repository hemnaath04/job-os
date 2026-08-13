"""What `sources` accepts, and what happens to a source this API does not run.

The old `Literal["theirstack","github"]` rejected every other source id with a
422, including ones the product ships. A saved search therefore could not hold
what the user actually selected, and the web app worked around it by stripping
the rest into localStorage, where no second device can see them.

Widening the type opens a path that was unreachable before: a search naming a
source with no runner. That used to be impossible, so the fan-out zipped the
requested list against the results with `strict=True`, which would now raise.
Both halves are covered here.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from job_os.schemas.discovery import (
    BACKEND_SOURCES,
    KNOWN_SOURCES,
    WEB_SOURCES,
    DiscoverySearchRequest,
    DiscoverySourceError,
    SavedSearchCreate,
)

# Every id apps/web/src/lib/discover/sources.ts can put in a selection. Written
# out rather than imported from the schema, so that dropping one from the schema
# fails here instead of silently narrowing what a saved search can hold.
WEB_APP_SOURCE_IDS = [
    "theirstack",
    "github",
    "greenhouse",
    "lever",
    "ashby",
    "remotive",
    "remoteok",
    "feed:himalayas",
    "feed:jobicy",
    "feed:arbeitnow",
    "jsearch",
    "adzuna",
]


@pytest.mark.parametrize("source", WEB_APP_SOURCE_IDS)
def test_every_source_the_web_app_offers_is_accepted(source: str) -> None:
    assert DiscoverySearchRequest(sources=[source]).sources == [source]


def test_a_whole_selection_survives_in_one_request() -> None:
    """The case that mattered: the user's real selection spans both backends."""
    request = DiscoverySearchRequest(sources=WEB_APP_SOURCE_IDS)

    assert request.sources == WEB_APP_SOURCE_IDS


@pytest.mark.parametrize(
    "source",
    [
        # The two shapes custom-sources.ts actually mints: crypto.randomUUID(),
        # and the cs-<base36>-<base36> fallback for a context without it.
        "custom:0f9d3c1e-6b2a-4f7d-9c31-2ab8e5d47f60",
        "custom:cs-m1a2b3c4-7f9qz1x2",
        "custom:my-board",
        "custom:board_1",
        "custom:a",
        "custom:feed.example~2",
    ],
)
def test_a_users_own_endpoint_is_accepted(source: str) -> None:
    """No union can enumerate a user-generated id, which is the other half of why
    the Literal could not work."""
    assert DiscoverySearchRequest(sources=[source]).sources == [source]


@pytest.mark.parametrize(
    "source",
    [
        "custom:",  # the prefix alone names nothing
        "custom:has spaces",
        "custom:" + "x" * 65,  # past the id cap
        "custom",
        "linkedin",  # plausible, and not a source this product has
        "THEIRSTACK",  # ids are lower case
        "greenhouse ",
        "",
        "feed:unheard-of",
        "'; drop table jobs; --",
    ],
)
def test_an_unknown_source_is_still_rejected(source: str) -> None:
    """Widened, not opened. A typo must still be a 422 rather than a source that
    silently returns nothing forever."""
    with pytest.raises(ValidationError):
        DiscoverySearchRequest(sources=[source])


def test_the_rejection_message_names_the_offending_value() -> None:
    with pytest.raises(ValidationError) as raised:
        DiscoverySearchRequest(sources=["linkedin"])

    message = str(raised.value)
    assert "linkedin" in message
    assert "custom:<id>" in message


def test_the_default_is_unchanged() -> None:
    assert DiscoverySearchRequest().sources == ["theirstack"]


def test_a_saved_search_can_store_a_mixed_selection() -> None:
    """The whole point of the widening: this used to 422 on save."""
    saved = SavedSearchCreate.model_validate(
        {
            "name": "New grad, everywhere",
            "query": {
                "sources": ["theirstack", "greenhouse", "feed:himalayas", "custom:my-board"],
                "title_keywords": ["software engineer"],
            },
        }
    )

    assert saved.query.sources == [
        "theirstack",
        "greenhouse",
        "feed:himalayas",
        "custom:my-board",
    ]


def test_a_source_error_can_name_any_accepted_source() -> None:
    """`DiscoverySourceError.source` shares the type, so a narrow one here would
    have made an error about a web-served source unreportable."""
    error = DiscoverySourceError(source="custom:my-board", message="timed out")

    assert error.source == "custom:my-board"


def test_the_backend_and_web_source_sets_do_not_overlap() -> None:
    """Two runtimes, one id space. An id in both would make routing ambiguous."""
    assert not BACKEND_SOURCES & WEB_SOURCES
    assert KNOWN_SOURCES == BACKEND_SOURCES | WEB_SOURCES


def test_the_backend_set_matches_the_runners_that_exist() -> None:
    """`BACKEND_SOURCES` documents which ids this API fetches. If it drifts from
    the fan-out, the zero-count path below stops meaning what it says."""
    from job_os.routers import discovery

    assert sorted(BACKEND_SOURCES) == ["github", "theirstack"]
    assert hasattr(discovery, "_search_theirstack")
    assert hasattr(discovery, "_search_github")


async def test_a_search_for_only_web_served_sources_returns_empty_not_500() -> None:
    """The path the old Literal made unreachable.

    `zip(..., strict=True)` in the fan-out raises when the requested list is
    longer than the results, which is every request naming a source with no
    runner. A saved search holding the user's full selection is exactly that
    request, so this is the crash the widening would otherwise have shipped.
    """
    from job_os.routers.discovery import _run_search

    request = DiscoverySearchRequest(sources=["greenhouse", "feed:jobicy", "custom:my-board"])
    response = await _run_search(request, session=None)  # type: ignore[arg-type]

    assert response.results == []
    assert response.errors == []
    # Reported at zero rather than omitted or errored: the caller's own half of
    # the search adds its counts to these, and an error would put a warning
    # banner on a search that worked.
    assert response.source_counts == {
        "greenhouse": 0,
        "feed:jobicy": 0,
        "custom:my-board": 0,
    }


async def test_a_mixed_search_runs_the_served_half_and_zeroes_the_rest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A served source must still be fetched, and paired with its own result."""
    from job_os.routers import discovery
    from job_os.schemas.discovery import DiscoveryResult

    hit = DiscoveryResult(
        source="github",
        source_id="1",
        source_url="https://example.test/1",
        title="New Grad SWE",
        description="",
    )

    async def fake_github(_payload: DiscoverySearchRequest) -> list[DiscoveryResult]:
        return [hit]

    async def unreachable(_payload: DiscoverySearchRequest) -> list[DiscoveryResult]:
        raise AssertionError("theirstack was not requested")

    async def skip_dedupe(_session: object, _results: list[DiscoveryResult]) -> None:
        """Stubbed for want of a database, not because it is uninteresting: the
        already-imported annotation is a real query and is covered elsewhere."""

    monkeypatch.setattr(discovery, "_search_github", fake_github)
    monkeypatch.setattr(discovery, "_search_theirstack", unreachable)
    monkeypatch.setattr(discovery, "_annotate_already_imported", skip_dedupe)

    request = DiscoverySearchRequest(sources=["greenhouse", "github", "custom:my-board"])
    response = await discovery._run_search(request, session=None)  # type: ignore[arg-type]

    assert [r.source_id for r in response.results] == ["1"]
    assert response.source_counts == {"greenhouse": 0, "custom:my-board": 0, "github": 1}


async def test_an_empty_source_list_is_still_a_400() -> None:
    from fastapi import HTTPException

    from job_os.routers.discovery import _run_search

    with pytest.raises(HTTPException) as raised:
        await _run_search(DiscoverySearchRequest(sources=[]), session=None)  # type: ignore[arg-type]

    assert raised.value.status_code == 400
