# Ingest and index

Crawl public ATS boards on a schedule, store what we find, and answer a search
from that store. Replaces the query-time fan-out in
`apps/web/src/lib/discover/no-key-sources.ts`, which fetches every curated board
on every search and therefore makes our search latency the sum of someone else's
API latency.

Every measurement below was taken on this branch. Where a number is stale or was
produced by an earlier version of the code, it says so.

---

## Why a new table and not `jobs`

`jobs` was the obvious candidate. It already has the right column vocabulary
(`jd_raw`, `jd_clean`, `source`, `source_id`, `posted_at`, `active`,
`first_seen_at`, `last_seen_at`) and a unique constraint on
`(source, source_id)`. `job_postings` deliberately mirrors that vocabulary so
promoting a crawled row into `jobs` stays a field copy. It is still the wrong
home, for four reasons found by reading the code that already queries it:

1. `discovery._annotate_already_imported` marks a search result
   `already_imported=True` when a row exists in `jobs` with the same
   `(source, source_id)`. Crawled rows use exactly that identity space, so
   writing the crawl into `jobs` would make every discovery result report as
   already imported and the import button would disappear from the whole feed.
   That is a silent functional break, not a cosmetic one.
2. `jobs.list_jobs` is `select(Job).where(Job.active == active)` with no user
   scope, because `jobs` holds only rows a user deliberately added. Adding a
   crawl turns the tracker list into the whole internet.
3. `applications.job_id` references `jobs` with `ondelete=RESTRICT`, so a `jobs`
   row is permanent by design. Index rows must be prunable in bulk. A table
   cannot be both.
4. `jobs` takes a handful of inserts a day, each behind an LLM `parse_jd` call.
   The index takes tens of thousands of upserts per sweep with no LLM in the
   loop. Sharing a table means sharing the HNSW index on `jd_embedding`, and
   maintaining an HNSW index during bulk ingest is the specific thing that makes
   bulk ingest slow.

So: two tables, same vocabulary, different jobs. `promote_payload` in
`services/job_index.py` is the one-way door between them.

## Why Postgres and not Elasticsearch

The reference point is hiring.cafe serving 3.7M postings in 277ms on
Elasticsearch. Postgres reaches the same latency class at this corpus size, and
the measurements below bear that out on a 19,461-row index. The repo already
runs Postgres with pgvector, so a later hybrid keyword-plus-embedding rank needs
no new infrastructure.

Signals that would change the answer, none of which are true yet:

- the index passes roughly 10M documents, where GIN maintenance during bulk
  ingest starts to dominate;
- per-field analyzers, faceting or aggregations become the product rather than
  filters;
- search traffic needs to scale independently of the write path.

Adding a second datastore before then buys latency we already have and costs an
operational component a solo-maintained project has to keep alive.

---

## The Appwrite detour, and what it cost

Between 2026-08-18 and 2026-08-31 `job_postings` was not a Postgres table.
`job_index.py` and `ingest/upsert.py` were rewritten against Appwrite TablesDB
(commit `4fcf37d`) to escape Neon's 512MB free-tier storage cap, and the
Postgres table was later dropped by hand to reclaim the 467 MB it held.

That trade was wrong in a way the storage number could not show. **Appwrite
bills database reads per row**, from its own documentation: "if you fetch a
table of 50 rows with a single API call, this counts as 50 read operations, not
as a single operation." One search reads a candidate pool of up to 2,000 rows.
The 6-hourly crawler alone measured **~1.19M reads a month** against an
Education-plan allowance of **1.75M**. Development runs of a cursor-paged
counter spent roughly ten times that on their own. The project ran out, every
Appwrite read began answering `402 limit_databases_reads_exceeded`, and because
resumes, profile facts and the application board share the same quota, the
tailor went down with the search.

Postgres charges for storage, not for reads. That is the entire reason the
table came back. Storage is a resource this workload can bound, and the section
below is the bounding.

What was genuinely lost on the way out, and is back:

| | Appwrite | Postgres |
| --- | --- | --- |
| relevance | fulltext match/no-match, `retrieve_score` flat 1.0 | `ts_rank_cd` over a weighted tsvector, graded |
| title search | one index over a concatenated `search_text` | `to_tsvector(title)` separately from the whole document |
| `location`/`company` | whole-word fulltext | `ILIKE` substring, backed by `pg_trgm` |
| `max_age_days` | filtered `last_seen_at`, the nearest expressible thing | `coalesce(posted_at, first_seen_at)`, the real one |
| upsert | lookup, decide in Python, write, with a race in the gap | one `ON CONFLICT DO UPDATE` |
| counters | `total` saturating at 5,000, or a per-row-billed table walk | `COUNT(*)` |
| derived columns | `search_text` and `posted_at_estimated` maintained by hand in two writers | STORED generated columns |

---

## Storage: what the index actually weighs

`FTS_DESCRIPTION_CHARS` (`db/models/job_posting.py`) is how much of `jd_clean`
reaches `search_vector`. It was 8,000; it is now **600**, applied by migration
`postings_back_to_pg`.

### The measurement

Measured on real data, not modelled. A live sweep of three curated Greenhouse
boards wrote **2,550 real postings** into the rebuilt table; the migration was
then downgraded and upgraded, which rebuilds `search_vector` and its GIN index
at 8,000 and back at 600 over **the same rows**, with a `VACUUM ANALYZE` and a
`pg_total_relation_size` read on each side. Only the slice differs between the
two columns:

| | slice 8,000 | slice 600 | change |
| --- | --- | --- | --- |
| total | 55.4 MB | **36.0 MB** | **-35%** |
| heap | 2.5 MB | 5.0 MB | tsvector fits inline again |
| TOAST | 48.5 MB | 29.2 MB | -40% |
| all indexes | 4.35 MB | 1.78 MB | |
| GIN on `search_vector` | 3.40 MB | **0.60 MB** | **-82%** |
| per row | 22,780 B | **14,800 B** | -7,980 B |

Compressed column sizes at the 600 slice, per row: `jd_clean` 4,610 B (from
7,990 characters), `jd_raw` 5,581 B (from 12,442 characters), `search_vector`
816 B.

Take the heap and TOAST rows together rather than separately: at 8,000 the
stored tsvector is large enough to be pushed out of line, so the heap shrinks
while TOAST grows. The saving is real and it is in the stored vector, which is
where it was expected.

One trap, recorded because it produced a wrong number first: a GIN index built
incrementally by 2,550 individual inserts measured **4.42 MB** at the 600 slice,
seven times the 0.60 MB a fresh build of the same data produces. Compare index
sizes only after a rebuild or a `REINDEX`, or the answer is about write history
rather than about the data.

### What it means at 359,000 rows

The per-row figures above are for a **hydrated** row, and 2,550 rows from three
curated Greenhouse boards is a biased sample: large employers write long
descriptions, and this sample's `jd_clean` averages 7,990 characters against
the 5,569 recorded across 19,461 rows earlier in this document. Treat the
hydrated-row cost as an upper bound. An unhydrated row carries no body at all
and costs roughly 0.9-1.5 KB, measured on a synthetic body-free load where
compression is not a factor.

| hydrated share | slice 8,000 | slice 600 |
| --- | --- | --- |
| 10% | ~1,240 MB | ~920 MB |
| 25% | ~2,400 MB | ~1,650 MB |
| 100% | ~7,800 MB | ~5,300 MB |

**The slice change removes about a third of the table, and 359,000 rows do not
fit Neon's 500 MB free tier at any slice length.** Those are both true and the
second one does not make the first one pointless: a third is a third, and it is
the only part of the cost the search index is responsible for.

Three consequences worth being blunt about:

1. **An earlier projection of ~385 MB does not survive this measurement.** It
   is close to what a body-free table weighs (a synthetic metadata-only load
   measured 312 MB at 359,000 rows), which is what such a number describes: it
   does not count the stored descriptions. The descriptions are real and are
   not optional, because `jd_clean` is the input to `job_enrich.enrich_job` and
   therefore to every fit score.
2. **`job_postings.jd_raw` is written and never read**, and it is the single
   largest column in the table: 5,581 compressed bytes a row, more than
   `jd_clean`'s 4,610. It is the vendor's own markup, set by `to_row` and
   `hydrate._success_patch`, and no read path touches it. `promote_payload`
   does not copy it, and every `job.jd_raw` elsewhere in this codebase is the
   `jobs` table's column, not this one. Dropping it would take roughly 38% off
   the hydrated-row cost. It is a data-model change with its own blast radius,
   so it is named here rather than folded into a search-slice change.
3. **The corpus size is a lever too**, and one already pulled: holding BambooHR
   (see below) keeps 4,992 boards' worth of rows out of the number above.

The hydrated share cannot be measured right now, which is why the table is a
range rather than a figure. The Appwrite table answers 402, and the Postgres
table holds only the 2,550 rows this measurement crawled into it.

**What the slice costs.** Deep-body recall, and only that. The tsvector weights
title `A`, company `B`, location `C` and body `D`, so the body already
contributes least to `ts_rank_cd`; what changes is that a word appearing for
the first time after a description's 600th character can no longer be used to
FIND the posting. Past a few hundred characters a JD is mostly benefits
boilerplate and legal text, which matches every query equally and discriminates
between none of them. No recall measurement was run against the real corpus
before the change, because the corpus was not readable.

**What must not follow it.** `jd_clean` itself stays whole.
`tests/test_search_slice_migration.py` asserts that neither `ingest/upsert.py`
nor `ingest/hydrate.py` so much as mentions `FTS_DESCRIPTION_CHARS`, because
truncating the stored body to match the indexed slice would silently degrade
every future fit score while every already-enriched row went on looking fine.

---

## Corpus: which providers are crawled

`corpus.DEFAULT_CRAWL_PROVIDERS` is what a sweep crawls when nobody says
otherwise: greenhouse, lever, ashby, smartrecruiters, workday, icims,
oracle_cloud.

`corpus.HELD_PROVIDERS` is `{"bamboohr"}`. Held, not deleted:
`seeds/bamboohr.txt` is still in the package, the provider is still registered,
rows already crawled from it stay in the index and stay searchable, and
`cli sweep --providers bamboohr` crawls it today with no code change. Naming a
provider explicitly always wins over the default.

The reason is a product decision pending evidence, not a judgement about the
provider. `bamboohr.txt` is the most carefully built seed file in the package
(4,992 tokens, every one confirmed by fetching it, harvested from Common Crawl
rather than guessed, at a measured 73% hit rate against guessing's 8.8%). What
is unproven is whether 4,992 boards of small employers are worth their share of
an index with a storage budget. To un-hold it, delete the string from
`HELD_PROVIDERS`. That is the whole reversal, which is why the mechanism is a
name in a set rather than a deleted file.

`corpus_summary()` reports every provider by name including held ones, plus
`held_total`, so the hold stays visible rather than looking like a deletion.

---

## Measured: corpus liveness

The seed corpus is 15,874 tokens (greenhouse 8,333, lever 4,368, ashby 3,164,
smartrecruiters 9, plus 85 curated companies carrying real names and domains).
It is a list of candidates, not a verified list.

Sampled on this branch, 60 random tokens per provider at concurrency 4, 180
requests total:

| provider   | sampled | live | empty | missing | reachable | postings | MB   | boards/s |
| ---------- | ------- | ---- | ----- | ------- | --------- | -------- | ---- | -------- |
| greenhouse | 60      | 27   | 4     | 29      | 51.7%     | 1,265    | 17.9 | 4.2      |
| lever      | 60      | 26   | 7     | 27      | 55.0%     | 619      | 10.9 | 3.1      |
| ashby      | 60      | 38   | 5     | 17      | 71.7%     | 622      | 9.0  | 63.8     |
| **total**  | **180** | 91   | 16    | 73      | **59.4%** | 2,506    | 37.8 |          |

So roughly four tokens in ten are dead. That matches the ~62% the research pass
reported closely enough to trust.

**The curated head is much healthier than the bulk tail.** A separate 300-board
sweep, which crawls in priority order and so hits the curated companies first,
left the token table at 201 live / 14 empty / 85 missing, a 71.7% reachable
rate. Same corpus, different slice, twelve points better. Quote whichever number
matches the question being asked; they are not interchangeable.

This is what `ats_board_tokens` exists for. A crawler that re-reads the seed file
every night spends ~40% of its request budget relearning the same 404s forever.
Recording the verdict per token and scheduling the next check from it is what
stops that, and is also what makes a sweep resumable: the schedule lives in the
database, so a killed sweep loses only the boards it was mid-flight on.

Ashby's throughput is an outlier worth not over-reading: 60 boards in 0.94s
against Greenhouse's 14.4s. Ashby's payloads are small and its API is fast.
Lever is the opposite: one board (`postings/palantir`) measured 5,970,962 bytes.

### The SmartRecruiters seed is 9 tokens, and that is honest

The research pass shipped no SmartRecruiters token list and the tokens are not
derivable from company names. 120 plausible names were probed and 9 were live, a
7.5% hit rate, so guessing is not a strategy. Growing it needs a real source.

---

## Measured: read-path latency

Against the local index of 19,461 postings (17,631 active, 201 companies),
`search_index` end to end, in-process, 12 runs per case, page size 60:

| query                          | p50     | p95     | matched | pool  |
| ------------------------------ | ------- | ------- | ------- | ----- |
| browse, no filters             | 22.3ms  | 29.8ms  | 1000+   | 480   |
| keyword: software engineer     | 98.2ms  | 203.8ms | 1000+   | 480   |
| keyword: ml engineer           | 83.1ms  | 85.7ms  | 1000+   | 480   |
| keyword + remote + US          | 28.2ms  | 35.6ms  | 1000+   | 480   |
| three-phrase alternatives      | 213.9ms | 278.7ms | 1000+   | 480   |
| free text over the body        | 221.2ms | 459.6ms | 394     | 394   |
| company substring (trigram)    | 27.3ms  | 31.7ms  | 713     | 480   |
| keyword + max_age_days=30      | 95.9ms  | 304.2ms | 1000+   | 480   |
| deep page (offset 120)         | 215.7ms | 259.8ms | 1000+   | 1440  |
| explain on                     | 157.9ms | 174.0ms | 1000+   | 480   |

Read these as a local-Postgres floor, not as production numbers: no network hop
to Neon, and a warm page cache.

What the shape says:

- **Browse is cheap.** 22ms, because the partial index on
  `coalesce(posted_at, first_seen_at) DESC WHERE active AND canonical_id IS NULL`
  answers it as an index scan with no sort.
- **More alternatives cost more.** A three-phrase tsquery is an OR of three
  AND-groups and roughly doubles a single-phrase query. Worth knowing before the
  UI offers users an unbounded keyword list.
- **Deep pagination is the worst case** at a fixed page size, because the
  candidate pool is `(limit + offset) * 8`. Bounded by `MAX_CANDIDATES = 2000`,
  so it degrades to a ceiling rather than without limit, but offset paging past
  a few pages should become a cursor if the UI ever encourages it.
- **`total_matched` is capped**, not exact. Counting every match for a broad
  keyword query means scanning most of the table for a number the UI renders as
  "1000+". `total_matched_capped` says when the cap was hit.

Two optimizations that came out of measuring rather than guessing:

1. `jd_clean` is not selected in the ranking query. It is TOASTed, and fetching
   it for the whole 480-row candidate pool dominated the query cost. Snippets
   are fetched in a second round trip for the ~60 rows actually returned.
2. Freshness is computed in SQL so filtering, ranking and `LIMIT` happen in one
   pass, and `now` is bound as a parameter so a test with a frozen clock and the
   Python twin in `_freshness_weight` agree exactly.

---

## Ranking

```
rank = retrieve_score * freshness_weight * mix_weight
```

Multiplicative, not additive. With an additive score a perfect title match from
eight months ago outranks a good match from this morning, which is the wrong
answer for a job search because the old one is probably filled. Multiplying
means a stale posting has to be substantially more relevant to win, not
marginally. `test_a_stale_exact_match_loses_to_a_fresh_weaker_match` pins that.

| component          | source                                                        |
| ------------------ | ------------------------------------------------------------- |
| `retrieve_score`   | `ts_rank_cd` over the weighted tsvector, squashed to (0,1] by `x/(x+1)`. Exactly 1.0 when no keywords were given, so a browse ranks on freshness and mix alone. |
| `freshness_weight` | `0.5 ^ (age_days / 14)`, floored at 0.05. Age is measured on `coalesce(posted_at, first_seen_at)`. |
| `mix_weight`       | `0.65 ^ n` for the nth posting from one company on the page, floored at 0.15. Positional, so it is applied in Python after SQL produces the pool. |

The tsvector is weighted A/B/C/D over title, company, location and the first
8,000 characters of the body, which is what lets a title hit outrank a body hit.
It is a STORED generated column, so no writer can forget to maintain it.

An old posting is **demoted, not filtered**. Deciding a posting is gone is the
crawl's job, via `active`, not the ranker's.

Set `explain: true` and every hit carries its components plus `text_rank_raw`,
`age_days`, `effective_date`, `company_rank` and the formula string. It
reconciles: `test_rank_is_the_product_of_its_three_components` asserts the
product equals the reported rank.

---

## Freshness, reported rather than asserted

This is the differentiator and the reason for several columns that would
otherwise look redundant.

- `first_seen_at` never moves after insert. It is absent from `_MUTABLE_COLUMNS`
  by design. A naive `ON CONFLICT DO UPDATE SET first_seen_at = now()` would
  turn every re-crawl into a fake new posting, which is precisely the behaviour
  competitors get caught doing.
- `last_seen_at` moves on every sweep that still finds the posting listed.
- Both are on the API response, so the UI can say "first seen 3 weeks ago, still
  listed 1 hour ago" instead of implying an employer posted something today when
  what actually happened is a crawler saw it today.
- `posted_at_basis` records where the date came from: `published`, `created`,
  `updated` or `first_crawl`. The last two are upper bounds, not posting dates.
- `posted_at_estimated` is a generated column derived from the basis, so the flag
  can never disagree with the basis it comes from. `/index/stats` reports how
  many active rows carry an estimated date.
- `repost_count` counts how many times a posting vanished and came back. A high
  count against an old `first_seen_at` is the signature of a perpetually
  reposted role, which is worth showing a job seeker rather than hiding.
- Postings are **deactivated, never deleted**, and only for boards whose fetch
  returned LIVE or EMPTY in that run. A 304, a timeout, a 5xx or a partial
  paginated read all mean "we did not see the current list".

`posted_within_days` is stricter than `max_age_days` on purpose: it only matches
postings carrying a real published date inside the window, excluding anything
estimated.

---

## Per-vendor traps

All four endpoints are public and unauthenticated. Each has one behaviour that
corrupts the index silently rather than raising, and each has a test named after
it in `tests/test_ingest_providers.py`.

| vendor | endpoint | trap |
| --- | --- | --- |
| Greenhouse | `GET boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true` | `content` is entity-encoded HTML (`&lt;p&gt;`) with no raw `<` at all. Without one unescape pass you store the markup as the description. 404 is an unambiguous dead token. |
| Lever | `GET api.lever.co/v0/postings/{co}?mode=json` | `createdAt` is epoch **milliseconds** (observed `1711403416463`). Read as seconds every Lever posting dates to 1970 and any freshness filter drops the lot. A bad slug answers 404 with an **object**, not an array, so shape must be checked as well as status. |
| Ashby | `GET api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true` | Salary is two levels down in `compensationTiers[].components`, mixed with equity rows whose values are null, and the field is documented both nested under `compensation` and at the top level. `isRemote` reads true on hybrids; `workplaceType` is the trustworthy signal. |
| SmartRecruiters | `GET api.smartrecruiters.com/v1/companies/{co}/postings` | **A company that does not exist answers 200 with `totalFound: 0`**, byte-identical to a real company with nothing open. Verified: `zzznotarealcompany9911` and `Square` both return 50 bytes and `totalFound: 0`. |

The SmartRecruiters trap is the one with a design consequence rather than a
parsing fix. There is no signal in a single response to tell an unknown company
from an idle one, so the provider returns `EMPTY` for both and refuses to guess.
`liveness.py` resolves it over time instead: a token that has **never once**
returned a posting is retired after 8 consecutive EMPTY observations. A token
that has produced postings before is merely idle and stays EMPTY however long
that lasts. Pruning on the first EMPTY would delete every seasonal employer
between hiring rounds.

SmartRecruiters also carries no description in its listing, and hydrating one
board can cost ~4,800 requests. Those rows land with `jd_hydrated=false` and a
factual stand-in built from the listing metadata; the read path exposes
`description_available` so the UI never presents metadata as a job description.

---

## Hydration: the second request per posting

Five providers (SmartRecruiters, Workday, BambooHR, iCIMS, Oracle) have no
description in their list response at all, so their rows are written with
`jd_hydrated=false`. Until 2026-08-30 nothing ever filled them in: measured
against the live index that morning, `descriptions_missing` was 5,000 of
`postings_active` 5,000, which was Appwrite's capped `total` estimate rather
than a true count and should be read as "all of it". (Both counters are exact
`COUNT(*)`s again now that the table is back on Postgres.) The index could rank
those postings on their titles and could not score them on their bodies, and
the tailor had nothing to read.

`ingest/hydrate.py` is that second pass, run by `cli hydrate`. Three things
about it are decisions rather than details.

- **It is an N+1, so it is budgeted like one.** One posting is one request.
  `--limit` defaults to 200, which is `job_index.MAX_LIMIT`, the largest page a
  single search can ask for, so one run fills at least a page of the freshest
  end of the index. Measured live: 200 postings in 11.0s, 4.1 MB, 18.1
  postings/second.
- **Candidates come out newest-first**, the same `last_seen_at DESC` the read
  path builds its own candidate pool with, so the pass fills the window a
  search reaches first rather than a random slice. The honest cost is that
  `last_seen_at` moves in whole sweeps, so the front of the queue is usually
  one vendor (measured: the newest 1,000 unhydrated rows were 1,000 of 1,000
  from a single BambooHR sweep spanning 51 seconds). `--providers` is the lever
  for that.
- **A failed hydrate never deactivates the row.** Every provider's `hydrate()`
  swallows a bad response and returns the posting unchanged, so a 404, a
  timeout and an exhausted 429 retry all arrive as the same "no body".
  Deactivating on that would close live postings because a vendor was slow.
  The list crawl already closes real closures, from a board it genuinely
  re-read. A failure is counted, recorded on the row as an attempt, and given
  up on after three.

Hydration writes the body, the raw markup, `jd_hydrated`, and
`posted_at`/`posted_at_basis` when the detail carries a real date. It no longer
writes the searchable text: while `job_postings` was in Appwrite this pass had
to rebuild a stored `search_text` column byte-for-byte the way `to_row` built
it, a second writer of a derived value carrying a comment about what would
break if the two drifted. Postgres generates `search_vector` from `jd_clean`,
so writing the body updates the index by construction and there is nothing left
to keep in step. It deliberately does **not** rewrite `content_hash`: that
column is the sweep's
change detector over the *list* payload, and rehashing the fetched body would
make every later sweep see a phantom edit, overwrite the body with the thin
stand-in and reset the flag, so the same posting would be paid for forever.
Verified live by re-crawling a board immediately after hydrating it: the
upsert reported `unchanged=1` and the row kept its 11,893-character body.

---

## Dedupe

Two stages. Both constants are JobFunnel's and are pinned by a test so they
cannot drift from what the docstrings claim:

```
MAX_TFIDF_SIMILARITY = 0.75
MIN_JOBS_TO_PERFORM_SIMILARITY_SEARCH = 25
```

**Stage one** is exact: a sha256 `content_hash` over identity plus the first
4,000 characters of the body, then a `dedupe_key` of company|title|location,
folded. Cheap, and it catches the common cases (one opening filed once per
office, the same role on a company board and an aggregator).

**Stage two** is TF-IDF cosine over descriptions, and it is gated hard. The
gates were not the first design; each was forced by inspecting what the previous
one merged, on one real 300-board sweep producing 19,461 postings, scored over
the same 5,000-row candidate set each time:

| gate added | marked / 5000 | comparisons |
| --- | --- | --- |
| global IDF, first 400 tokens | 1830 (36.6%) | 2,620,224 |
| + company blocks, block-local IDF | 875 (17.5%) | 581,051 |
| + `max_df` boilerplate removal | 829 (16.6%) | 586,014 |
| + role gate, grades collapsed | 405 (8.1%) | 934 |
| + role gate, grades preserved | **355 (7.1%)** | **597** |

Global IDF gets it backwards. A company's "About us" and benefits boilerplate
appears in every one of its postings and almost nowhere else in the corpus, so
globally it looks *rare* and IDF weights it highly. The vectors then measure
"are these from the same company", and for a pair from the same company that is
always yes. It merged "Electromechanical Assembly Technician" into "Mechanical
Assembly Technician" at 0.998.

Per-company IDF plus `max_df` still was not enough on heavily templated
employers, merging "Laser Test Engineer" into "Manufacturing Test Engineer" at
0.754. Description similarity alone cannot separate two jobs that share 80% of
their text, so the role a title names became a hard gate rather than a signal.
Grades are canonicalized in spelling but never erased: collapsing them merged
"Senior Software Engineer, Database Internals" into the Staff posting at 0.978,
and those are two openings with two pay bands.

The costs are asymmetric and that is what justifies the conservatism. A
surviving duplicate is visible and the user scrolls past it. A wrongly merged
job is invisible: the user never learns it existed, and it may have been the
better fit.

Duplicates are marked, never deleted. The losing row keeps `canonical_id`
pointing at the survivor, so its URL still resolves and a wrong merge is
reversible. The read path filters on `canonical_id IS NULL`.

> **The duplicate marks currently in the local index are stale.** That database
> holds 1,830 marked rows, of which 1,716 are `tfidf_cosine` with a minimum
> score of 0.7504. 1,830 is exactly the first row of the table above, so those
> marks were written by the pre-gate global-IDF pass and do not reflect the
> shipped algorithm, which produces ~355 on the same input. The shipped
> thresholds are covered by `tests/test_ingest_dedupe.py`; the stored marks are
> not evidence of anything. Re-running a sweep overwrites them for the rows it
> touches.

`dedupe_recent` is scoped to the postings one run touched, because a full
pairwise pass over the index is quadratic and mostly re-answers settled
questions. Cross-run duplicates are caught the next time both rows are
re-crawled together; the content hash catches the rest for free on write.

---

## Running it

**The crawl IS scheduled**, every six hours, by
`.github/workflows/ingest.yml`. This section used to say nothing was, and the
next one described the workflow as sitting at `ingest.yml.disabled` awaiting a
`git mv`. That rename happened on 2026-08-26; the prose did not follow it, and
was still being read as current four days later.

`sweep` is the entrypoint that cron calls, and the same one to use by hand:

```bash
cd apps/api
uv run python -m job_os.ingest.cli seed                         # idempotent
uv run python -m job_os.ingest.cli sweep --limit 200            # crawl what is due
uv run python -m job_os.ingest.cli hydrate --limit 200          # fetch the bodies
uv run python -m job_os.ingest.cli status                       # counters
uv run python -m job_os.ingest.cli sample --provider lever --limit 60   # no DB writes
```

`sample` is how the liveness table above was produced. It opens no database
connection at all, so it is safe to point at any provider from a laptop.

Every command prints one JSON object on stdout, because the caller of a cron job
is a log aggregator and a run that quietly crawled 3 boards instead of 400
should be greppable rather than buried in prose. `sweep` exits nonzero when it
reached no boards.

### What the crawl commits you to

The schedule is **currently commented out** in `.github/workflows/ingest.yml`.
It was enabled, then paused on 2026-08-31 because Appwrite's per-row read
billing made a four-times-daily sweep cost ~1.19M reads a month. That reason is
gone, since `job_postings` is a Postgres table again, and the file says so; the
three lines are still commented out because turning a crawler loose is a
decision for a person rather than a side effect of merging a branch.

Before it ran at all it also spent four days **failing** every run:
`job_postings` had moved to Appwrite and the workflow was never given
`APPWRITE_API_KEY`, so each run exited 1 without crawling anything. A scheduled
job that is red and unwatched looks exactly like one that was never switched
on, which is precisely how that went unnoticed.

What enabling it agrees to.

- **You are crawling other people's APIs, unattended, forever.** At the settings
  in that file it is ~400 requests every six hours across four vendors, none of
  whom have been asked. The politeness measures are real (per-host concurrency
  ceilings, conditional GET, `Retry-After` honoured, no retry on 404, a
  descriptive User-Agent with a contact URL) but they are not permission.
- **Conditional GET is doing most of the work.** All of Greenhouse, Lever and
  Ashby return a strong ETag and honour `If-None-Match` with a 304 and an empty
  body. Re-verified on this branch, fetching each board twice back to back:

  | board | first fetch | re-fetch with ETag |
  | --- | --- | --- |
  | `greenhouse/vercel` (83 postings) | 838,935 bytes | 304, **0 bytes** |
  | `lever/palantir` (309 postings) | 5,970,962 bytes | 304, **0 bytes** |
  | `ashby/ramp` (136 postings) | 2,428,407 bytes | 304, **0 bytes** |

  Since most boards are unchanged on any given re-crawl, storing the ETag per
  token is both the largest bandwidth saving available and the politest thing we
  can do with someone else's API. If you change the fetcher, do not break this.
- **The first runs are the expensive ones**, with ~15,500 tokens at status
  `unknown`. That cost falls away as the corpus prunes itself.
- **GitHub's scheduler is not a guarantee.** Scheduled workflows are queued, not
  promised, are dropped after 60 days of repository inactivity, and run late
  under load. Fine here because coverage accumulates across runs, but it is not
  the right host for a crawl anyone depends on being fresh. The API already runs
  on Heroku (see `docs/DEPLOY.md`); a Heroku Scheduler dyno running the same CLI
  is the better home once this matters.
- **Point `INGEST_DATABASE_URL` somewhere you are willing to have written to on
  a timer.** The workflow fails loudly rather than crawling and discarding.

---

## Integration: swapping the web app over

Not done on this branch, and deliberately so. `/api/v1/index/search` lands
**alongside** `/api/v1/discovery/search` rather than replacing it, because the
web app depends on the live path's behaviour today and
`apps/web/src/lib/discover/no-key-sources.ts` is owned by another branch.

The remaining steps, in order:

1. **Populate the index.** A read path over an empty table is worse than the
   fan-out, so run sweeps until `/api/v1/index/stats` reports coverage worth
   reading. Coverage accumulates; there is no single run that finishes the job.
2. **Add a client for the new endpoint** in the web app alongside the existing
   discovery client. `IndexHitRead` is intentionally close in shape to
   `DiscoveryResult`, so this is a mapping rather than a UI rewrite. The one
   real difference is the freshness fields, and they are the point: render
   `first_seen_at` and `last_seen_at` as two facts, and gate any "posted" label
   on `posted_at_estimated` so an inferred date is never shown as an
   employer-stated one.
3. **Put it behind a flag**, defaulting off, so the two can be compared on the
   same queries. Expect the index to win on latency and lose on coverage until
   step 1 has run for a while.
4. **Compare honestly before switching.** The fan-out's coverage is "whatever
   the 85 curated boards have right now". The index's is "whatever has been
   crawled". Those differ in both directions early on.
5. **Then retire the fan-out** in `no-key-sources.ts`, coordinating with
   whoever owns that file. Keep `/discovery/import` unchanged: `promote_payload`
   exists so an index row becomes a `jobs` row by field copy.
6. **Only then consider deleting the live path.** Keeping it as a fallback for
   the first weeks costs a code path and buys a rollback.

An `already_imported` annotation equivalent to the discovery router's will be
needed on the index results before the swap, so the import button behaves the
same. It is not built here because it belongs with the client work.

---

## Schema notes

Three tables, two migrations: `ingest_index_20260812` created them, and
`postings_back_to_pg` (2026-08-31) recreates `job_postings` with a 600-character
search slice.

The second one has two paths because there are two realities. On production the
table was dropped by hand, outside Alembic, to reclaim 467 MB during the
Appwrite period, so `alembic_version` claims a migration that created a table
that is gone; that path creates it. On CI and any fresh checkout the table is
there; that path drops and re-adds `search_vector` alone, since PostgreSQL
before 17 cannot rewrite a generated column's expression in place. The two were
diffed with `pg_dump -s` and differ only in `search_vector`'s ordinal position,
which nothing here depends on because every INSERT names its columns.

Its downgrade restores the 8,000-character expression and stops. It does not
drop the table: deleting several hundred thousand crawled rows to undo a column
expression is not a reversal.

`job_postings` carries a `jd_embedding` column dimensioned to match
`jobs.jd_embedding`, so an embedding computed here can be copied on promotion
rather than recomputed. **There is no HNSW index on it yet, deliberately.**
Nothing populates the column until an enrichment stage lands, and an empty
vector index still has to be maintained through every bulk sweep. Create it
alongside the first backfill, concurrently so ingest is not blocked:

```sql
CREATE INDEX CONCURRENTLY ix_job_postings_embedding_hnsw
  ON job_postings USING hnsw (jd_embedding vector_cosine_ops);
```

### Migration chain

The revision id is `ingest_index_20260812` and it does not claim a sequence
number. Four branches were open against `0006_resume_engine` at once and all
four wrote a migration called `0007_*`. Numbering only works when one person
allocates the numbers, which was not the situation. Merging two of these
siblings produces two alembic heads; that is expected and is resolved at merge
time by whoever merges second, either by re-pointing `down_revision` at the
revision that landed first or with `alembic merge`. These tables touch nothing
the siblings create, so the order between them does not matter, only that a
single chain exists.

---

## Tests

```bash
cd apps/api && uv run pytest -q
```

The upsert and ranking suites need a real Postgres, because what they assert is
what Postgres does: `ON CONFLICT`, generated columns, `xmax`, partial indexes.
Faking that would test the fake. They skip cleanly without a database and run in
CI, which already provisions pgvector and runs `alembic upgrade head`. Each test
runs inside a transaction that is rolled back, so a run leaves no rows behind
even against a database with real data in it.

| file | covers |
| --- | --- |
| `test_ingest_normalize.py` | dates (Lever milliseconds, the seconds/millis boundary, naive stamps, implausible values), HTML flattening, country inference, hashes and keys |
| `test_ingest_dedupe.py` | both stages, the two thresholds, the role gate, the measured regressions that must **not** merge, transitivity, comparison count |
| `test_ingest_providers.py` | per-vendor parsing and the four traps, plus the shared contract that 304 and 5xx are never `usable` |
| `test_ingest_upsert.py` | `first_seen_at` preservation, `last_seen_at` bumping, deactivation scoped to a run, reposts and the reactivation counter, cleared fields, batch-level duplicate ids |
| `test_job_index_ranking.py` | the rank formula and its EXPLAIN reconciliation, freshness decay and floor, the multiplicative property, company diversity, what the default query hides, what the 600-char slice does and does not reach |
| `test_job_index_title_weight.py` | a title hit outranking a body-only hit, the page that was wrong before it did, and that the candidate pool still does not select `jd_clean` |
| `test_ingest_hydrate.py` | what a hydration write must not touch, and that a fetched body becomes searchable rather than merely stored |
| `test_index_stats.py` | each counter against a known set of rows, and that a deactivated posting leaves `postings_active` without leaving `postings_total` |
| `test_ingest_held_providers.py` | that a held provider is not crawled by default, is still seeded on request, and is still reported by name |
| `test_search_slice_migration.py` | that the model and the migration index the same slice, and that no write path truncates `jd_clean` to match it |

Three files went when `job_postings` came back to Postgres, and are named here
so a reader looking for them stops looking: `tests/_fake_appwrite.py` (an
in-memory TablesDB), `tests/test_appwrite_job_postings.py` (its write and read
path coverage now lives in `test_ingest_upsert.py` and
`test_job_index_ranking.py`, with the filter-parsing tests moved to
`test_appwrite_filter_syntax.py`, which still matters because the application
board still uses that module), and `tests/test_index_stats_degrades.py`
(replaced by `test_index_stats.py`; the failure it guarded against was
Appwrite's, and its own docstring explains what and why).
