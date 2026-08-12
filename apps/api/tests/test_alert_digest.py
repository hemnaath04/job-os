"""Digest composition: what gets dropped, what never gets sent, and how it reads.

The three promises under test are the ones a job alert product is judged on:

1. A job mailed once is never mailed again, including when it comes back under a
   new id or through a second source.
2. An empty digest is not sent at all.
3. The rendered mail survives Outlook and says something true about every date.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from job_os.services.alert_digest import (
    CandidateJob,
    build_digest,
    content_key,
    normalize_for_key,
    render_html,
    render_text,
    salary_note,
    source_key,
    to_email_message,
)

NOW = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
SUBSCRIPTION_ID = UUID("3f2504e0-4f89-11d3-9a0c-0305e82c3301")
USER_ID = UUID("8a1b09c0-4f89-11d3-9a0c-0305e82c3302")

UNSUB = "https://api.example.com/api/v1/alerts/unsubscribe?token=abc"
UNSUB_ALL = "https://api.example.com/api/v1/alerts/unsubscribe?token=xyz"
POSTAL = "job.os, 1 Example Street, Boston MA 02115, USA"


def candidate(**overrides: object) -> CandidateJob:
    base: dict[str, object] = {
        "source": "greenhouse",
        "source_id": "job-1",
        "source_url": "https://boards.example.com/job-1",
        "title": "Backend Engineer",
        "company_name": "Acme",
        "location": "Boston, MA",
        "posted_at": NOW - timedelta(hours=4),
    }
    base.update(overrides)
    return CandidateJob(**base)  # type: ignore[arg-type]


def make_digest(candidates, **overrides):
    kwargs = {
        "subscription_id": SUBSCRIPTION_ID,
        "user_id": USER_ID,
        "recipient": "person@example.com",
        "search_name": "Backend roles in Boston",
        "cadence": "daily",
        "candidates": candidates,
        "already_sent_source_keys": set(),
        "already_sent_content_keys": set(),
        "unsubscribe_url": UNSUB,
        "unsubscribe_all_url": UNSUB_ALL,
        "postal_address": POSTAL,
        "now": NOW,
    }
    kwargs.update(overrides)
    return build_digest(**kwargs)  # type: ignore[arg-type]


# ---- dedupe -----------------------------------------------------------------


def test_a_job_already_in_the_sent_log_is_not_mailed_again() -> None:
    job = candidate()

    digest = make_digest([job], already_sent_source_keys={job.source_key})

    assert digest is None


def test_a_repost_under_a_new_source_id_is_caught_by_the_content_key() -> None:
    """The same role, relisted with a new id, must not arrive twice.

    This is the dedupe that matters. Boards hand out a fresh id on every repost,
    so a source_key check alone would let the identical job through every time a
    recruiter republished it.
    """
    original = candidate(source_id="job-1")
    reposted = candidate(source_id="job-99999", source_url="https://boards.example.com/new")

    assert original.source_key != reposted.source_key
    assert original.content_key == reposted.content_key

    digest = make_digest([reposted], already_sent_content_keys={original.content_key})

    assert digest is None


def test_the_same_role_carried_by_two_sources_is_sent_once() -> None:
    from_greenhouse = candidate(source="greenhouse", source_id="g-1")
    from_lever = candidate(source="lever", source_id="l-7")

    digest = make_digest([from_greenhouse, from_lever])

    assert digest is not None
    assert len(digest.jobs) == 1
    assert digest.deduped_count == 1


def test_the_same_listing_twice_in_one_batch_is_sent_once() -> None:
    job = candidate()

    digest = make_digest([job, job])

    assert digest is not None
    assert len(digest.jobs) == 1
    assert digest.deduped_count == 1


def test_dedupe_survives_cosmetic_differences_in_company_and_location() -> None:
    """Punctuation, case and accents must not defeat the content key."""
    first = candidate(source_id="a", company_name="Acme, Inc.", location="Boston, MA")
    second = candidate(source_id="b", company_name="ACME Inc", location="boston ma")

    assert first.content_key == second.content_key

    digest = make_digest([first, second])

    assert digest is not None
    assert len(digest.jobs) == 1


def test_a_candidate_with_no_stable_identity_is_dropped() -> None:
    """No id means no way to promise we will not send it again."""
    digest = make_digest(
        [candidate(source_id=""), candidate(source_id="x", title=""), candidate()]
    )

    assert digest is not None
    assert len(digest.jobs) == 1
    assert digest.deduped_count == 2


def test_deduping_counts_every_drop_so_the_run_report_is_honest() -> None:
    seen = candidate(source_id="old")
    digest = make_digest(
        [seen, candidate(source_id="new-1", title="Staff Engineer")],
        already_sent_source_keys={seen.source_key},
    )

    assert digest is not None
    assert len(digest.jobs) == 1
    assert digest.deduped_count == 1


def test_the_keys_are_built_the_way_the_sent_log_stores_them() -> None:
    """The email and the ledger have to agree on what identifies a job."""
    job = candidate()

    assert job.source_key == source_key("greenhouse", "job-1")
    assert job.content_key == content_key(
        company_name="Acme", title="Backend Engineer", location="Boston, MA"
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Sr. Engineer (Remote)", "sr engineer remote"),
        ("  Séñior   Engineer  ", "senior engineer"),
        ("ACME, Inc.", "acme inc"),
        (None, ""),
        ("", ""),
    ],
)
def test_key_normalisation_folds_only_the_cosmetic_parts(raw, expected) -> None:
    assert normalize_for_key(raw) == expected


# ---- empty digest suppression -----------------------------------------------


def test_no_candidates_at_all_produces_no_digest() -> None:
    assert make_digest([]) is None


def test_everything_already_sent_produces_no_digest() -> None:
    """An "0 new roles" email is the fastest way to get marked as spam.

    Returning None rather than an empty Digest means the caller cannot forget to
    check: there is no object to send.
    """
    jobs = [candidate(source_id="a"), candidate(source_id="b", title="Platform Engineer")]

    digest = make_digest(jobs, already_sent_content_keys={j.content_key for j in jobs})

    assert digest is None


def test_one_surviving_job_is_enough_to_produce_a_digest() -> None:
    digest = make_digest([candidate()])

    assert digest is not None
    assert len(digest.jobs) == 1
    assert digest.subject == "1 new role for Backend roles in Boston"


def test_the_subject_counts_only_what_is_in_the_email() -> None:
    jobs = [candidate(source_id=f"j{i}", title=f"Engineer {i}") for i in range(5)]

    digest = make_digest(jobs, max_jobs=2)

    assert digest is not None
    assert digest.subject == "2 new roles for Backend roles in Boston"
    assert digest.overflow_count == 3


# ---- ordering and freshness -------------------------------------------------


def test_rows_are_ordered_by_the_age_the_label_actually_claims() -> None:
    """Order has to agree with the words, including for a repost.

    A repost whose board date says "1 hour" but whose real age is three weeks
    must sort as three weeks old, or the email contradicts itself.
    """
    fresh = candidate(source_id="fresh", title="Fresh Role", posted_at=NOW - timedelta(hours=2))
    repost = candidate(
        source_id="repost",
        title="Old Role",
        posted_at=NOW - timedelta(hours=1),
        first_seen_at=NOW - timedelta(days=30),
    )
    undated = candidate(source_id="undated", title="Undated Role", posted_at=None)

    digest = make_digest([repost, undated, fresh])

    assert digest is not None
    assert [job.title for job in digest.jobs] == ["Fresh Role", "Old Role", "Undated Role"]
    assert digest.repost_count == 1


def test_a_known_first_sighting_turns_a_fresh_looking_date_into_a_labelled_repost() -> None:
    job = candidate(posted_at=NOW - timedelta(hours=1))

    digest = make_digest([job], known_first_seen={job.content_key: NOW - timedelta(days=40)})

    assert digest is not None
    assert digest.jobs[0].freshness.is_repost is True
    assert digest.repost_count == 1


# ---- salary -----------------------------------------------------------------


def test_structured_salary_fields_are_preferred_and_not_labelled_as_parsed() -> None:
    note = salary_note(
        candidate(salary_min=150000, salary_max=180000, salary_currency="usd")
    )

    assert note is not None
    assert note.text == "USD 150,000 to 180,000"
    assert note.from_posting_text is False


def test_a_salary_read_out_of_the_body_says_so() -> None:
    note = salary_note(candidate(description="We pay $150,000 to $180,000 depending on level."))

    assert note is not None
    assert note.text == "USD 150,000 to 180,000"
    assert note.from_posting_text is True


@pytest.mark.parametrize(
    "description",
    [
        "",
        "Competitive salary and equity.",
        "You will own 5000 lines of legacy code.",  # a number, not a salary
        "Pay is $12 per hour.",  # below the floor, and hourly
    ],
)
def test_no_salary_is_shown_rather_than_a_wrong_one(description: str) -> None:
    assert salary_note(candidate(description=description)) is None


# ---- rendering --------------------------------------------------------------


@pytest.fixture
def fixture_digest():
    """One digest covering every branch the renderer has.

    A fresh role with structured pay, a repost, and a role with no date and a
    salary that had to be read out of the body.
    """
    jobs = [
        candidate(
            source_id="fresh-1",
            title="Senior Backend Engineer",
            company_name="Acme",
            location="Boston, MA",
            posted_at=NOW - timedelta(hours=3),
            salary_min=160000,
            salary_max=195000,
            salary_currency="USD",
            source_label="Greenhouse",
        ),
        candidate(
            source="lever",
            source_id="repost-1",
            source_url="https://jobs.example.com/repost-1",
            title="Platform Engineer",
            company_name="Globex",
            location="Remote, US",
            posted_at=NOW - timedelta(hours=1),
            first_seen_at=NOW - timedelta(days=28),
            source_label="Lever",
        ),
        candidate(
            source="ashby",
            source_id="undated-1",
            source_url="https://jobs.example.com/undated-1",
            title="Infrastructure Engineer",
            company_name=None,
            location=None,
            posted_at=None,
            first_seen_at=NOW - timedelta(days=2),
            description="The range for this role is $140,000 to $170,000.",
            source_label="Ashby",
        ),
    ]
    digest = make_digest(jobs)
    assert digest is not None
    return digest


def test_the_text_part_carries_every_job_with_its_link_and_freshness(fixture_digest) -> None:
    text = render_text(fixture_digest)

    for job in fixture_digest.jobs:
        assert job.title in text
        assert job.url in text
        assert job.freshness.headline in text
    assert "Senior Backend Engineer" in text
    assert "USD 160,000 to 195,000" in text
    assert "(read from the posting text)" in text


def test_the_text_part_states_the_repost_plainly(fixture_digest) -> None:
    text = render_text(fixture_digest)

    assert "1 listing below looks like a repost." in text
    assert "repost date, not a new role" in text
    assert "First seen by us about 4 weeks ago" in text


def test_the_repost_banner_agrees_with_itself_in_both_parts() -> None:
    """One repost is "1 listing looks like a repost", not "1 listing look like"."""
    old = candidate(
        source_id="r1",
        title="Old Role",
        posted_at=NOW - timedelta(hours=1),
        first_seen_at=NOW - timedelta(days=30),
    )
    older = candidate(
        source_id="r2",
        title="Older Role",
        posted_at=NOW - timedelta(hours=2),
        first_seen_at=NOW - timedelta(days=60),
    )

    one = make_digest([old])
    two = make_digest([old, older])
    assert one is not None and two is not None

    for render in (render_text, render_html):
        assert "1 listing below looks like a repost." in render(one)
        assert "2 listings below look like reposts." in render(two)


def test_a_missing_company_or_location_is_said_out_loud_not_left_blank(
    fixture_digest,
) -> None:
    text = render_text(fixture_digest)

    assert "Company not named" in text
    assert "Location not given" in text


def test_both_parts_carry_both_unsubscribe_links_and_the_postal_address(
    fixture_digest,
) -> None:
    """CAN-SPAM: a working opt-out and a valid physical postal address in every
    commercial message (15 U.S.C. 7704(a)(3) and (a)(5)).
    """
    text = render_text(fixture_digest)
    html = render_html(fixture_digest)

    for part in (text, html):
        assert UNSUB in part
        assert UNSUB_ALL in part
        assert POSTAL in part
    assert "needs no sign in" in text


def test_the_html_is_built_from_tables_and_inline_styles(fixture_digest) -> None:
    html = render_html(fixture_digest)

    assert html.startswith("<!DOCTYPE html>")
    assert '<table role="presentation"' in html
    assert 'cellpadding="0"' in html
    assert "style=" in html
    # Outlook renders through Word. None of these survive it.
    for modern in ("display:flex", "display:grid", "var(--", "rem;", "@media", "<style"):
        assert modern not in html


def test_the_html_escapes_anything_that_came_from_a_job_board() -> None:
    """Titles and companies are third-party strings arriving over the network."""
    hostile = candidate(
        title='Engineer <script>alert("x")</script>',
        company_name="Acme & Sons <b>",
        source_url="https://example.com/?a=1&b=2",
    )
    digest = make_digest([hostile])
    assert digest is not None

    html = render_html(digest)

    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "Acme &amp; Sons" in html
    assert "a=1&amp;b=2" in html


def test_the_html_and_the_text_agree_on_which_jobs_are_in_the_email(
    fixture_digest,
) -> None:
    text = render_text(fixture_digest)
    html = render_html(fixture_digest)

    for job in fixture_digest.jobs:
        assert job.url in text
        assert job.url in html


def test_the_overflow_count_is_stated_rather_than_silently_truncating() -> None:
    jobs = [candidate(source_id=f"j{i}", title=f"Engineer {i}") for i in range(8)]
    digest = make_digest(jobs, max_jobs=3)
    assert digest is not None

    text = render_text(digest)
    html = render_html(digest)

    assert len(digest.jobs) == 3
    assert "5 more matched" in text
    assert "5 more matched" in html


def test_no_rendered_copy_contains_an_em_dash(fixture_digest) -> None:
    text = render_text(fixture_digest)
    html = render_html(fixture_digest)

    assert "—" not in text
    assert "—" not in html
    assert "&mdash;" not in html


def test_the_html_body_is_fixed_at_a_width_every_client_agrees_on(
    fixture_digest,
) -> None:
    html = render_html(fixture_digest)

    assert 'width="600"' in html
    assert "max-width:600px" in html


def test_the_message_carries_the_rfc_8058_one_click_headers(fixture_digest) -> None:
    """Gmail and Yahoo surface a native unsubscribe control when these are set,
    and RFC 8058 requires the POST take effect with no confirmation step.
    """
    message = to_email_message(fixture_digest)

    assert message.headers["List-Unsubscribe"] == f"<{UNSUB}>"
    assert message.headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert message.to == "person@example.com"
    assert message.subject == fixture_digest.subject


def test_the_message_always_has_both_a_text_and_an_html_part(fixture_digest) -> None:
    message = to_email_message(fixture_digest)

    assert message.text.strip()
    assert message.html.strip()
    assert message.text != message.html


def test_the_preheader_is_hidden_in_the_body_but_present_for_the_inbox_list(
    fixture_digest,
) -> None:
    html = render_html(fixture_digest)

    assert "Dates checked against our own records" in html
    assert "display:none" in html


def test_every_html_tag_that_opens_a_table_also_closes_it(fixture_digest) -> None:
    """A stray unclosed table is how a mail layout collapses in Outlook."""
    html = render_html(fixture_digest)

    for tag in ("table", "tr", "td", "html", "body"):
        opened = len(re.findall(rf"<{tag}[\s>]", html))
        closed = len(re.findall(rf"</{tag}>", html))
        assert opened == closed, f"{tag}: {opened} opened, {closed} closed"
