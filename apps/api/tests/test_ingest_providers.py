"""Per-vendor board parsing, against payload shapes taken from real responses.

Each provider gets one test per trap that would silently corrupt the index
rather than raise. The four that matter, and what each would cost:

  * SmartRecruiters answers 200 with `totalFound: 0` for a company that does not
    exist, identically to a real company between hiring rounds. Treating that as
    proof of death prunes every seasonal employer from the corpus.
  * Lever sends epoch MILLISECONDS. Read as seconds, every Lever posting dates to
    1970 and any freshness filter drops the lot.
  * Lever answers a bad slug with a JSON object, not an array. Guarding on the
    status code alone and then iterating would read the error's keys as postings.
  * Greenhouse sends entity-encoded HTML. Skipping the unescape pass stores
    `&lt;p&gt;` as the job description.

The fetcher is faked rather than mocked at the socket, so these tests describe
the parsing contract and never touch the network.
"""
from __future__ import annotations

from typing import Any

import pytest

from job_os.ingest.fetcher import FetchResponse
from job_os.ingest.providers import (
    AshbyProvider,
    BoardStatus,
    GreenhouseProvider,
    LeverProvider,
    SmartRecruitersProvider,
)

pytestmark = pytest.mark.asyncio


class FakeFetcher:
    """Serves canned responses in order and records the URLs asked for."""

    def __init__(self, *responses: FetchResponse) -> None:
        self._responses = list(responses)
        self.urls: list[str] = []
        self.etags: list[str | None] = []

    async def get_json(
        self, url: str, *, host: str, etag: str | None = None, expect_bytes: int = 0
    ) -> FetchResponse:
        self.urls.append(url)
        self.etags.append(etag)
        if not self._responses:
            raise AssertionError(f"unexpected extra request to {url}")
        return self._responses.pop(0)


def ok(payload: Any, *, etag: str | None = "W/\"abc\"", size: int = 1024) -> FetchResponse:
    return FetchResponse(
        status_code=200, payload=payload, etag=etag, bytes_read=size, requests_made=1
    )


def status(code: int, *, payload: Any = None) -> FetchResponse:
    return FetchResponse(
        status_code=code, payload=payload, etag=None, bytes_read=64, requests_made=1
    )


def not_modified() -> FetchResponse:
    return FetchResponse(
        status_code=304,
        payload=None,
        etag="W/\"abc\"",
        bytes_read=0,
        requests_made=1,
        not_modified=True,
    )


# ---------------------------------------------------------------------------
# SmartRecruiters: the trap
# ---------------------------------------------------------------------------


async def test_smartrecruiters_nonexistent_company_is_empty_not_missing() -> None:
    """A company that does not exist answers 200 with totalFound 0.

    Byte for byte the same answer a real company with nothing open gives, so a
    single response can never prove this token is dead. EMPTY is the honest
    verdict; `liveness.py` resolves it over repeated observations instead.
    """
    fetcher = FakeFetcher(ok({"offset": 0, "limit": 100, "totalFound": 0, "content": []}))

    result = await SmartRecruitersProvider().fetch_board(fetcher, "zzznotarealcompany9911")

    assert result.status is BoardStatus.EMPTY
    assert result.status is not BoardStatus.MISSING
    assert result.postings == []


async def test_smartrecruiters_idle_real_company_is_indistinguishable() -> None:
    """The same payload from a real company must reach the same verdict.

    This is the test that documents why the trap cannot be worked around at the
    provider layer: there is no signal here to tell the two apart.
    """
    empty = {"offset": 0, "limit": 100, "totalFound": 0, "content": []}
    fake = await SmartRecruitersProvider().fetch_board(FakeFetcher(ok(empty)), "square")
    unreal = await SmartRecruitersProvider().fetch_board(FakeFetcher(ok(empty)), "notacompany")

    assert fake.status is unreal.status is BoardStatus.EMPTY


async def test_smartrecruiters_404_is_missing() -> None:
    result = await SmartRecruitersProvider().fetch_board(FakeFetcher(status(404)), "gone")

    assert result.status is BoardStatus.MISSING


async def test_smartrecruiters_listing_has_no_description() -> None:
    """Rows land unhydrated, with a factual stand-in built from the listing.

    The body needs one extra call per posting, which for a 4,776 posting board is
    ~4,800 requests, so it is not done during a sweep. `jd_hydrated=False` is what
    stops the read path presenting this metadata as the job description.
    """
    payload = {
        "offset": 0,
        "limit": 100,
        "totalFound": 1,
        "content": [
            {
                "id": "744000",
                "name": "Staff Software Engineer",
                "company": {"identifier": "Example", "name": "Example Inc"},
                "location": {"city": "Boston", "region": "MA", "country": "us",
                             "remote": False, "hybrid": True},
                "department": {"label": "Engineering"},
                "typeOfEmployment": {"label": "Full-time"},
                "experienceLevel": {"label": "Senior"},
                "releasedDate": "2026-08-01T10:00:00.000Z",
            }
        ],
    }

    result = await SmartRecruitersProvider().fetch_board(FakeFetcher(ok(payload)), "example")

    assert result.status is BoardStatus.LIVE
    posting = result.postings[0]
    assert posting.jd_hydrated is False
    # Thin but factual: everything in it came from the listing response.
    assert "Staff Software Engineer" in posting.jd_clean
    assert "Engineering" in posting.jd_clean
    assert posting.country_code == "US"
    assert posting.workplace_type == "Hybrid"
    # `ref` is an API URL. The public page is built from the company identifier.
    assert posting.source_url == "https://jobs.smartrecruiters.com/Example/744000"


async def test_smartrecruiters_pages_by_the_limit_the_server_actually_served() -> None:
    """`limit` is clamped server-side, so paging by what we asked for skips rows."""
    page_one = {
        "offset": 0,
        "limit": 100,
        "totalFound": 150,
        "content": [{"id": str(i), "name": f"Role {i}"} for i in range(100)],
    }
    page_two = {
        "offset": 100,
        "limit": 100,
        "totalFound": 150,
        "content": [{"id": str(i), "name": f"Role {i}"} for i in range(100, 150)],
    }
    fetcher = FakeFetcher(ok(page_one), ok(page_two))

    result = await SmartRecruitersProvider().fetch_board(fetcher, "bigco")

    assert len(result.postings) == 150
    assert "offset=100" in fetcher.urls[1]
    # Only page one carries the conditional header; a 304 there ends the board.
    assert fetcher.etags[1] is None


async def test_smartrecruiters_partial_page_failure_is_error_not_live() -> None:
    """An incomplete list must not be treated as authoritative.

    `BoardResult.usable` is what gates deactivation. If a later page fails and the
    board still reported LIVE, every posting on the missing pages would be
    deactivated because this run did not see them.
    """
    page_one = {
        "offset": 0,
        "limit": 100,
        "totalFound": 150,
        "content": [{"id": str(i), "name": f"Role {i}"} for i in range(100)],
    }
    fetcher = FakeFetcher(ok(page_one), status(503))

    result = await SmartRecruitersProvider().fetch_board(fetcher, "bigco")

    assert result.status is BoardStatus.ERROR
    assert result.usable is False
    assert len(result.postings) == 100


# ---------------------------------------------------------------------------
# Lever
# ---------------------------------------------------------------------------


async def test_lever_created_at_is_milliseconds() -> None:
    """Observed value 1711403416463. As seconds it would be March 1970."""
    payload = [
        {
            "id": "abc-123",
            "text": "Backend Engineer",
            "hostedUrl": "https://jobs.lever.co/example/abc-123",
            "createdAt": 1711403416463,
            "categories": {"location": "Toronto, ON", "commitment": "Full-time"},
            "descriptionPlain": "Work on distributed systems.",
        }
    ]

    result = await LeverProvider().fetch_board(FakeFetcher(ok(payload)), "example")

    posted = result.postings[0].posted_at
    assert posted is not None
    assert posted.year == 2024
    assert posted.month == 3
    assert result.postings[0].posted_at_basis == "created"


async def test_lever_bad_slug_object_is_missing_not_a_posting_list() -> None:
    """404 carries `{"ok": false, "error": "Document not found"}`, an object."""
    fetcher = FakeFetcher(status(404, payload={"ok": False, "error": "Document not found"}))

    result = await LeverProvider().fetch_board(fetcher, "nosuchcompany")

    assert result.status is BoardStatus.MISSING
    assert result.postings == []


async def test_lever_200_with_an_object_is_an_error_not_an_empty_board() -> None:
    """A soft error and an idle board must not reach the same verdict.

    EMPTY is a fact about the company. ERROR is a fact about the request. Only
    the first is safe to act on.
    """
    fetcher = FakeFetcher(ok({"ok": False, "error": "Something went wrong"}))

    result = await LeverProvider().fetch_board(fetcher, "example")

    assert result.status is BoardStatus.ERROR
    assert result.usable is False
    assert result.error == "Something went wrong"


async def test_lever_country_code_is_trusted_when_it_is_an_alpha_2() -> None:
    payload = [
        {
            "id": "1",
            "text": "Engineer",
            "hostedUrl": "https://jobs.lever.co/example/1",
            "country": "de",
            "categories": {"location": "Somewhere unrecognisable"},
            "descriptionPlain": "text",
        }
    ]

    result = await LeverProvider().fetch_board(FakeFetcher(ok(payload)), "example")

    assert result.postings[0].country_code == "DE"


async def test_lever_free_text_country_falls_back_to_inference() -> None:
    payload = [
        {
            "id": "1",
            "text": "Engineer",
            "hostedUrl": "https://jobs.lever.co/example/1",
            "country": "Germany but written out",
            "categories": {"location": "Berlin"},
            "descriptionPlain": "text",
        }
    ]

    result = await LeverProvider().fetch_board(FakeFetcher(ok(payload)), "example")

    assert result.postings[0].country_code == "DE"


async def test_lever_empty_array_is_an_empty_board() -> None:
    result = await LeverProvider().fetch_board(FakeFetcher(ok([])), "example")

    assert result.status is BoardStatus.EMPTY
    assert result.usable is True


# ---------------------------------------------------------------------------
# Ashby
# ---------------------------------------------------------------------------


async def test_ashby_salary_lives_two_levels_down_in_components() -> None:
    """Shape measured on job-board/ramp. Equity rows share the tier."""
    payload = {
        "jobs": [
            {
                "id": "job-1",
                "title": "Software Engineer",
                "jobUrl": "https://jobs.ashbyhq.com/example/job-1",
                "location": "New York",
                "publishedAt": "2026-08-01T00:00:00.000Z",
                "descriptionPlain": "Build product.",
                "compensation": {
                    "compensationTierSummary": "$211.4K - $290.6K - Offers Equity",
                    "compensationTiers": [
                        {
                            "components": [
                                {
                                    "compensationType": "Salary",
                                    "interval": "1 YEAR",
                                    "currencyCode": "USD",
                                    "minValue": 211400,
                                    "maxValue": 290600,
                                },
                                {
                                    "compensationType": "EquityPercentage",
                                    "minValue": None,
                                    "maxValue": None,
                                },
                            ]
                        }
                    ],
                },
            }
        ]
    }

    result = await AshbyProvider().fetch_board(FakeFetcher(ok(payload)), "example")

    posting = result.postings[0]
    assert posting.salary_min == 211400
    assert posting.salary_max == 290600
    assert posting.salary_currency == "USD"
    assert posting.salary_interval == "1 YEAR"


async def test_ashby_accepts_compensation_tiers_at_the_top_level() -> None:
    """The field has been documented both ways; handling both costs three lines."""
    payload = {
        "jobs": [
            {
                "id": "job-1",
                "title": "Engineer",
                "jobUrl": "https://jobs.ashbyhq.com/example/job-1",
                "descriptionPlain": "text",
                "compensationTiers": [
                    {
                        "components": [
                            {
                                "compensationType": "Salary",
                                "currencyCode": "GBP",
                                "minValue": 80000,
                                "maxValue": 100000,
                            }
                        ]
                    }
                ],
            }
        ]
    }

    result = await AshbyProvider().fetch_board(FakeFetcher(ok(payload)), "example")

    assert result.postings[0].salary_min == 80000
    assert result.postings[0].salary_currency == "GBP"


async def test_ashby_multiple_tiers_report_the_widest_span() -> None:
    """One job paid differently by location is still one job.

    The honest single-row answer is the full span, not whichever tier happened to
    be listed first.
    """
    payload = {
        "jobs": [
            {
                "id": "job-1",
                "title": "Engineer",
                "jobUrl": "https://jobs.ashbyhq.com/example/job-1",
                "descriptionPlain": "text",
                "compensationTiers": [
                    {"components": [{"compensationType": "Salary", "currencyCode": "USD",
                                     "minValue": 150000, "maxValue": 180000}]},
                    {"components": [{"compensationType": "Salary", "currencyCode": "USD",
                                     "minValue": 130000, "maxValue": 200000}]},
                ],
            }
        ]
    }

    result = await AshbyProvider().fetch_board(FakeFetcher(ok(payload)), "example")

    assert result.postings[0].salary_min == 130000
    assert result.postings[0].salary_max == 200000


async def test_ashby_unlisted_postings_are_skipped() -> None:
    payload = {
        "jobs": [
            {"id": "hidden", "title": "Hidden", "jobUrl": "https://x.test/1",
             "isListed": False, "descriptionPlain": "text"},
            {"id": "shown", "title": "Shown", "jobUrl": "https://x.test/2",
             "isListed": True, "descriptionPlain": "text"},
        ]
    }

    result = await AshbyProvider().fetch_board(FakeFetcher(ok(payload)), "example")

    assert [p.external_id for p in result.postings] == ["shown"]


async def test_ashby_remote_is_surfaced_in_the_location_label() -> None:
    """`workplaceType` is the trustworthy signal; `isRemote` reads true on hybrids.

    The label matters because a remote-only search that filters on text would
    otherwise drop a posting the board itself calls remote.
    """
    payload = {
        "jobs": [
            {
                "id": "1",
                "title": "Engineer",
                "jobUrl": "https://x.test/1",
                "location": "New York",
                "workplaceType": "Remote",
                "descriptionPlain": "text",
            }
        ]
    }

    result = await AshbyProvider().fetch_board(FakeFetcher(ok(payload)), "example")

    posting = result.postings[0]
    assert posting.remote is True
    assert posting.location is not None and "Remote" in posting.location


async def test_ashby_country_comes_from_the_human_readable_address() -> None:
    """`addressCountry` is a name ("USA"), not an ISO code."""
    payload = {
        "jobs": [
            {
                "id": "1",
                "title": "Engineer",
                "jobUrl": "https://x.test/1",
                "location": "Somewhere",
                "address": {"postalAddress": {"addressCountry": "USA"}},
                "descriptionPlain": "text",
            }
        ]
    }

    result = await AshbyProvider().fetch_board(FakeFetcher(ok(payload)), "example")

    assert result.postings[0].country_code == "US"


@pytest.mark.parametrize("code", [404, 400])
async def test_ashby_client_errors_are_missing(code: int) -> None:
    """Ashby answers an unknown board with a client error, not an empty list."""
    result = await AshbyProvider().fetch_board(FakeFetcher(status(code)), "nosuchboard")

    assert result.status is BoardStatus.MISSING


# ---------------------------------------------------------------------------
# Greenhouse
# ---------------------------------------------------------------------------


async def test_greenhouse_content_is_entity_encoded_html() -> None:
    """Observed on boards/vercel: the body contains no raw `<` at all."""
    payload = {
        "jobs": [
            {
                "id": 4567,
                "title": "Sales Engineer",
                "absolute_url": "https://boards.greenhouse.io/example/jobs/4567",
                "content": (
                    "&lt;div class=&quot;content-intro&quot;&gt;&lt;p&gt;We are hiring."
                    "&lt;/p&gt;&lt;ul&gt;&lt;li&gt;Own the pipeline&lt;/li&gt;"
                    "&lt;/ul&gt;&lt;/div&gt;"
                ),
                "first_published": "2026-08-01T09:00:00-04:00",
                "location": {"name": "Remote - US"},
            }
        ]
    }

    board = await GreenhouseProvider().fetch_board(FakeFetcher(ok(payload)), "example")

    posting = board.postings[0]
    assert "We are hiring." in posting.jd_clean
    assert "Own the pipeline" in posting.jd_clean
    assert "&lt;" not in posting.jd_clean
    assert "<p>" not in posting.jd_clean


async def test_greenhouse_first_published_is_not_an_estimate() -> None:
    payload = {
        "jobs": [
            {
                "id": 1,
                "title": "Engineer",
                "absolute_url": "https://boards.greenhouse.io/example/jobs/1",
                "content": "text",
                "first_published": "2026-08-01T09:00:00Z",
                "updated_at": "2026-08-10T09:00:00Z",
            }
        ]
    }

    result = await GreenhouseProvider().fetch_board(FakeFetcher(ok(payload)), "example")

    posting = result.postings[0]
    assert posting.posted_at_basis == "published"
    assert posting.posted_at_estimated is False
    assert posting.posted_at is not None and posting.posted_at.day == 1


async def test_greenhouse_falls_back_to_updated_at_and_says_it_is_estimated() -> None:
    """`updated_at` is an upper bound on when it went up, never the posting date."""
    payload = {
        "jobs": [
            {
                "id": 1,
                "title": "Engineer",
                "absolute_url": "https://boards.greenhouse.io/example/jobs/1",
                "content": "text",
                "updated_at": "2026-08-10T09:00:00Z",
            }
        ]
    }

    result = await GreenhouseProvider().fetch_board(FakeFetcher(ok(payload)), "example")

    posting = result.postings[0]
    assert posting.posted_at_basis == "updated"
    assert posting.posted_at_estimated is True


async def test_greenhouse_404_is_missing() -> None:
    result = await GreenhouseProvider().fetch_board(FakeFetcher(status(404)), "gone")

    assert result.status is BoardStatus.MISSING


async def test_greenhouse_reports_the_company_name_the_board_states() -> None:
    """The bulk corpus carries no employer names, so learning them here matters."""
    payload = {
        "jobs": [
            {
                "id": 1,
                "title": "Engineer",
                "absolute_url": "https://boards.greenhouse.io/example/jobs/1",
                "content": "text",
                "company_name": "Example Corporation",
            }
        ]
    }

    result = await GreenhouseProvider().fetch_board(FakeFetcher(ok(payload)), "example")

    assert result.postings[0].company_name == "Example Corporation"


# ---------------------------------------------------------------------------
# shared contract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "provider",
    [GreenhouseProvider(), LeverProvider(), AshbyProvider(), SmartRecruitersProvider()],
    ids=["greenhouse", "lever", "ashby", "smartrecruiters"],
)
async def test_304_is_not_modified_and_not_usable(provider: Any) -> None:
    """A 304 means we did not see the current list, so nothing may be closed.

    This is the difference between a cheap re-crawl and a crawler that deactivates
    a company's whole board every time its ETag matches.
    """
    result = await provider.fetch_board(FakeFetcher(not_modified()), "example", "W/\"abc\"")

    assert result.status is BoardStatus.NOT_MODIFIED
    assert result.usable is False
    assert result.postings == []


@pytest.mark.parametrize(
    "provider",
    [GreenhouseProvider(), LeverProvider(), AshbyProvider(), SmartRecruitersProvider()],
    ids=["greenhouse", "lever", "ashby", "smartrecruiters"],
)
async def test_server_errors_are_not_usable(provider: Any) -> None:
    result = await provider.fetch_board(FakeFetcher(status(500)), "example")

    assert result.status is BoardStatus.ERROR
    assert result.usable is False


@pytest.mark.parametrize(
    "provider",
    [GreenhouseProvider(), LeverProvider(), AshbyProvider()],
    ids=["greenhouse", "lever", "ashby"],
)
async def test_source_id_is_the_board_token_and_external_id(provider: Any) -> None:
    """Matches the web app's `{token}:{id}`, so a crawled row and a live-fetched
    row for the same posting collide instead of both being stored."""
    payloads: dict[str, Any] = {
        "greenhouse": {"jobs": [{"id": 7, "title": "T",
                                 "absolute_url": "https://x.test/7", "content": "c"}]},
        "lever": [{"id": "7", "text": "T", "hostedUrl": "https://x.test/7",
                   "descriptionPlain": "c"}],
        "ashby": {"jobs": [{"id": "7", "title": "T", "jobUrl": "https://x.test/7",
                            "descriptionPlain": "c"}]},
    }

    result = await provider.fetch_board(FakeFetcher(ok(payloads[provider.name])), "acme")

    assert result.postings[0].source_id == "acme:7"
