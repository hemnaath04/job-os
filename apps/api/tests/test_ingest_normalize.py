"""Normalization, with the cases the boards actually produce.

Every fixture here is shaped after a payload observed on the real endpoint, not
invented. The date handling is the part that matters most: a timestamp read with
the wrong unit does not fail loudly, it silently dates a posting to 1970 and every
freshness filter then drops it.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from job_os.ingest import normalize

# ---------------------------------------------------------------------------
# dates
# ---------------------------------------------------------------------------


def test_lever_epoch_milliseconds() -> None:
    """Lever's `createdAt` is milliseconds. Observed value from api.lever.co."""
    parsed = normalize.to_datetime(1711403416463)
    assert parsed is not None
    assert parsed.year == 2024
    assert parsed.month == 3
    assert parsed.tzinfo is not None


def test_milliseconds_are_not_read_as_seconds() -> None:
    """The regression this guards is the whole reason the discriminator exists.

    Reading Lever's milliseconds as seconds puts every posting in 1970, which then
    reads as 55 years old and is dropped by any max-age filter, so the source
    silently contributes nothing.
    """
    parsed = normalize.to_datetime(1711403416463)
    assert parsed is not None
    assert parsed.year != 1970
    assert parsed.year > 2020


def test_epoch_seconds_still_work() -> None:
    parsed = normalize.to_datetime(1711403416)
    assert parsed is not None
    assert parsed.year == 2024


def test_epoch_boundary_picks_the_right_unit() -> None:
    """1e11 is the seconds/milliseconds discriminator.

    Below it, the value is seconds. At or above, it is milliseconds, because
    1e11 seconds would be the year 5138 and no job posting is dated then.
    """
    # 1.7e9 seconds -> 2023.
    below = normalize.to_datetime(1_700_000_000)
    assert below is not None and below.year == 2023
    # 1.7e12 milliseconds -> the same instant, not the year 55879.
    above = normalize.to_datetime(1_700_000_000_000)
    assert above is not None and above.year == 2023
    assert below == above


def test_numeric_strings_are_handled_by_digit_count() -> None:
    assert normalize.to_datetime("1711403416463").year == 2024
    assert normalize.to_datetime("1711403416").year == 2024


def test_iso_with_offset() -> None:
    """Greenhouse sends an offset, observed: 2026-08-06T12:50:10-04:00."""
    parsed = normalize.to_datetime("2026-08-06T12:50:10-04:00")
    assert parsed == datetime(2026, 8, 6, 16, 50, 10, tzinfo=UTC)


def test_ashby_fractional_seconds() -> None:
    """Observed: 2026-04-07T17:12:35.753+00:00."""
    parsed = normalize.to_datetime("2026-04-07T17:12:35.753+00:00")
    assert parsed is not None
    assert parsed.microsecond == 753_000


def test_smartrecruiters_zulu() -> None:
    """Observed: 2026-06-24T10:00:11.853Z."""
    parsed = normalize.to_datetime("2026-06-24T10:00:11.853Z")
    assert parsed is not None
    assert parsed.tzinfo is not None
    assert parsed.hour == 10


def test_naive_timestamp_is_pinned_to_utc() -> None:
    """A stamp with no zone must not shift with the worker's local timezone."""
    parsed = normalize.to_datetime("2026-07-24T10:33:35")
    assert parsed == datetime(2026, 7, 24, 10, 33, 35, tzinfo=UTC)


def test_implausible_dates_are_rejected() -> None:
    """A parse artifact is worse than no date: it looks authoritative."""
    assert normalize.to_datetime(0) is None
    assert normalize.to_datetime("1899-01-01T00:00:00Z") is None
    assert normalize.to_datetime(10**15) is None


@pytest.mark.parametrize("value", [None, "", "   ", "not a date", [], {}, True])
def test_unparseable_values_return_none(value: object) -> None:
    assert normalize.to_datetime(value) is None


# ---------------------------------------------------------------------------
# html
# ---------------------------------------------------------------------------


def test_greenhouse_entity_encoded_html() -> None:
    """Greenhouse `content` is HTML that has been entity-encoded once.

    Observed prefix on boards/vercel:
    `&lt;div class=&quot;content-intro&quot;&gt;&lt;h2&gt;About Vercel:&lt;/h2&gt;`
    """
    raw = (
        "&lt;div class=&quot;content-intro&quot;&gt;&lt;h2&gt;About Vercel:&lt;/h2&gt;"
        "&lt;p&gt;Vercel is the agentic platform.&lt;/p&gt;"
        "&lt;ul&gt;&lt;li&gt;Ship fast&lt;/li&gt;&lt;li&gt;Stay safe&lt;/li&gt;&lt;/ul&gt;"
    )
    text = normalize.html_to_text(raw)
    assert "About Vercel:" in text
    assert "Vercel is the agentic platform." in text
    assert "Ship fast" in text
    assert "<" not in text and "&lt;" not in text
    assert "&quot;" not in text


def test_real_html_is_not_double_decoded() -> None:
    """Lever and Ashby send real HTML. A literal `&lt;` in prose must survive.

    Unconditionally unescaping would corrupt this into a broken tag.
    """
    raw = "<p>Compare with a &lt;div&gt; element and ship it.</p>"
    text = normalize.html_to_text(raw)
    assert "<div>" in text or "&lt;div&gt;" not in text
    assert "Compare with a" in text


def test_script_and_style_are_dropped() -> None:
    raw = "<p>Real text</p><script>alert('x')</script><style>.a{color:red}</style>"
    text = normalize.html_to_text(raw)
    assert "Real text" in text
    assert "alert" not in text
    assert "color:red" not in text


def test_list_items_become_readable_lines() -> None:
    text = normalize.html_to_text("<ul><li>First</li><li>Second</li></ul>")
    assert "- First" in text
    assert "- Second" in text


def test_description_is_bounded() -> None:
    text = normalize.html_to_text("<p>" + ("word " * 20_000) + "</p>")
    assert len(text) <= normalize.MAX_DESCRIPTION_CHARS


def test_empty_html_is_empty_string_not_none() -> None:
    assert normalize.html_to_text(None) == ""
    assert normalize.html_to_text("") == ""


# ---------------------------------------------------------------------------
# location
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("San Francisco, CA", "US"),
        ("Austin, TX (Remote)", "US"),
        ("Toronto, ON", "CA"),
        ("London, United Kingdom", "GB"),
        ("Bengaluru", "IN"),
        ("Remote - US", "US"),
        ("Berlin", "DE"),
        ("Tel Aviv", "IL"),
        # Ashby writes a human country name, not an ISO code.
        ("USA", "US"),
        ("Canada", "CA"),
        ("United States", "US"),
    ],
)
def test_country_inference(label: str, expected: str) -> None:
    assert normalize.infer_country_code(label) == expected


@pytest.mark.parametrize("label", [None, "", "AMER", "EMEA", "Global South"])
def test_unknown_country_returns_none_rather_than_guessing(label: str | None) -> None:
    """Unknown is a different fact from wrong, and the read path treats it so."""
    assert normalize.infer_country_code(label) is None


def test_columbus_does_not_match_us_as_a_substring() -> None:
    """The word-boundary check is what keeps "Columbus" from matching "us"."""
    assert normalize.infer_country_code("Columbus, OH") == "US"  # via the state code
    assert normalize.infer_country_code("Belarus") is None


def test_remote_detection() -> None:
    assert normalize.is_remote("Remote - US") is True
    assert normalize.is_remote("Work from home") is True
    assert normalize.is_remote("San Francisco, CA") is False
    # An explicit provider flag beats the label.
    assert normalize.is_remote("San Francisco, CA", explicit=True) is True
    assert normalize.is_remote("Remote", explicit=False) is False


def test_anywhere_is_narrower_than_remote() -> None:
    """Remote-in-one-country and hire-from-anywhere are different facts."""
    assert normalize.is_anywhere("Remote (Worldwide)") is True
    assert normalize.is_anywhere("Anywhere") is True
    assert normalize.is_anywhere("Remote - US") is False


# ---------------------------------------------------------------------------
# identity keys
# ---------------------------------------------------------------------------


def test_fold_removes_accents_and_case() -> None:
    assert normalize.fold("São Paulo") == normalize.fold("Sao Paulo")
    assert normalize.fold("Senior  Engineer") == "senior engineer"


def test_dedupe_key_prefers_domain_over_name() -> None:
    """One employer spelled three ways is still one employer."""
    left = normalize.dedupe_key("Acme Inc.", "Engineer", "NYC", domain="acme.com")
    right = normalize.dedupe_key("ACME Incorporated", "engineer", "nyc", domain="acme.com")
    assert left == right


def test_dedupe_key_strips_www_and_scheme() -> None:
    a = normalize.dedupe_key("Acme", "Engineer", "NYC", domain="https://www.acme.com/careers")
    b = normalize.dedupe_key("Acme", "Engineer", "NYC", domain="acme.com")
    assert a == b


def test_dedupe_key_separates_genuinely_different_jobs() -> None:
    a = normalize.dedupe_key("Acme", "Software Engineer", "NYC", domain="acme.com")
    b = normalize.dedupe_key("Acme", "Product Designer", "NYC", domain="acme.com")
    assert a != b


def test_content_hash_changes_when_the_body_changes() -> None:
    args = ("Acme", "Engineer", "NYC")
    a = normalize.content_hash(*args, "Original description text.")
    b = normalize.content_hash(*args, "Rewritten description with new scope.")
    assert a != b
    assert len(a) == 64


def test_content_hash_ignores_edits_past_the_window() -> None:
    head = "Meaningful role content. " * 400
    assert len(head) > normalize.HASH_DESCRIPTION_CHARS
    a = normalize.content_hash("Acme", "Engineer", "NYC", head + "Footer v1")
    b = normalize.content_hash("Acme", "Engineer", "NYC", head + "Footer v2 differs")
    assert a == b


def test_content_hash_is_stable_across_runs() -> None:
    """An unstable hash would make every re-crawl look like an edit."""
    args = ("Acme", "Engineer", "NYC", "Body text")
    assert normalize.content_hash(*args) == normalize.content_hash(*args)


def test_similarity_tokens_drop_stopwords() -> None:
    tokens = normalize.tokens_for_similarity(
        "We are looking for a candidate with experience in Kubernetes and Rust"
    )
    assert "kubernetes" in tokens
    assert "rust" in tokens
    for stopword in ("we", "are", "for", "a", "with", "in", "and"):
        assert stopword not in tokens
