"""Reading an Eightfold posting from its API rather than from its shell.

Eightfold career sites are client-rendered, so fetching the HTML returns the
app's bootstrap payload and not the job. Production held two of them:
Millennium's `campusjobs.mlp.com` at 15KB of theme colours, navigation markup
and CSS, and Microsoft's `apply.careers.microsoft.com` at roughly 498,000
characters of the same. Both parsed to zero requirements, correctly, because
there were none in the text.

The payloads below are trimmed from real responses captured 2026-08-29.
"""
from __future__ import annotations

import json

import httpx
import pytest

from job_os.integrations import eightfold, firecrawl

MILLENNIUM_URL = "https://campusjobs.mlp.com/careers/job/755957778848"
MICROSOFT_URL = "https://apply.careers.microsoft.com/careers/job/1970393556962891"

REAL_PAYLOAD = {
    "id": 755957778848,
    "name": "2027 Applied AI Engineer Intern, Miami",
    "location": "Miami, Florida, United States of America",
    "department": "Information Technology",
    "job_description": (
        "<p><b>About Millennium</b></p><p>Millennium is a global investment "
        "firm.</p><ul><li>Python and SQL</li><li>Machine learning</li></ul>"
        "<p>Pursuing a degree in Computer Science.</p>"
    ),
}


class _FakeResponse:
    def __init__(self, status_code: int, payload: object) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> object:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _FakeClient:
    """Stands in for `httpx.AsyncClient` inside `eightfold.fetch_job`."""

    def __init__(self, response: object) -> None:
        self._response = response
        self.urls: list[str] = []

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False

    async def get(self, url: str, *, headers: dict[str, str]) -> _FakeResponse:
        self.urls.append(url)
        if isinstance(self._response, Exception):
            raise self._response
        assert isinstance(self._response, _FakeResponse)
        return self._response


# ---------------------------------------------------------------------------
# Recognising the URL. No host allowlist: Eightfold has many tenants on their
# own vanity domains, and listing them would mean this only ever worked for the
# two that were reported.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (MILLENNIUM_URL, "https://campusjobs.mlp.com/api/apply/v2/jobs/755957778848"),
        (
            MICROSOFT_URL,
            "https://apply.careers.microsoft.com/api/apply/v2/jobs/1970393556962891",
        ),
        ("campusjobs.mlp.com/careers/job/755957778848", None),
    ],
)
def test_the_api_url_is_derived_from_the_posting_url(url: str, expected: str | None) -> None:
    got = eightfold.job_api_url(url)
    if expected is None:
        # A scheme-less string still resolves, because a stored source_url is
        # not reliably absolute.
        assert got == "https://campusjobs.mlp.com/api/apply/v2/jobs/755957778848"
    else:
        assert got == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://job-boards.greenhouse.io/glossgenius/jobs/7978666003",
        "https://jobs.lever.co/matchgroup/abc-123",
        "https://example.com/careers/job/engineer",  # not numeric
        "https://example.com/careers/job/42",  # too short to be an Eightfold id
        "",
    ],
)
def test_everything_else_is_left_to_the_ordinary_fetch(url: str) -> None:
    assert eightfold.job_api_url(url) is None


# ---------------------------------------------------------------------------
# Reading the job.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_description_comes_back_as_readable_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient(_FakeResponse(200, REAL_PAYLOAD))
    monkeypatch.setattr(eightfold.httpx, "AsyncClient", lambda **_kw: client)

    job = await eightfold.fetch_job(MILLENNIUM_URL)

    assert job is not None
    assert client.urls == ["https://campusjobs.mlp.com/api/apply/v2/jobs/755957778848"]
    assert job.title == "2027 Applied AI Engineer Intern, Miami"
    # The markup is gone and the words survive.
    assert "<p>" not in job.text
    assert "About Millennium" in job.text
    assert "Python and SQL" in job.text
    # List items are separated rather than run together, which is what a
    # requirement extractor reads as one unusable line.
    assert "Python and SQL\n- Machine learning" in job.text


@pytest.mark.asyncio
async def test_the_title_and_location_lead_the_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The role's name is not always inside the description body.

    A parser that never sees it has to infer the role from the responsibilities,
    which is how a posting ends up stored as "Untitled".
    """
    client = _FakeClient(_FakeResponse(200, REAL_PAYLOAD))
    monkeypatch.setattr(eightfold.httpx, "AsyncClient", lambda **_kw: client)

    job = await eightfold.fetch_job(MILLENNIUM_URL)

    assert job is not None
    assert job.text.startswith("2027 Applied AI Engineer Intern, Miami")
    assert "Miami, Florida" in job.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        _FakeResponse(404, {}),
        _FakeResponse(200, {"id": 1}),  # 200, no description
        _FakeResponse(200, {"job_description": "   "}),  # present but empty
        _FakeResponse(200, ["not", "an", "object"]),
        _FakeResponse(200, json.JSONDecodeError("bad", "", 0)),
        httpx.ConnectError("eightfold is down"),
    ],
)
async def test_anything_unusable_falls_through_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch, response: object
) -> None:
    """This runs in front of the ordinary fetch, so it must never be the reason
    a posting fails. A page it cannot read has to stay fetchable the usual way.
    """
    monkeypatch.setattr(
        eightfold.httpx, "AsyncClient", lambda **_kw: _FakeClient(response)
    )

    assert await eightfold.fetch_job(MILLENNIUM_URL) is None


# ---------------------------------------------------------------------------
# The dispatch in front of Firecrawl.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_eightfold_posting_never_reaches_firecrawl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not an optimisation. For these hosts the HTML path cannot work at all:
    it returns the app shell, which is what was being stored as the JD."""

    async def _never(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the HTML path was used for an Eightfold posting")

    monkeypatch.setattr(firecrawl, "_fetch_firecrawl_retrying", _never)
    monkeypatch.setattr(firecrawl, "_fetch_plain", _never)
    monkeypatch.setattr(
        eightfold.httpx, "AsyncClient", lambda **_kw: _FakeClient(_FakeResponse(200, REAL_PAYLOAD))
    )

    page = await firecrawl.fetch_url_markdown(MILLENNIUM_URL)

    assert "About Millennium" in page.markdown
    assert page.title == "2027 Applied AI Engineer Intern, Miami"
    assert page.company_hint == "Mlp"


@pytest.mark.asyncio
async def test_an_ordinary_posting_still_goes_to_firecrawl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The adapter is ahead of the normal path, so it has to stay out of its way."""

    class _Settings:
        firecrawl_api_key = "test-key"

    expected = firecrawl.FetchedPage(
        url="https://job-boards.greenhouse.io/acme/jobs/1",
        markdown="a real greenhouse page",
        raw="<html></html>",
        title="Engineer",
        company_hint="Acme",
    )

    async def _firecrawl(url: str, api_key: str) -> firecrawl.FetchedPage:
        return expected

    monkeypatch.setattr(firecrawl, "get_settings", lambda: _Settings())
    monkeypatch.setattr(firecrawl, "_fetch_firecrawl_retrying", _firecrawl)

    page = await firecrawl.fetch_url_markdown("https://job-boards.greenhouse.io/acme/jobs/1")

    assert page is expected
