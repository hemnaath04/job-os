"""The crawl index: every posting we have ever seen on a public ATS board.

WHY THIS IS NOT THE EXISTING `jobs` TABLE
-----------------------------------------
`jobs` was the obvious candidate. It already carries the right column vocabulary
(`jd_clean`, `jd_parsed`, `jd_embedding`, `source`, `source_id`,
`posted_at`, `active`, `first_seen_at`, `last_seen_at`) and a unique constraint on
`(source, source_id)`, and this table deliberately mirrors it so promoting a
posting into `jobs` stays a field copy. It is still the wrong home, for four
reasons found by reading the code that already queries it.

1.  `discovery._annotate_already_imported` (routers/discovery.py) marks a search
    result `already_imported=True` when a row exists in `jobs` with the same
    `(source, source_id)`. Crawled rows use exactly that identity space, so
    writing the crawl into `jobs` would make every discovery result report as
    already imported, and the import button would disappear from the whole feed.
    That is a silent functional break, not a cosmetic one.

2.  `jobs.list_jobs` (routers/jobs.py) is `select(Job).where(Job.active == active)`
    with no user scope, because `jobs` holds only rows a user deliberately added.
    Adding a crawl of hundreds of thousands of postings turns the tracker list
    into the whole internet.

3.  Lifecycles differ. `applications.job_id` references `jobs` with
    `ondelete=RESTRICT`, so a `jobs` row is permanent by design. Index rows must
    be prunable in bulk, and a table cannot be both.

4.  Write patterns differ. `jobs` takes a handful of inserts a day, each behind an
    LLM `parse_jd` call and a company upsert. The index takes tens of thousands of
    upserts per sweep with no LLM in the loop. Sharing a table means sharing the
    HNSW index on `jd_embedding`, and maintaining an HNSW index during bulk
    ingest is the specific thing that makes bulk ingest slow.

So: two tables, same vocabulary, different jobs. `jobs` stays the curated set a
user tracks; `job_postings` is the index a search reads. `promote_to_job` in
`services/job_index.py` is the one-way door between them.

WHY POSTGRES AND NOT ELASTICSEARCH
----------------------------------
The reference point is hiring.cafe serving 3.7M postings in 277ms on
Elasticsearch. Postgres reaches the same latency class at this corpus size: a GIN
index over a weighted `tsvector` answers a keyword query over a few million rows
in tens of milliseconds, and the repo already runs Postgres with pgvector, so a
later hybrid keyword-plus-embedding rank needs no new infrastructure. The
crossover where a dedicated search cluster starts to pay is roughly 10M documents
or when per-field analyzers and aggregations become the product; `docs/
ingest-index.md` records the signals to watch for. Adding a second datastore
before then would buy latency we already have and cost an operational component
that a solo-maintained project has to keep alive.

THE APPWRITE DETOUR, AND WHY IT ENDED
-------------------------------------
Between 2026-08-18 and 2026-08-31 this table did not exist: `job_index.py` and
`ingest/upsert.py` were rewritten against Appwrite TablesDB (commit 4fcf37d) to
escape Neon's 512MB free-tier storage cap, and the Postgres table was later
dropped to reclaim the 467 MB it held. The model file stayed, unused.

That trade was wrong in a way the storage number could not show, because it
swapped a resource that was merely scarce for one that was metered. **Appwrite
bills database reads per ROW**, from its own documentation: "if you fetch a
table of 50 rows with a single API call, this counts as 50 read operations, not
as a single operation." One search reads a candidate pool of up to 2,000 rows,
and the 6-hourly crawler alone was measured at ~1.19M reads a month against an
Education-plan allowance of 1.75M. The project spent the allowance and every
Appwrite read began returning 402 `limit_databases_reads_exceeded` -- which took
resumes and tailoring down with it, since they share the quota.

Postgres charges for storage, not for reads. That is the whole of the reason
this table is back, and `FTS_DESCRIPTION_CHARS` below is what makes the storage
side of it affordable.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

# pgvector ships no py.typed marker, so mypy cannot see into it. `jobs` imports
# the same symbol untyped; silenced here rather than globally so the day the
# package gains stubs, the suppression is one line to find and delete.
from pgvector.sqlalchemy import Vector  # type: ignore[import-untyped]
from sqlalchemy import (
    Boolean,
    Computed,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from job_os.db.models._mixins import UUIDPK, Timestamped
from job_os.db.models.job import EMBEDDING_DIM
from job_os.db.session import Base

if TYPE_CHECKING:
    from job_os.db.models.company import Company

#: How much of the description feeds the full-text vector.
#:
#: 600, down from 8,000. This is a storage decision with a measured price and a
#: named cost, not a tidy-up.
#:
#: The price, measured on 2,550 real crawled postings by rebuilding this column
#: at both lengths over the same rows: 22,780 bytes a row at 8,000 against
#: 14,800 at 600, a 35% smaller table, with the GIN index over `search_vector`
#: falling from 3.40 MB to 0.60 MB. `docs/ingest-index.md` carries the full
#: breakdown, the projection to 359k rows, and the part of that projection
#: which is a range rather than a figure.
#:
#: The cost: deep-body recall, and only that. The vector is weighted title A,
#: company B, location C, body D (see `SEARCH_VECTOR_SQL`), so the body already
#: contributes least to `ts_rank_cd`; what is lost is the ability to MATCH a
#: posting on a word that appears for the first time after its 600th character.
#: Past a few hundred characters a JD is mostly benefits boilerplate and legal
#: text, which matches every query equally and therefore discriminates between
#: none of them. Unverified: no recall measurement was run against the real
#: corpus before this change, because the corpus was not readable at the time
#: (see the migration's own note).
#:
#: DO NOT "tidy" this by truncating `jd_clean` to match. `jd_clean` is the input
#: to `job_enrich.enrich_job`, which produces the `enrichment` document the fit
#: score reads. Truncating the stored body would silently degrade every future
#: fit score while every already-enriched row went on looking fine, so the
#: damage would not surface as a failure anywhere. Only this derived slice
#: shrinks; the body stays whole.
FTS_DESCRIPTION_CHARS = 600

#: Weighted `tsvector` over the four fields worth searching. The A/B/C/D weights
#: are what let `ts_rank_cd` put a title hit far above a body hit, so "engineer"
#: in the title outranks "engineer" in a sentence about who you will work with.
#: Kept as a STORED generated column so the value can never drift from the row:
#: every writer gets it for free and no writer can forget it.
SEARCH_VECTOR_SQL = (
    "setweight(to_tsvector('english', coalesce(title, '')), 'A') || "
    "setweight(to_tsvector('english', coalesce(company_name, '')), 'B') || "
    "setweight(to_tsvector('english', coalesce(location, '')), 'C') || "
    f"setweight(to_tsvector('english', left(coalesce(jd_clean, ''), {FTS_DESCRIPTION_CHARS})), 'D')"
)

#: A crawled date is evidence, not testimony. `posted_at_basis` records which
#: kind we have, and this generated column is the single boolean the API exposes
#: so the flag can never disagree with the basis it is derived from.
POSTED_AT_ESTIMATED_SQL = "posted_at_basis IN ('updated', 'first_crawl')"


class JobPosting(UUIDPK, Timestamped, Base):
    __tablename__ = "job_postings"
    __table_args__ = (
        UniqueConstraint("source", "source_id", name="uq_job_postings_source_pair"),
    )

    # --- identity -----------------------------------------------------------
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    #: "{board_token}:{external_id}". Matches the web app's existing scheme so a
    #: row crawled here and a row fetched live by `no-key-sources.ts` collide.
    source_id: Mapped[str] = mapped_column(String, nullable=False)
    board_token: Mapped[str] = mapped_column(String, nullable=False)
    external_id: Mapped[str] = mapped_column(String, nullable=False)
    source_url: Mapped[str] = mapped_column(String, nullable=False)

    # --- company ------------------------------------------------------------
    #: Denormalized on purpose. Resolving a company row per posting would put an
    #: upsert and a round trip in the middle of the hot ingest loop for a value
    #: the read path only ever displays. `company_id` is filled in later, by
    #: enrichment, for the postings that get promoted or scored.
    company_name: Mapped[str] = mapped_column(String, nullable=False)
    company_domain: Mapped[str | None] = mapped_column(String, nullable=True)
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="SET NULL"), nullable=True
    )

    # --- what the job is ----------------------------------------------------
    title: Mapped[str] = mapped_column(String, nullable=False)
    location: Mapped[str | None] = mapped_column(String, nullable=True)
    country_code: Mapped[str | None] = mapped_column(String(2), nullable=True)
    remote: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    #: Hire-from-anywhere, as opposed to remote-within-one-country. The country
    #: filter treats the two differently: an "anywhere" posting is plausibly open
    #: to the country being filtered on, a "Remote (US)" one is not.
    anywhere: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    workplace_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    employment_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    department: Mapped[str | None] = mapped_column(String, nullable=True)
    #: Filled by enrichment, not by the crawl. No board states either reliably.
    level: Mapped[str | None] = mapped_column(String(32), nullable=True)
    function: Mapped[str | None] = mapped_column(String(64), nullable=True)

    salary_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    salary_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    salary_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    salary_interval: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # --- body ---------------------------------------------------------------
    # `jd_raw` is deliberately absent. The vendor's own markup was written on
    # every insert and hydrate and read by nothing: measured at 5,581
    # compressed bytes a row against 2,550 real Greenhouse postings, the
    # largest column in the table, larger than `jd_clean` itself. Providers
    # still carry it on `RawPosting` on the way to building `jd_clean`, which
    # is honest and free; storing it is 5.5 KB a row forever for no reader.
    jd_clean: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    #: False when the provider's list endpoint carries no body and the extra
    #: per-posting call has not been made. SmartRecruiters is why this exists.
    #: The read path must not present an unhydrated `jd_clean` as the JD.
    jd_hydrated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    jd_parsed: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    #: Same dimension as `jobs.jd_embedding`, so an embedding computed here can be
    #: copied on promotion instead of recomputed. Populated by a later stage.
    jd_embedding: Mapped[list[float] | None] = mapped_column(
        Vector(EMBEDDING_DIM), nullable=True
    )
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR, Computed(SEARCH_VECTOR_SQL, persisted=True), nullable=False
    )

    # --- dedupe -------------------------------------------------------------
    #: sha256 over identity plus the description head. Changes when a posting is
    #: genuinely edited, which is how the upsert tells "seen again, unchanged"
    #: from "revised" and skips the write in the common case.
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Stage-one collapse key: company|title|location, folded.
    dedupe_key: Mapped[str] = mapped_column(String, nullable=False)
    #: Set on the losing row of a duplicate pair, pointing at the survivor. The
    #: row is kept rather than deleted so the duplicate's own URL still resolves
    #: and so a wrong merge is reversible.
    canonical_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("job_postings.id", ondelete="SET NULL"),
        nullable=True,
    )
    duplicate_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: Cosine similarity that caused the merge, when stage two made the call.
    duplicate_score: Mapped[float | None] = mapped_column(nullable=True)

    # --- freshness, told honestly -------------------------------------------
    posted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: published | created | updated | first_crawl. See ingest/providers/base.py.
    posted_at_basis: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="first_crawl"
    )
    posted_at_estimated: Mapped[bool] = mapped_column(
        Boolean, Computed(POSTED_AT_ESTIMATED_SQL, persisted=True), nullable=False
    )
    closes_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: When we first saw it, and when we last saw it still listed. Both are
    #: exposed on the read path, which is the differentiator: a posting can be
    #: honestly described as "first seen three weeks ago, still listed an hour
    #: ago" instead of being silently re-dated to look new.
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    #: True while the board still lists it. A posting that disappears is
    #: deactivated, never deleted, so a closure is a fact we can show rather than
    #: a row that quietly vanishes.
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    inactive_since: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Times this posting went away and came back. A high count on an old
    #: `first_seen_at` is the signature of a perpetually reposted role, which is
    #: worth showing a job seeker rather than hiding.
    repost_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    #: Which sweep last saw it. Deactivation is "same board, older run id".
    last_crawl_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("crawl_runs.id", ondelete="SET NULL"), nullable=True
    )

    company: Mapped[Company | None] = relationship(lazy="noload")
