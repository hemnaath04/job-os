"""One posting, one row.

The dedup in `create_from_url` matched the raw `source_url` string and only
looked inside `source == "url"`. Reported as: the same job could be saved
twice, through the web app and through MCP alike, since both go through that
one route.

Two claims are tested, and the second matters more than the first.

The first is that links which differ only in how somebody arrived at them are
recognised as one posting. The second is that links to genuinely different
postings are still different, because over-normalising is the far worse
failure: it would attach one job's applications, tailored resumes and
interview notes to another job's description, and nothing in the UI would
show it had happened.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://job_os:job_os@localhost/job_os"
)

from job_os.services.job_identity import (  # noqa: E402
    canonical_url,
    find_job_by_url,
    same_posting,
)

GREENHOUSE = "https://boards.greenhouse.io/acme/jobs/4512"


@pytest.mark.parametrize(
    "variant",
    [
        GREENHOUSE,
        # Greenhouse stamps its own referrer on every link copied off a board.
        f"{GREENHOUSE}?gh_src=a1b2c3d4",
        # LinkedIn and the aggregators add campaign tags.
        f"{GREENHOUSE}?utm_source=linkedin&utm_medium=social&utm_campaign=q3",
        # Order of the parameters is not meaning.
        f"{GREENHOUSE}?utm_medium=social&utm_source=linkedin&utm_campaign=q3",
        # A trailing slash, from a browser that added one.
        f"{GREENHOUSE}/",
        # `www.`, and a host typed in mixed case.
        "https://WWW.boards.Greenhouse.io/acme/jobs/4512",
        # The scheme, which a redirect changes without changing the job.
        "http://boards.greenhouse.io/acme/jobs/4512",
        # A doubled slash from a hand-built link.
        "https://boards.greenhouse.io/acme//jobs/4512",
    ],
)
def test_one_posting_however_you_arrived_at_it(variant: str) -> None:
    assert same_posting(GREENHOUSE, variant), variant


@pytest.mark.parametrize(
    ("left", "right"),
    [
        # Different postings on the same board.
        (GREENHOUSE, "https://boards.greenhouse.io/acme/jobs/4513"),
        # Same id, different employer.
        (GREENHOUSE, "https://boards.greenhouse.io/globex/jobs/4512"),
        # The single-page-app case. Oracle Cloud and Taleo put the posting id
        # after the `#`, so dropping the fragment would collapse an entire
        # careers site into one row. This is the reason the fragment is kept.
        (
            "https://x.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX/requisitions#/job/1234",
            "https://x.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX/requisitions#/job/9999",
        ),
        # A query parameter that IS the identity. `currentJobId` is not on the
        # tracking list precisely because of this.
        (
            "https://linkedin.com/jobs/search?currentJobId=111",
            "https://linkedin.com/jobs/search?currentJobId=222",
        ),
        # An unrecognised parameter is kept, so two links that differ only by
        # one are two jobs. The cost of that is a duplicate row, which is the
        # side to err on.
        (
            "https://careers.example.com/apply?req=A-1",
            "https://careers.example.com/apply?req=A-2",
        ),
    ],
)
def test_two_postings_stay_two_postings(left: str, right: str) -> None:
    assert not same_posting(left, right), f"{left} and {right} were merged"


@pytest.mark.parametrize("value", [None, "", "   "])
def test_nothing_matches_nothing(value: str | None) -> None:
    """An absent URL is not an identity.

    `same_posting(None, None)` returning True would make every job with no
    link a duplicate of every other one.
    """
    assert canonical_url(value) is None
    assert not same_posting(value, value)


@pytest.mark.asyncio
async def test_a_pasted_link_finds_the_job_discovery_already_imported(db_session) -> None:
    """The scoping half of the bug.

    A job pulled in by discovery is `source == "greenhouse"`. The old dedup
    query was filtered to `source == "url"` and so could not see it, and
    pasting the link made a second card for a job the board was already
    showing. Nothing about the row changed; only which rows the query was
    allowed to look at.
    """
    from job_os.db.models import Job

    job = Job(
        title="Backend Engineer",
        jd_raw="",
        jd_clean="",
        source="greenhouse",
        source_id="gh-4512",
        source_url=GREENHOUSE,
    )
    db_session.add(job)
    await db_session.flush()

    found = await find_job_by_url(db_session, f"{GREENHOUSE}?gh_src=abc")
    assert found is not None and found.id == job.id


@pytest.mark.asyncio
async def test_the_key_is_derived_and_cannot_be_set_wrong(db_session) -> None:
    """`source_url_key` is written by the model, at every construction site.

    Fourteen places in this app build a Job. Deriving the key on assignment is
    what makes it impossible for one of them to forget, and for the column to
    disagree with the URL it describes.
    """
    from job_os.db.models import Job

    job = Job(
        title="Untitled",
        jd_raw="",
        jd_clean="",
        source="url",
        source_url="https://WWW.example.com/jobs/7/?utm_source=x",
    )
    assert job.source_url_key == "example.com/jobs/7"

    # And it follows the URL when that changes, rather than going stale.
    job.source_url = "https://example.com/jobs/8"
    assert job.source_url_key == "example.com/jobs/8"

    job.source_url = None
    assert job.source_url_key is None


@pytest.mark.asyncio
async def test_the_oldest_row_wins_when_a_key_is_already_duplicated(db_session) -> None:
    """Until the merge script has run, a key can match several rows.

    The oldest is the one the application history, tailored resumes and
    interview notes hang off. Returning a newer row would send someone to a
    card with none of their work on it.
    """
    from datetime import UTC, datetime, timedelta

    from job_os.db.models import Job

    url = "https://boards.greenhouse.io/dupetest/jobs/1"
    # Timestamps set by hand. `created_at` defaults to Postgres `now()`, which
    # is transaction-start time, so two rows inserted in one test transaction
    # would otherwise share a timestamp and the age this asserts on would not
    # exist to be read.
    then = datetime.now(UTC) - timedelta(days=2)
    older = Job(
        title="First", jd_raw="", jd_clean="", source="url",
        source_url=url, created_at=then,
    )
    newer = Job(
        title="Second", jd_raw="", jd_clean="", source="text",
        source_url=f"{url}/", created_at=then + timedelta(days=1),
    )
    db_session.add_all([older, newer])
    await db_session.flush()

    found = await find_job_by_url(db_session, url)
    assert found is not None and found.id == older.id
