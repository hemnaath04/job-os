"""SSRF guard on the plain-fetch fallback in integrations/firecrawl.py.

_fetch_plain runs whenever FIRECRAWL_API_KEY is unset, fetching a
user-supplied job posting URL directly from this server with
follow_redirects=True. _assert_fetchable_url is the guard that keeps that URL
from pointing at loopback, private, link-local or cloud-metadata addresses.
"""
from __future__ import annotations

import pytest

from job_os.integrations.firecrawl import _assert_fetchable_url


@pytest.mark.parametrize(
    "url",
    [
        "https://boards.greenhouse.io/anthropic/jobs/123",
        "https://example.com/jobs/123",
    ],
)
def test_allows_ordinary_public_https_url(url: str) -> None:
    _assert_fetchable_url(url)  # must not raise


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/jobs/123",  # not https
        "https://localhost/jobs/123",
        "https://127.0.0.1/jobs/123",
        "https://169.254.169.254/latest/meta-data",  # cloud metadata
        "https://metadata.google.internal/computeMetadata/v1",
        "https://10.0.0.5/internal",
        "https://192.168.1.1/internal",
        "https://172.16.0.1/internal",
        "https://[::1]/internal",
        "https://foo.internal/jobs",
        "https://foo.local/jobs",
    ],
)
def test_blocks_unsafe_destination(url: str) -> None:
    with pytest.raises(ValueError):
        _assert_fetchable_url(url)
