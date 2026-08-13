"""The contract every ATS provider implements.

A provider owns one vendor's URL shape, pagination and payload quirks, and hands
back `RawPosting` rows that the upsert can write without knowing which board
they came from.

The `BoardStatus` distinction is the part that earns its keep. A board that
answers 200 with an empty list is LIVE-but-hiring-nothing and worth re-crawling
next week; a board whose token does not exist is MISSING and should be pruned
from the corpus; a board that timed out is an ERROR and must be retried without
touching the postings we already have. Collapsing those three into "no results"
is how a crawler quietly deletes a company's whole board on a bad afternoon.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

# How a posting's date was arrived at. The read path exposes this rather than
# presenting every crawled date as though the employer stated it.
#   published  - the board gave an explicit publish/release date
#   created    - the board gave a creation date for the requisition
#   updated    - we only had a last-modified stamp, so the real posting date is
#                at or before this and the value is an upper bound
#   first_crawl- the board gave no date at all; this is when we first saw it,
#                which says nothing about when it went up
POSTED_AT_BASES = ("published", "created", "updated", "first_crawl")
ESTIMATED_BASES = frozenset({"updated", "first_crawl"})


class BoardStatus(StrEnum):
    LIVE = "live"
    EMPTY = "empty"
    MISSING = "missing"
    NOT_MODIFIED = "not_modified"
    ERROR = "error"


def as_dict(value: object) -> dict[str, Any]:
    """A nested payload field as a dict, or an empty one.

    Every provider reaches into payloads a vendor can change without telling us,
    so a field documented as an object arrives as null, a string or a list often
    enough to matter. Coercing once at the boundary keeps the parsers free of
    `isinstance` noise and means a shape surprise costs a missing field rather
    than an exception that fails the whole board.
    """
    return value if isinstance(value, dict) else {}


def as_list(value: object) -> list[Any]:
    """The list counterpart of `as_dict`."""
    return value if isinstance(value, list) else []


@dataclass(slots=True)
class RawPosting:
    """One posting, normalized, ready to upsert."""

    source: str
    board_token: str
    external_id: str
    title: str
    company_name: str
    source_url: str
    jd_clean: str

    company_domain: str | None = None
    location: str | None = None
    country_code: str | None = None
    remote: bool = False
    anywhere: bool = False
    workplace_type: str | None = None
    employment_type: str | None = None
    department: str | None = None
    posted_at: datetime | None = None
    posted_at_basis: str = "first_crawl"
    closes_at: datetime | None = None
    jd_raw: str = ""
    # False when the board's list endpoint carries no body and we have not made
    # the extra per-posting call yet. SmartRecruiters is the provider this is
    # for; the read path can still rank the row on title and metadata.
    jd_hydrated: bool = True
    salary_min: int | None = None
    salary_max: int | None = None
    salary_currency: str | None = None
    salary_interval: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def source_id(self) -> str:
        """Stable per-source identity. Matches the web app's `{token}:{id}`."""
        return f"{self.board_token}:{self.external_id}"

    @property
    def posted_at_estimated(self) -> bool:
        return self.posted_at_basis in ESTIMATED_BASES


@dataclass(slots=True)
class BoardResult:
    provider: str
    token: str
    status: BoardStatus
    postings: list[RawPosting] = field(default_factory=list)
    http_status: int | None = None
    etag: str | None = None
    bytes_fetched: int = 0
    requests_made: int = 0
    error: str | None = None

    @property
    def usable(self) -> bool:
        """Whether this crawl learned enough to rewrite the board's rows.

        NOT_MODIFIED and ERROR both mean "we did not see the current list", so
        the caller must not deactivate anything on the strength of them.
        """
        return self.status in (BoardStatus.LIVE, BoardStatus.EMPTY)


class Provider(Protocol):
    name: str
    #: Host the provider talks to, for per-host politeness accounting.
    host: str

    def board_url(self, token: str) -> str:
        """The public URL a human could paste into a browser to check a token."""
        ...

    async def fetch_board(
        self,
        fetcher: object,
        token: str,
        etag: str | None = None,
        expect_bytes: int = 0,
    ) -> BoardResult:
        """Fetch and normalize one board.

        `etag` enables the conditional GET. `expect_bytes` is the size this board
        came in at last time and is only used to attribute a bandwidth saving to
        a 304, so the crawl can report the saving rather than claim it.
        """
        ...
