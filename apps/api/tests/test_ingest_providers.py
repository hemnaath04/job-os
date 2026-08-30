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
  * Workday's list `postedOn` is prose ("Posted 30+ Days Ago"). Recording it as
    a date would date every posting to the day it was crawled and call that the
    employer's own figure.
  * BambooHR answers HTTP 200 for a slug that does not exist, by redirecting to
    its own marketing site. Judging liveness on the status code files 43KB of
    product copy as a live job board.
  * BambooHR's `isRemote` is null on every posting ever measured. The field that
    carries the answer is `locationType`, so reading the obvious one marks every
    BambooHR role onsite, including the ones titled "(Remote)".

The fetcher is faked rather than mocked at the socket, so these tests describe
the parsing contract and never touch the network.
"""
from __future__ import annotations

from typing import Any

import pytest

from job_os.ingest.fetcher import FetchResponse
from job_os.ingest.providers import (
    AshbyProvider,
    BambooHRProvider,
    BoardStatus,
    GreenhouseProvider,
    LeverProvider,
    SmartRecruitersProvider,
    WorkdayProvider,
)

pytestmark = pytest.mark.asyncio


class FakeFetcher:
    """Serves canned responses in order and records the URLs asked for."""

    def __init__(self, *responses: FetchResponse) -> None:
        self._responses = list(responses)
        self.urls: list[str] = []
        self.etags: list[str | None] = []
        self.bodies: list[dict[str, Any]] = []

    async def get_json(
        self, url: str, *, host: str, etag: str | None = None, expect_bytes: int = 0
    ) -> FetchResponse:
        self.urls.append(url)
        self.etags.append(etag)
        if not self._responses:
            raise AssertionError(f"unexpected extra request to {url}")
        return self._responses.pop(0)

    async def post_json(
        self, url: str, *, host: str, body: dict[str, Any]
    ) -> FetchResponse:
        """Workday's list is a POST. The body is recorded because its `offset`
        is how pagination is driven, and a provider that never advanced it
        would loop on page one and look like a board of exactly 20 jobs."""
        self.urls.append(url)
        self.bodies.append(body)
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


# ── Workday ──
#
# Payload shapes copied from real responses (NVIDIA wd5, Workiva wd503,
# Salesforce wd12, fetched 2026-08-29), trimmed to the fields the parser reads.

WD_TOKEN = "nvidia:wd5:NVIDIAExternalCareerSite"


def wd_page(*paths: str, posted: str = "Posted Today") -> dict[str, Any]:
    return {
        "total": 2000,
        "jobPostings": [
            {
                "title": f"Engineer {i}",
                "externalPath": path,
                "locationsText": "US-CA-Santa Clara",
                "postedOn": posted,
                "bulletFields": ["JR100" + str(i)],
            }
            for i, path in enumerate(paths)
        ],
    }


async def test_workday_pages_until_a_short_page() -> None:
    """`limit` is capped at 20 by the vendor, so pagination is not optional.

    A provider that stopped after page one would report NVIDIA as a 20-job
    company. Twenty is Workday's own ceiling: `limit=50` returns HTTP 400.
    """
    full = wd_page(*(f"/job/loc/Engineer_JR{i:04d}" for i in range(20)))
    tail = wd_page("/job/loc/Engineer_JR9999")
    fetcher = FakeFetcher(ok(full), ok(tail))

    result = await WorkdayProvider().fetch_board(fetcher, WD_TOKEN)

    assert result.status is BoardStatus.LIVE
    assert len(result.postings) == 21
    assert [b["offset"] for b in fetcher.bodies] == [0, 20]


async def test_workday_never_claims_the_list_date_is_the_posting_date() -> None:
    """The trap this provider exists around.

    The list's `postedOn` is prose -- "Posted Today", "Posted 30+ Days Ago" --
    with no date in it at all. Reading it as a date would stamp every posting
    with the crawl date and label that the employer's own. Only the DETAIL
    payload carries a real `startDate`, so a list-only row is `first_crawl`.
    """
    fetcher = FakeFetcher(ok(wd_page("/job/loc/E_JR1", posted="Posted 30+ Days Ago")))

    result = await WorkdayProvider().fetch_board(fetcher, WD_TOKEN)

    posting = result.postings[0]
    assert posting.posted_at is None
    assert posting.posted_at_basis == "first_crawl"
    assert posting.posted_at_estimated is True
    assert posting.jd_hydrated is False, "the list carries no description"


async def test_workday_hydration_supplies_the_employers_own_date() -> None:
    """And what hydration buys: a real date, from `startDate`.

    Verified against a live board -- "Posted Today" on the list alongside
    `startDate: 2026-08-29` on the detail for the same requisition.
    """
    fetcher = FakeFetcher(ok(wd_page("/job/loc/E_JR1")))
    provider = WorkdayProvider()
    result = await provider.fetch_board(fetcher, WD_TOKEN)

    detail = FakeFetcher(
        ok(
            {
                "jobPostingInfo": {
                    "jobReqId": "JR2022858",
                    "startDate": "2026-08-29",
                    "jobDescription": "<p>Build <b>maps</b>.</p><li>C++</li>",
                    "externalUrl": "https://nvidia.wd5.myworkdayjobs.com/x/job/JR2022858",
                }
            }
        )
    )
    hydrated = await provider.hydrate(detail, WD_TOKEN, result.postings[0])

    assert hydrated.jd_hydrated is True
    assert hydrated.posted_at_basis == "published"
    assert hydrated.posted_at is not None and hydrated.posted_at.year == 2026
    assert hydrated.external_id == "JR2022858"
    # The HTML is flattened, not stored as tags.
    assert "<p>" not in hydrated.jd_clean
    assert "Build maps." in hydrated.jd_clean
    assert "- C++" in hydrated.jd_clean


@pytest.mark.parametrize(
    ("response", "why"),
    [
        (status(422), "no such tenant: *.myworkdayjobs.com is wildcard DNS, so a "
                      "wrong tenant resolves and answers rather than failing to connect"),
        (status(404, payload={"errorCode": "S21"}), "tenant exists, site id does not"),
    ],
)
async def test_workday_knows_its_two_missing_board_shapes(
    response: FetchResponse, why: str
) -> None:
    """MISSING prunes the token; ERROR retries it. Confusing them either
    strands a dead board forever or drops a live one."""
    result = await WorkdayProvider().fetch_board(FakeFetcher(response), WD_TOKEN)

    assert result.status is BoardStatus.MISSING, why


async def test_workday_a_bare_404_is_not_a_missing_board() -> None:
    """Only 404 WITH `errorCode: S21` means the site is gone. A bare 404 is a
    transport-level answer that says nothing about the board, and pruning on it
    would delete boards over a bad afternoon."""
    result = await WorkdayProvider().fetch_board(FakeFetcher(status(404)), WD_TOKEN)

    assert result.status is BoardStatus.ERROR


async def test_workday_a_multi_site_tenant_does_not_duplicate_a_requisition() -> None:
    """Workday serves the same requisition under sibling sites, so one board
    can repeat a path across pages. Two rows for one job would be counted
    twice and shown twice."""
    repeated = wd_page(*(["/job/loc/E_JR1"] * 20))
    fetcher = FakeFetcher(ok(repeated), ok(wd_page()))

    result = await WorkdayProvider().fetch_board(fetcher, WD_TOKEN)

    assert len(result.postings) == 1


async def test_workday_a_location_count_is_not_a_location() -> None:
    """`locationsText` is "4 Locations" on a multi-site posting. Storing that
    indexes the job as being in a city called "4 Locations"."""
    page = wd_page("/job/loc/E_JR1")
    page["jobPostings"][0]["locationsText"] = "4 Locations"

    result = await WorkdayProvider().fetch_board(FakeFetcher(ok(page)), WD_TOKEN)

    assert result.postings[0].location is None


@pytest.mark.parametrize("token", ["nvidia", "nvidia:careers", "nvidia:x5:site", ""])
async def test_workday_rejects_a_token_that_cannot_address_a_board(token: str) -> None:
    """Three parts, because the host is per-tenant and the site is not
    derivable from it. A malformed token that reached a request would build a
    URL pointing at some other tenant's board -- and wildcard DNS means that
    URL resolves rather than failing."""
    result = await WorkdayProvider().fetch_board(FakeFetcher(), token)

    assert result.status is BoardStatus.MISSING
    assert result.requests_made == 0, "a bad token must not reach the network"


# ── BambooHR ──
#
# Payload shapes copied from real responses (soundstripe, canopy, anvil, titan,
# trajectory, fetched 2026-08-30), trimmed to the fields the parser reads.

BH_TOKEN = "soundstripe"

BH_NASHVILLE = {
    "country": "United States",
    "state": "Tennessee",
    "province": None,
    "city": "Nashville",
}


def bh_row(
    job_id: str = "167",
    *,
    name: str = "Head of Sales (Remote)",
    location_type: str | None = "1",
    location: dict[str, Any] | None = None,
    ats_location: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One `result` entry. `isRemote` is included precisely because it is the
    decoy: it is null on every posting ever measured, so a parser that reads it
    has to fail these tests rather than quietly pass them."""
    return {
        "id": job_id,
        "jobOpeningName": name,
        "departmentId": "18660",
        "departmentLabel": "Sales",
        "employmentStatusLabel": "Full-Time",
        "employmentType": None,
        "location": location if location is not None else {"city": None, "state": None},
        "atsLocation": ats_location if ats_location is not None else dict(BH_NASHVILLE),
        "isRemote": None,
        "locationType": location_type,
    }


def bh_list(*rows: dict[str, Any]) -> dict[str, Any]:
    return {"meta": {"totalCount": len(rows)}, "result": list(rows)}


def bh_not_json(size: int) -> FetchResponse:
    """What a slug with no tenant behind it actually returns.

    A 302 that `httpx` follows, so the provider is handed HTTP 200 and a body of
    HTML. `get_json` reports the parse failure through `error`, and that is the
    only signal there is.
    """
    return FetchResponse(
        status_code=200,
        payload=None,
        etag=None,
        bytes_read=size,
        requests_made=1,
        error="not json: Expecting value: line 1 column 1 (char 0)",
    )


async def test_bamboohr_a_200_full_of_marketing_html_is_not_a_live_board() -> None:
    """The single most important behaviour in this provider.

    A nonexistent slug does not 404. It 302s to BambooHR's own marketing site
    and answers 200 with 43,785 bytes of product copy, and every one of
    `zzznotarealcompany9911`, `test`, `foo` and `xyzzy` produces exactly that.
    A provider that trusted the status code would file a page selling HR
    software as a live job board, for every dead slug in the corpus, forever.
    """
    result = await BambooHRProvider().fetch_board(FakeFetcher(bh_not_json(43785)), "notarealco")

    assert result.status is BoardStatus.MISSING
    assert result.http_status == 200, "the status code was 200 and proved nothing"
    assert result.postings == []
    assert result.usable is False


async def test_bamboohr_an_expired_account_page_is_also_missing() -> None:
    """The second non-board 200. A lapsed tenant redirects to
    `/settings/account/expired.php` and returns 67,122 bytes of a different
    page, so keying off the marketing page's size or content would miss it.
    Only "did it parse into the documented shape" catches both."""
    result = await BambooHRProvider().fetch_board(FakeFetcher(bh_not_json(67122)), "acme")

    assert result.status is BoardStatus.MISSING


async def test_bamboohr_json_in_an_unknown_shape_is_an_error_not_a_missing_board() -> None:
    """MISSING prunes the token; ERROR retries it. Real JSON that this parser
    does not recognise is a vendor change, and pruning the whole corpus on the
    morning BambooHR renames `result` would be unrecoverable."""
    result = await BambooHRProvider().fetch_board(
        FakeFetcher(ok({"meta": {"totalCount": 3}, "openings": []})), BH_TOKEN
    )

    assert result.status is BoardStatus.ERROR
    assert result.usable is False


async def test_bamboohr_an_empty_result_is_a_real_board_with_nothing_open() -> None:
    """`{"meta":{"totalCount":0},"result":[]}`, 37 bytes, is what a live tenant
    between hiring rounds returns -- measured on `hover`. It is a completely
    different answer from the marketing HTML above, and conflating the two would
    prune every seasonal employer from the corpus."""
    result = await BambooHRProvider().fetch_board(FakeFetcher(ok(bh_list())), "hover")

    assert result.status is BoardStatus.EMPTY
    assert result.status is not BoardStatus.MISSING
    assert result.usable is True, "we did see the current list, and it was empty"


async def test_bamboohr_reads_locationtype_and_never_the_isremote_decoy() -> None:
    """`isRemote` is null on all 37 postings measured across ten boards,
    including soundstripe's "Head of Sales (Remote)". A parser that read it
    would mark every BambooHR role in the index as onsite, and the remote filter
    would return nothing from this provider at all.

    "1" is confirmed remote by that title; "2" is confirmed hybrid by anvil's
    "(Forward Deployed)" roles, whose own descriptions say so.
    """
    payload = bh_list(
        bh_row("167", location_type="1"),
        bh_row("45", name="Sales Officer-FIP", location_type="0"),
        bh_row("42", name="Account Manager", location_type="2"),
    )

    result = await BambooHRProvider().fetch_board(FakeFetcher(ok(payload)), BH_TOKEN)

    by_id = {p.external_id: p for p in result.postings}
    assert by_id["167"].remote is True
    assert by_id["167"].workplace_type == "remote"
    assert by_id["45"].remote is False
    assert by_id["45"].workplace_type == "onsite"
    assert by_id["42"].remote is False
    assert by_id["42"].workplace_type == "hybrid"


async def test_bamboohr_reads_whichever_location_object_the_board_filled() -> None:
    """Both objects are always present and either can be entirely null, in both
    directions: soundstripe fills `atsLocation` and nulls `location`, canopy
    does the reverse. Reading only one loses the location on about half the
    boards, which silently drops those postings out of any location search."""
    payload = bh_list(
        bh_row("167"),
        bh_row(
            "42",
            location={"city": "Kingston", "state": None},
            ats_location={"country": None, "state": None, "province": None, "city": None},
        ),
    )

    result = await BambooHRProvider().fetch_board(FakeFetcher(ok(payload)), BH_TOKEN)

    by_id = {p.external_id: p for p in result.postings}
    assert by_id["167"].location == "Nashville, Tennessee, United States"
    assert by_id["42"].location == "Kingston"


async def test_bamboohr_list_rows_do_not_claim_a_posted_date() -> None:
    """The list payload has no date field of any kind. Only the detail call
    carries `datePosted`, so a list-only row is honestly `first_crawl` and
    cannot be presented as the employer's own figure."""
    result = await BambooHRProvider().fetch_board(FakeFetcher(ok(bh_list(bh_row()))), BH_TOKEN)

    posting = result.postings[0]
    assert posting.posted_at is None
    assert posting.posted_at_basis == "first_crawl"
    assert posting.posted_at_estimated is True
    assert posting.jd_hydrated is False, "the list carries no description"


async def test_bamboohr_hydration_supplies_the_employers_own_date_and_body() -> None:
    """What the extra request per posting buys. Verified against
    soundstripe/167: `datePosted: 2026-07-29`, and a description whose HTML is
    mostly nested `<span style=...>` noise that has to come out."""
    provider = BambooHRProvider()
    result = await provider.fetch_board(FakeFetcher(ok(bh_list(bh_row()))), BH_TOKEN)

    detail = FakeFetcher(
        ok(
            {
                "meta": {},
                "result": {
                    "jobOpening": {
                        "jobOpeningShareUrl": "https://soundstripe.bamboohr.com/careers/167",
                        "jobOpeningName": "Head of Sales (Remote)",
                        "datePosted": "2026-07-29",
                        "description": (
                            '<p><span style="font-size: 10pt">Own the full sales '
                            "motion.</span></p><ul><li>Manage a team of 4</li></ul>"
                        ),
                        "compensation": "$28 - $35/DOE and shift",
                        "minimumExperience": "Senior Manager/Supervisor",
                        "locationType": "1",
                    },
                    "formFields": [],
                },
            }
        )
    )
    hydrated = await provider.hydrate(detail, BH_TOKEN, result.postings[0])

    assert hydrated.jd_hydrated is True
    assert hydrated.posted_at_basis == "published"
    assert hydrated.posted_at is not None
    assert (hydrated.posted_at.year, hydrated.posted_at.month) == (2026, 7)
    assert "<span" not in hydrated.jd_clean and "<p>" not in hydrated.jd_clean
    assert "Own the full sales motion." in hydrated.jd_clean
    assert "- Manage a team of 4" in hydrated.jd_clean
    assert hydrated.extra["minimumExperience"] == "Senior Manager/Supervisor"


async def test_bamboohr_a_deleted_posting_keeps_the_row_it_already_had() -> None:
    """A detail 404 (`{"type":"not_found"}`) means that one posting went away
    between the list call and the hydrate call. Emptying the row on that would
    replace a usable title-ranked posting with a blank one."""
    provider = BambooHRProvider()
    result = await provider.fetch_board(FakeFetcher(ok(bh_list(bh_row()))), BH_TOKEN)
    before = result.postings[0]

    after = await provider.hydrate(
        FakeFetcher(status(404, payload={"type": "not_found"})), BH_TOKEN, before
    )

    assert after.title == "Head of Sales (Remote)"
    assert after.jd_hydrated is False
    assert after.posted_at_basis == "first_crawl"


async def test_bamboohr_source_id_is_the_board_token_and_external_id() -> None:
    """Matches the web app's `{token}:{id}`, so a crawled row and a live-fetched
    row for the same posting collide instead of both being stored."""
    result = await BambooHRProvider().fetch_board(FakeFetcher(ok(bh_list(bh_row("167")))), "acme")

    assert result.postings[0].source_id == "acme:167"


async def test_bamboohr_server_errors_are_not_usable() -> None:
    result = await BambooHRProvider().fetch_board(FakeFetcher(status(500)), BH_TOKEN)

    assert result.status is BoardStatus.ERROR
    assert result.usable is False


@pytest.mark.parametrize(
    "token", ["sound stripe", "sound.stripe", "evil.com/x", "../etc", "", "a" * 64]
)
async def test_bamboohr_rejects_a_token_that_cannot_address_a_board(token: str) -> None:
    """The token is interpolated into a hostname, not a path. `*.bamboohr.com`
    resolves for names that are not tenants, so a malformed token does not fail
    to connect -- it succeeds against something else, which means a request sent
    to a stranger under this crawler's user agent."""
    result = await BambooHRProvider().fetch_board(FakeFetcher(), token)

    assert result.status is BoardStatus.MISSING
    assert result.requests_made == 0, "a bad token must not reach the network"


@pytest.mark.parametrize(
    "raw",
    [
        "$9.75/hour",
        "$70,000 - $100,000 per year",
        "$45,000-$50,000 per year",
        "$18-$20 per hour",
        "$40 per hour",
        "$28 - $35/DOE and shift",
    ],
)
async def test_bamboohr_keeps_compensation_raw_and_invents_no_salary(raw: str) -> None:
    """`compensation` is set on about a quarter of postings and it is free text
    an employer typed, with no unit, currency or period field beside it. These
    six shapes all came off real boards in one small sample, and the last has
    prose inside the range.

    A regex over that would fill `salary_min`/`salary_max` confidently and
    wrongly, and a wrong salary is worse than an absent one: nothing downstream
    can tell that an hourly figure got stored as an annual one. So the string is
    preserved for a future tested parser and the typed fields stay empty.
    """
    provider = BambooHRProvider()
    result = await provider.fetch_board(FakeFetcher(ok(bh_list(bh_row()))), BH_TOKEN)

    detail = FakeFetcher(
        ok(
            {
                "result": {
                    "jobOpening": {
                        "description": "<p>Work here.</p>",
                        "datePosted": "2026-07-29",
                        "compensation": raw,
                    }
                }
            }
        )
    )
    hydrated = await provider.hydrate(detail, BH_TOKEN, result.postings[0])

    assert hydrated.extra["compensation"] == raw, "the employer's own string survives"
    assert hydrated.salary_min is None
    assert hydrated.salary_max is None
    assert hydrated.salary_currency is None
