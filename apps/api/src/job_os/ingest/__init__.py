"""Continuous ATS ingest and the indexed read path.

The problem this package exists to solve: every discovery search used to fan out
live HTTP to a curated list of company boards, download megabytes to return at
most sixty rows, and persist nothing (`schemas/discovery.py` says so outright).
Search latency was therefore the sum of someone else's API latency, coverage was
capped at the curated list, and nothing was learned between two searches.

Here the two halves are separated. `worker` crawls boards on a schedule and
writes to `job_postings`; `services/job_index` answers a search from that table.
A search stops being a fan-out and becomes an indexed query.

  corpus       bundled company-token seed lists
  fetcher      polite HTTP: bounded concurrency, conditional GET, honest retries
  providers/   one module per ATS vendor, each owning its own payload quirks
  normalize    payload field -> storable, comparable value
  dedupe       exact key, then TF-IDF cosine over descriptions
  upsert       idempotent writes that preserve first_seen_at
  liveness     which tokens are worth crawling again, and when
  worker       orchestration for one sweep
  cli          the entrypoint a scheduler calls
"""
