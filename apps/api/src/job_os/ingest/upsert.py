"""Idempotent writes into `job_postings`.

The contract, which the tests in `tests/test_ingest_upsert.py` pin:

  * Upserting the same posting twice **preserves `first_seen_at`** and **bumps
    `last_seen_at`**. Those two columns are the honest-freshness feature, and a
    naive `ON CONFLICT DO UPDATE SET first_seen_at = now()` would destroy the
    first one and turn every re-crawl into a fake new posting. That is precisely
    the behaviour competitors were caught doing.
  * A posting whose `content_hash` is unchanged keeps every stored value except
    `last_seen_at` and the run id, and its `updated_at` does not move, so
    `updated_at` keeps meaning "the employer edited this". Postgres still writes
    a new tuple version for any UPDATE, but because no indexed column changes it
    can take the heap-only path and skip GIN and HNSW index maintenance, which at
    a few hundred thousand rows a sweep is most of the cost.
  * A posting that vanishes from its board is **deactivated, not deleted**, and
    only when the board was genuinely re-read (see `deactivate_missing`).
  * A deactivated posting that comes back is reactivated with `repost_count`
    incremented, so a perpetually reposted role is visible as one.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from job_os.ingest import normalize
from job_os.ingest.providers import RawPosting
from job_os.services import appwrite_tables

log = structlog.get_logger(__name__)

#: Rows per Appwrite bulk call. See `appwrite_tables.BATCH_SIZE` -- kept as a
#: separate constant here since this module's own lookup-before-write batching
#: (see `_write_batch`) is what actually bounds call size; `appwrite_tables`
#: re-batches internally too, so this only has to be a sane unit of work, not
#: an exact fit against Appwrite's own cap.
BATCH_SIZE = 25


#: How much of a description reaches the fulltext index. Appwrite's `search_text`
#: is `longtext`, so this is not a column limit -- it is the same 8,000 characters
#: the write path has always indexed, kept as a named constant now that a second
#: caller (`ingest/hydrate.py`) has to produce a byte-identical value.
SEARCH_TEXT_DESCRIPTION_CHARS = 8_000


def search_text_for(
    *, title: str | None, company_name: str | None, location: str | None, jd_clean: str | None
) -> str:
    """The fulltext blob `job_index.search_index` matches against.

    Extracted from `to_row` rather than copied into the hydration pass. The two
    have to agree exactly: hydration rewrites `search_text` for a row the sweep
    wrote, and a second copy of this join that drifted by a field or a slice
    length would leave two rows from the same board matching different queries
    for no reason a reader could see.
    """
    parts = [
        title or "",
        company_name or "",
        location or "",
        (jd_clean or "")[:SEARCH_TEXT_DESCRIPTION_CHARS],
    ]
    return " ".join(part for part in parts if part)


@dataclass(slots=True)
class UpsertStats:
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    reactivated: int = 0
    deactivated: int = 0
    skipped: int = 0

    def merge(self, other: UpsertStats) -> None:
        self.inserted += other.inserted
        self.updated += other.updated
        self.unchanged += other.unchanged
        self.reactivated += other.reactivated
        self.deactivated += other.deactivated
        self.skipped += other.skipped


def to_row(
    posting: RawPosting,
    *,
    run_id: uuid.UUID | None,
    seen_at: datetime,
    company_name: str | None = None,
    company_domain: str | None = None,
) -> dict[str, object]:
    """Flatten a `RawPosting` into an Appwrite-insertable row.

    `company_name` / `company_domain` come from the token's curated entry when
    there is one. Lever and Ashby do not report the employer's name at all, so
    without that the board token is the best available answer, which is why the
    curated list matters for dedupe quality rather than only for display.

    `source_posting_id` is new: a fresh id minted here, not carried over from
    anywhere. The Appwrite migration used this same column to hold the
    original Postgres row's UUID for its own idempotent keying; a brand new
    posting from this ingest path never touched Postgres, so it gets a fresh
    one instead, and it means the same thing going forward -- a stable,
    non-Appwrite-internal identity for this posting.

    `search_text` backs the fulltext index `job_index.search_index` reads
    (see that module's docstring for the tradeoff of one combined index
    versus Postgres's weighted-zone tsvector).

    `posted_at_estimated` was a *generated* column in Postgres, computed from
    `posted_at_basis`. Appwrite has no generated columns, so it is computed
    here, the same way: true when the date came from an update timestamp or
    a first-crawl guess rather than something the board actually published.
    """
    name = company_name or posting.company_name
    domain = company_domain or posting.company_domain
    description = posting.jd_clean

    row = {
        "source_posting_id": str(uuid.uuid4()),
        "source": posting.source,
        "source_id": posting.source_id,
        "board_token": posting.board_token,
        # `external_id` is display/debugging only -- `source_id` (already
        # hashed above the 255-char mark) is what dedupe actually keys on.
        # The scraper falls back to the full posting URL here, which the
        # column's own 255-char cap would otherwise reject outright; the full
        # value already lives in `source_url` (2048 chars), so truncating
        # this one loses nothing load-bearing.
        "external_id": posting.external_id[:255],
        "source_url": posting.source_url,
        "company_name": name,
        "company_domain": domain,
        "title": posting.title,
        "location": posting.location,
        "country_code": posting.country_code,
        "remote": posting.remote,
        "anywhere": posting.anywhere,
        "workplace_type": posting.workplace_type,
        "employment_type": posting.employment_type,
        "department": posting.department,
        "salary_min": posting.salary_min,
        "salary_max": posting.salary_max,
        "salary_currency": posting.salary_currency,
        "salary_interval": posting.salary_interval,
        "jd_raw": posting.jd_raw or None,
        "jd_clean": description,
        "jd_hydrated": posting.jd_hydrated,
        "jd_parsed": json.dumps(posting.extra or {}),
        "content_hash": normalize.content_hash(
            name, posting.title, posting.location, description, domain=domain
        ),
        "dedupe_key": normalize.dedupe_key(
            name, posting.title, posting.location, domain=domain
        ),
        "posted_at": posting.posted_at.isoformat() if posting.posted_at else None,
        "posted_at_basis": posting.posted_at_basis,
        "posted_at_estimated": posting.posted_at_estimated,
        "closes_at": posting.closes_at.isoformat() if posting.closes_at else None,
        "active": True,
        "first_seen_at": seen_at.isoformat(),
        "last_seen_at": seen_at.isoformat(),
        "last_crawl_run_id": str(run_id) if run_id else None,
        "content_updated_at": seen_at.isoformat(),
    }
    row["search_text"] = search_text_for(
        title=posting.title,
        company_name=name,
        location=posting.location,
        jd_clean=description,
    )
    return row


#: Columns a re-crawl is allowed to overwrite. `first_seen_at` is absent by
#: design: it is the one fact a later crawl can never improve on, and every other
#: honest-freshness claim rests on it. `search_text` is derived from several of
#: these, so it moves alongside them.
_MUTABLE_COLUMNS = (
    "source_url",
    "company_name",
    "company_domain",
    "title",
    "location",
    "country_code",
    "remote",
    "anywhere",
    "workplace_type",
    "employment_type",
    "department",
    "salary_min",
    "salary_max",
    "salary_currency",
    "salary_interval",
    "jd_raw",
    "jd_clean",
    "jd_hydrated",
    "jd_parsed",
    "content_hash",
    "dedupe_key",
    "posted_at",
    "posted_at_basis",
    "posted_at_estimated",
    "closes_at",
    "search_text",
)


async def upsert_postings(
    session: AsyncSession,
    postings: list[RawPosting],
    *,
    run_id: uuid.UUID | None = None,
    seen_at: datetime | None = None,
    company_names: dict[tuple[str, str], tuple[str | None, str | None]] | None = None,
) -> UpsertStats:
    """Write postings to Appwrite, preserving history. Returns what changed.

    `session` is accepted and unused, kept so callers did not need a
    signature change; nothing here touches Postgres.
    """
    del session
    stats = UpsertStats()
    if not postings:
        return stats

    now = seen_at or datetime.now(UTC)
    lookup = company_names or {}

    rows: list[dict[str, object]] = []
    seen_ids: set[tuple[str, str]] = set()
    for posting in postings:
        identity = (posting.source, posting.source_id)
        if identity in seen_ids:
            # One board listing the same posting id twice would make a single
            # upsert-rows call try to touch the same row twice -- dropped here
            # rather than taking the whole batch down, same reasoning as the
            # Postgres version's "cannot affect row a second time" guard.
            stats.skipped += 1
            continue
        seen_ids.add(identity)
        name, domain = lookup.get((posting.source, posting.board_token), (None, None))
        rows.append(
            to_row(
                posting,
                run_id=run_id,
                seen_at=now,
                company_name=name,
                company_domain=domain,
            )
        )

    for start in range(0, len(rows), BATCH_SIZE):
        batch = rows[start : start + BATCH_SIZE]
        stats.merge(await _write_batch(batch, now=now))
    return stats


#: Appwrite rejects a `queries[]` entry once its JSON-encoded string exceeds
#: 4096 chars. Most `source_id` values are short board-native ids, but the
#: scraper falls back to a full posting URL when a board gives it nothing
#: else (see `scraper_import._row_to_posting`), and 100 of those in one
#: `equal` filter's `values` array can blow well past that limit even though
#: it is still under Appwrite's separate 100-item cap. Chunking by a
#: character budget rather than a fixed count handles both provider mixes
#: without needing to know in advance which one a batch contains.
_LOOKUP_CHAR_BUDGET = 3500


async def _lookup_chunk(chunk: list[str]) -> list[dict[str, object]]:
    try:
        return await appwrite_tables.list_rows(
            queries=[{"method": "equal", "attribute": "source_id", "values": chunk}],
            limit=len(chunk),
        )
    except appwrite_tables.AppwriteTablesError:
        # Temporary diagnostics for a 400 this budget-based chunking should
        # have prevented but, live, has not -- logs enough about the actual
        # chunk to tell which of Appwrite's stacked queries[] constraints
        # (item count, a too-long value, an empty value) is the real one,
        # then re-raises unchanged.
        lengths = sorted((len(s) for s in chunk), reverse=True)
        log.error(
            "ingest.lookup_chunk_failed",
            items=len(chunk),
            max_len=lengths[0] if lengths else None,
            min_len=lengths[-1] if lengths else None,
            empties=sum(1 for s in chunk if not s),
            sample=chunk[:3],
        )
        raise


async def _lookup_by_source_id(source_ids: list[str]) -> list[dict[str, object]]:
    existing: list[dict[str, object]] = []
    chunk: list[str] = []
    chunk_chars = 0
    for source_id in source_ids:
        if chunk and chunk_chars + len(source_id) > _LOOKUP_CHAR_BUDGET:
            existing.extend(await _lookup_chunk(chunk))
            chunk, chunk_chars = [], 0
        chunk.append(source_id)
        chunk_chars += len(source_id)
    if chunk:
        existing.extend(await _lookup_chunk(chunk))
    return existing


async def _write_batch(rows: list[dict[str, object]], *, now: datetime) -> UpsertStats:
    """Look up which of this batch's postings already exist, then upsert.

    This is the one real behavioural change from the Postgres version worth
    being blunt about: `ON CONFLICT DO UPDATE` made the whole
    read-decide-write a single atomic statement, so two concurrent crawls of
    the same posting could not race each other. This is a lookup, then a
    separate write, with a real gap between them. Appwrite has no
    conditional-upsert-on-a-non-`$id`-column primitive to close that gap
    with. Acceptable here because this app runs one crawler at a time, not
    several writers contending for the same posting -- but it is a real gap,
    not a hidden one.
    """
    stats = UpsertStats()
    source_ids = [row["source_id"] for row in rows]
    existing_rows = await _lookup_by_source_id(source_ids)
    # Keyed by (source, source_id): source_id alone already embeds the board
    # token for every provider in this codebase, but a board-token collision
    # across two different `source` values is not something to bet the
    # dedupe on.
    existing_by_key = {(r.get("source"), r.get("source_id")): r for r in existing_rows}

    upsert_batch: list[dict[str, object]] = []
    for row in rows:
        key = (row["source"], row["source_id"])
        existing = existing_by_key.get(key)
        if existing is None:
            upsert_batch.append(row)
            stats.inserted += 1
            continue

        changed = existing.get("content_hash") != row["content_hash"]
        was_inactive = not existing.get("active", True)
        payload: dict[str, object] = {
            "$id": existing["$id"],
            "last_seen_at": row["last_seen_at"],
            "last_crawl_run_id": row["last_crawl_run_id"],
            "active": True,
            "inactive_since": None,
            "repost_count": int(existing.get("repost_count") or 0) + (1 if was_inactive else 0),
        }
        if changed:
            for column in _MUTABLE_COLUMNS:
                payload[column] = row[column]
            payload["content_updated_at"] = row["content_updated_at"]
            stats.updated += 1
        else:
            stats.unchanged += 1
        if was_inactive:
            stats.reactivated += 1
        upsert_batch.append(payload)

    await appwrite_tables.upsert_rows(upsert_batch)
    return stats


async def deactivate_missing(
    session: AsyncSession,
    *,
    source: str,
    board_token: str,
    run_id: uuid.UUID,
    at: datetime | None = None,
) -> int:
    """Mark postings this board no longer lists as inactive, in Appwrite.

    Scoped to one board and one run on purpose. "Absent from the crawl" is only
    evidence of closure if that board's current list was actually read, so the
    caller must only call this for a board whose fetch returned LIVE or EMPTY. A
    304 or a timeout means we did not see the list, and deactivating on either
    would close a company's whole board because one request was slow.

    Rows are never deleted. A closed posting is a fact worth showing, and keeping
    it means `first_seen_at` survives if the role is reposted later.

    `session` is accepted and unused, kept so callers did not need a signature
    change. This is one real bulk `update-rows` call, unlike `_write_batch`'s
    per-batch lookup-then-write -- Appwrite's WHERE-then-SET semantics here
    give the same one-statement guarantee `deactivate_missing` always had.
    """
    del session
    now = at or datetime.now(UTC)
    updated = await appwrite_tables.update_rows(
        filters=[
            f"source={source}",
            f"board_token={board_token}",
            "active=true",
            f"last_crawl_run_id!={run_id}",
        ],
        data={"active": False, "inactive_since": now.isoformat()},
    )
    return updated


async def mark_duplicates(
    session: AsyncSession,
    links: list[tuple[uuid.UUID, uuid.UUID, str, float | None]],
) -> int:
    """Point duplicate rows at their canonical row.

    The duplicate keeps its own row: its URL still resolves, the merge is
    reversible if it was wrong, and the read path filters on `canonical_id IS
    NULL` rather than relying on a delete having been correct.

    `duplicate_id`/`canonical_id` are `source_posting_id` values (the stable
    identity `job_index.py` and this module both key on since the move to
    Appwrite), not Appwrite's own `$id`. One `update_rows` call per link,
    not the single batched SQL `UPDATE` Postgres gave this for free -- each
    link sets a different `canonical_id`/`reason`/`score`, so they cannot
    share one `data` patch the way `deactivate_missing`'s single flip to
    `active=False` can. `session` is unused; kept so `worker.py`'s call site
    did not need to change, same as `search_index`.
    """
    del session
    if not links:
        return 0
    marked = 0
    for duplicate_id, canonical_id, reason, score in links:
        if duplicate_id == canonical_id:
            continue
        marked += await appwrite_tables.update_rows(
            filters=[f"source_posting_id={duplicate_id}"],
            queries=[{"method": "isNull", "attribute": "canonical_id"}],
            data={"canonical_id": str(canonical_id), "duplicate_reason": reason, "duplicate_score": score},
        )
    return marked


