"""Importing a link-only row fetches the JD from the board that owns it.

The gap this closes, from live QA: a SimplifyJobs card shows "score on import"
because that source ships a title and a link and no description, and importing
it created an application flagged "imported without its description" anyway.
The import path did try to fetch the JD, via Firecrawl -- but those links are
overwhelmingly Greenhouse, and `job-boards.greenhouse.io` serves a JavaScript
shell, so the plain-HTML fallback recovered nothing.

Both boards answer with the posting as JSON, key-free. These tests cover the
URL reading, the markup handling and the "never raise, fall through" contract
the caller depends on; the network itself is stubbed.
"""
from __future__ import annotations

import pytest

from job_os.integrations import ats_jd

GREENHOUSE_URLS = [
    "https://boards.greenhouse.io/verkada/jobs/5211595007",
    "https://job-boards.greenhouse.io/verkada/jobs/5211595007?gh_src=simplify",
    "https://boards.eu.greenhouse.io/verkada/jobs/5211595007",
]
LEVER_URL = "https://jobs.lever.co/matific/6c2c5f3a-1e2b-4d55-9a11-8f0f2b3c4d5e"


def test_a_greenhouse_posting_is_recognised_whichever_host_linked_it():
    for url in GREENHOUSE_URLS:
        assert ats_jd.parse_posting_url(url) == ("greenhouse", "verkada", "5211595007")


def test_a_lever_posting_is_recognised_with_or_without_an_apply_suffix():
    expected = ("lever", "matific", "6c2c5f3a-1e2b-4d55-9a11-8f0f2b3c4d5e")
    assert ats_jd.parse_posting_url(LEVER_URL) == expected
    assert ats_jd.parse_posting_url(f"{LEVER_URL}/apply") == expected


def test_a_board_we_cannot_read_is_left_to_firecrawl():
    for url in [
        "https://careers.cotiviti.com/job/12345",
        "https://jobs.ashbyhq.com/openai/8a7b6c5d-4e3f-2a1b-9c8d-7e6f5a4b3c2d",
        "http://boards.greenhouse.io/verkada/jobs/5211595007",  # not https
        "",
        "not a url",
    ]:
        assert ats_jd.parse_posting_url(url) is None


def test_greenhouse_entity_encoded_html_becomes_readable_text():
    encoded = (
        "&lt;h2&gt;Overview&lt;/h2&gt;"
        "&lt;p&gt;Build things with Python &amp;amp; Go.&lt;/p&gt;"
    )
    assert ats_jd.html_to_text(encoded) == "Overview\nBuild things with Python & Go."


def test_list_items_survive_as_list_items():
    html = "<ul><li>Python</li><li>Kubernetes</li></ul>"
    assert ats_jd.html_to_text(html) == "- Python\n- Kubernetes"


def test_script_and_style_never_reach_the_description():
    html = "<style>.a{color:red}</style><p>Real body.</p><script>alert(1)</script>"
    assert ats_jd.html_to_text(html) == "Real body."


@pytest.mark.asyncio
async def test_a_greenhouse_posting_returns_the_boards_own_text(monkeypatch):
    seen: list[str] = []

    async def fake_get(url: str):
        seen.append(url)
        return {"content": "&lt;p&gt;We are hiring a Software Engineering Intern.&lt;/p&gt;"}

    monkeypatch.setattr(ats_jd, "_get_json", fake_get)
    text = await ats_jd.fetch_description(GREENHOUSE_URLS[1])
    assert text == "We are hiring a Software Engineering Intern."
    assert seen == ["https://boards-api.greenhouse.io/v1/boards/verkada/jobs/5211595007"]


@pytest.mark.asyncio
async def test_lever_returns_whichever_of_its_two_bodies_is_fuller(monkeypatch):
    async def fake_get(_url: str):
        return {
            "descriptionPlain": "Short teaser.",
            "description": "<p>The full posting, with requirements and everything else.</p>",
        }

    monkeypatch.setattr(ats_jd, "_get_json", fake_get)
    text = await ats_jd.fetch_description(LEVER_URL)
    assert text == "The full posting, with requirements and everything else."


@pytest.mark.asyncio
async def test_a_board_that_does_not_answer_returns_none_rather_than_raising(monkeypatch):
    async def boom(_url: str):
        raise RuntimeError("HTTP 503")

    monkeypatch.setattr(ats_jd, "_get_json", boom)
    assert await ats_jd.fetch_description(GREENHOUSE_URLS[0]) is None


@pytest.mark.asyncio
async def test_an_empty_posting_reads_as_nothing_found(monkeypatch):
    async def empty(_url: str):
        return {"content": ""}

    monkeypatch.setattr(ats_jd, "_get_json", empty)
    assert await ats_jd.fetch_description(GREENHOUSE_URLS[0]) is None


@pytest.mark.asyncio
async def test_an_unreadable_url_never_touches_the_network(monkeypatch):
    async def fail(_url: str):
        raise AssertionError("should not have been called")

    monkeypatch.setattr(ats_jd, "_get_json", fail)
    assert await ats_jd.fetch_description("https://careers.jd.com/en/job/1") is None
