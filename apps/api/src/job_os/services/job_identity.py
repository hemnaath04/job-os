"""One posting, one row: what makes two links the same job.

`create_from_url` has always deduplicated, but on the raw string and only
within `source == "url"`. Both halves leak, and a job link in the wild almost
never arrives clean:

    https://boards.greenhouse.io/acme/jobs/4512
    https://boards.greenhouse.io/acme/jobs/4512?gh_src=a1b2c3
    https://www.greenhouse.io/acme/jobs/4512/
    https://job-boards.greenhouse.io/acme/jobs/4512?utm_source=linkedin

Four rows today. Every one of them is the same posting, and the person who
pasted the second one after seeing it on LinkedIn has no way to know they now
have two cards, two tailoring runs and two application histories for one job.

The scoping leak is the other half. A job pulled in by discovery is
`source == "greenhouse"`; paste its link and the dedup query, filtered to
`source == "url"`, cannot see it. The same posting arrives twice through two
doors.

The bar here is deliberately asymmetric. Merging two DIFFERENT postings is far
worse than keeping a duplicate: it would attach one job's application history
to another job's description. So this only removes things that are known not
to identify a posting, by name, and keeps everything it does not recognise.
Nothing is inferred, guessed, or dropped for looking noisy.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import select
from sqlalchemy.orm import joinedload

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from job_os.db.models.job import Job

# Parameters that identify who sent you, not which job you are looking at.
# Every one is a named campaign, referrer or click id, and each was chosen
# because removing it cannot change which posting the link opens.
#
# Note what is NOT here: `gh_jid`, `jobId`, `currentJobId`, `id`, `req`,
# `pid`, and anything else that could be the posting's identity. When a
# parameter's meaning is not certain, it stays, and the cost of that is a
# duplicate row rather than two jobs merged into one.
_TRACKING_PARAMS = frozenset(
    {
        # Campaign tagging, in its various house styles.
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "utm_id",
        "gh_src",  # Greenhouse's board referrer
        "lever-source",
        "lever-origin",
        "ashby_jid_source",
        "source",
        "src",
        "ref",
        "referer",
        "referrer",
        "referredby",
        # Click ids from the ad networks and the big boards.
        "fbclid",
        "gclid",
        "msclkid",
        "twclid",
        "igshid",
        "li_fat_id",
        "trk",
        "trackingid",
        "traffictype",
        "originaltype",
        "seniority",
        "position",
        "pagenum",
        "refid",
        "eblocid",
        "mcid",
        "cid",
        "campaignid",
    }
)


def canonical_url(raw: str | None) -> str | None:
    """The comparable form of a job link, or None when there is nothing to compare.

    Scheme and host are lowercased, a leading `www.` and a trailing slash go,
    known tracking parameters are dropped and what remains is sorted so that
    parameter order stops mattering.

    The fragment is KEPT. Several ATS front ends are single-page apps that put
    the posting id after the `#` (Oracle Cloud and Taleo both do), so throwing
    it away would collapse every job on such a board into one row. It is
    normalised the same way the path is and otherwise left alone.
    """
    if not raw or not raw.strip():
        return None

    parts = urlsplit(raw.strip())
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if not host:
        # Not a URL we can reason about -- a bare path, or something pasted by
        # hand. Comparing it as-is is still better than not comparing it.
        return raw.strip().lower() or None

    if parts.port and parts.port not in (80, 443):
        host = f"{host}:{parts.port}"

    path = re.sub(r"/{2,}", "/", parts.path)
    path = path.rstrip("/")

    kept = sorted(
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in _TRACKING_PARAMS
    )

    fragment = parts.fragment.rstrip("/")

    # Scheme is deliberately not part of the key: http and https serve the same
    # posting everywhere this app has seen, and a redirect between them is the
    # most common way one link becomes two rows.
    return urlunsplit(("", host, path, urlencode(kept), fragment)).lstrip("/") or host


def same_posting(left: str | None, right: str | None) -> bool:
    """Whether two links point at one job. False when either is unusable."""
    a, b = canonical_url(left), canonical_url(right)
    return a is not None and a == b


async def find_job_by_url(session: AsyncSession, url: str | None) -> Job | None:
    """The job this link already points at, if this app has it.

    Not scoped to a `source`. That scoping is what let one posting arrive
    twice through two doors: a row imported by discovery is
    `source == "greenhouse"`, and a dedup query filtered to `source == "url"`
    could not see it, so pasting the link made a second card for a job the
    board was already showing.

    When several rows share a key -- which is possible until
    `job_os.scripts.merge_duplicate_jobs` has been run over a table that
    predates this -- the oldest wins. It is the one with the application
    history, the tailored resumes and the interview notes hanging off it, and
    sending someone to the newer row would quietly orphan all of that.
    """
    # Imported here, not at module scope: `db.models.job` imports
    # `canonical_url` from this module to derive the key on assignment, so a
    # top-level import back would be a cycle.
    from job_os.db.models.job import Job

    key = canonical_url(url)
    if key is None:
        return None
    result = await session.execute(
        select(Job)
        .options(joinedload(Job.company))
        .where(Job.source_url_key == key)
        # `id` breaks the tie, and it is not decoration: `created_at`
        # defaults to Postgres `now()`, which is transaction-start time, so
        # two rows written in one transaction carry the SAME timestamp and
        # ordering by it alone returns whichever the planner reached first.
        .order_by(Job.created_at.asc(), Job.id.asc())
    )
    return result.unique().scalars().first()
