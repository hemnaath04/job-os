"""Command line entrypoint for the crawl. This is what a scheduler calls.

    uv run python -m job_os.ingest.cli seed
    uv run python -m job_os.ingest.cli sweep --providers greenhouse --limit 200
    uv run python -m job_os.ingest.cli hydrate --limit 200
    uv run python -m job_os.ingest.cli status
    uv run python -m job_os.ingest.cli sample --provider lever --limit 120
    uv run python -m job_os.ingest.cli import-scraper

`hydrate` is the second half of `sweep` for the five vendors whose list
endpoint carries no description. It is a separate command, not a sweep stage,
because it is an N+1 (one request per posting) and so has to be budgeted
separately from the list crawl; `ingest/hydrate.py` explains the ordering and
the limit. It touches Appwrite only, so it can be scheduled independently of
`sweep` -- though running it shortly after one is what keeps its newest-first
queue pointed at rows a search will actually reach.

`sweep` (this repo's own direct-to-ATS crawl) is still not scheduled anywhere -
`docs/ingest-index.md` has the three lines of workflow YAML and says what to
weigh before turning it on. `import-scraper` is a different, lighter path: it
pulls already-crawled postings from a personal standalone scraper (own infra,
own schedule, covers BambooHR/Workday/iCIMS too) via SCRAPER_EXPORT_URL/KEY and
writes them through the same upsert_postings/deactivate_missing this module's
own sweep uses (which now write to Appwrite, not Postgres - see
services/appwrite_tables.py) - no crawling here, so it's safe to schedule
(e.g. Heroku Scheduler) independently of `sweep`.

`sample` exists so liveness and throughput can be measured without writing to a
database, which is how the numbers quoted in `liveness.py` were produced.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime

from sqlalchemy import select

from job_os.ingest.corpus import corpus_summary, seed_tokens
from job_os.ingest.fetcher import PoliteFetcher
from job_os.ingest.hydrate import DEFAULT_LIMIT as DEFAULT_HYDRATE_LIMIT
from job_os.ingest.providers import PROVIDER_NAMES, BoardStatus, get_provider
from job_os.ingest.worker import (
    DEFAULT_CONCURRENCY,
    DEFAULT_GROUP_SIZE,
    DEFAULT_TOKEN_LIMIT,
    run_sweep,
)


def _emit(payload: dict[str, object]) -> None:
    """One JSON object per invocation, on stdout.

    Machine-readable on purpose: the caller of a cron job is a log aggregator, and
    a run that quietly crawled 3 boards instead of 400 should be greppable rather
    than buried in prose.
    """
    print(json.dumps(payload, default=str, sort_keys=True))


async def _cmd_import_scraper(args: argparse.Namespace) -> int:
    from job_os.db.session import async_session
    from job_os.ingest.scraper_import import run_import

    async with async_session() as session:
        try:
            result = await run_import(session)
        except RuntimeError as e:
            _emit({"command": "import-scraper", "error": str(e)})
            return 1
    _emit({"command": "import-scraper", **result.as_dict()})
    return 0


async def _cmd_seed(args: argparse.Namespace) -> int:
    from job_os.db.session import async_session
    from job_os.ingest import liveness

    async with async_session() as session:
        result = await liveness.seed_corpus(session, args.providers)
        await session.commit()
    _emit({"command": "seed", "corpus": corpus_summary(), **result})
    return 0


async def _cmd_sweep(args: argparse.Namespace) -> int:
    from job_os.db.session import async_session

    async with async_session() as session:
        result = await run_sweep(
            session,
            providers=args.providers,
            token_limit=args.limit,
            concurrency=args.concurrency,
            group_size=args.group_size,
            include_retired=args.include_retired,
            seed=not args.no_seed,
            dedupe=not args.no_dedupe,
        )
    _emit({"command": "sweep", **result.as_dict()})
    # A sweep that reached nothing is a failure worth a nonzero exit, so a
    # scheduler surfaces it instead of reporting a green run that did nothing.
    return 0 if result.tokens_attempted else 1


async def _cmd_hydrate(args: argparse.Namespace) -> int:
    """Fetch the descriptions the list endpoints did not carry.

    No database session: this pass reads and writes `job_postings` in Appwrite
    and never touches Postgres, so it does not open one. It records no
    `crawl_runs` row for the same reason, and because a `crawl_run` means "a
    board's list was read", which this never does.
    """
    from job_os.ingest.hydrate import hydrate_descriptions
    from job_os.services.appwrite_tables import AppwriteTablesError

    try:
        result = await hydrate_descriptions(
            providers=args.providers, limit=args.limit, concurrency=args.concurrency
        )
    except AppwriteTablesError as exc:
        _emit({"command": "hydrate", "error": str(exc)})
        return 1
    _emit({"command": "hydrate", **result.as_dict()})
    # Unlike `sweep`, zero work is the success case here: it means every
    # posting a search can reach already has its body. Exiting nonzero on it
    # would page whoever reads the scheduler's logs to tell them the index is
    # finished.
    return 0


async def _cmd_status(args: argparse.Namespace) -> int:
    from job_os.db.models.ingest import CrawlRun
    from job_os.db.session import async_session
    from job_os.ingest import liveness
    from job_os.services import job_index

    postings = await job_index.index_stats()
    async with async_session() as session:
        tokens = await liveness.liveness_summary(session)
        last_run = await session.scalar(
            select(CrawlRun).order_by(CrawlRun.started_at.desc()).limit(1)
        )

    _emit(
        {
            "command": "status",
            "postings": postings,
            "tokens": tokens,
            "last_run": last_run.as_summary() if last_run else None,
        }
    )
    return 0


async def _cmd_sample(args: argparse.Namespace) -> int:
    """Fetch boards and report liveness and throughput without writing anything.

    No database connection at all, so it is safe to point at any provider from a
    laptop to check whether a corpus is worth crawling.
    """
    provider = get_provider(args.provider)
    tokens = [s.token for s in seed_tokens([args.provider])]
    if args.limit and len(tokens) > args.limit:
        import random

        tokens = random.Random(args.seed).sample(tokens, args.limit)  # noqa: S311

    started = datetime.now(UTC)
    async with PoliteFetcher(
        concurrency=args.concurrency, per_host_concurrency=args.concurrency
    ) as fetcher:
        t0 = asyncio.get_running_loop().time()
        results = await asyncio.gather(
            *(provider.fetch_board(fetcher, token) for token in tokens),
            return_exceptions=True,
        )
        elapsed = asyncio.get_running_loop().time() - t0
        stats = fetcher.stats

    counts: dict[str, int] = {}
    postings = 0
    failures: list[str] = []
    for token, result in zip(tokens, results, strict=True):
        if isinstance(result, BaseException):
            counts["exception"] = counts.get("exception", 0) + 1
            failures.append(f"{token}: {type(result).__name__}: {result}")
            continue
        counts[result.status.value] = counts.get(result.status.value, 0) + 1
        postings += len(result.postings)
        if result.status is BoardStatus.ERROR and result.error:
            failures.append(f"{token}: {result.error}")

    reachable = counts.get("live", 0) + counts.get("empty", 0)
    boards_per_second = len(tokens) / elapsed if elapsed else 0.0
    _emit(
        {
            "command": "sample",
            "provider": args.provider,
            "started_at": started,
            "sampled": len(tokens),
            "corpus": len(seed_tokens([args.provider])),
            "elapsed_s": round(elapsed, 2),
            "concurrency": args.concurrency,
            "status_counts": counts,
            "postings": postings,
            "reachable_pct": round(100 * reachable / len(tokens), 1) if tokens else 0.0,
            "boards_per_second": round(boards_per_second, 1),
            "megabytes": round(stats.bytes_read / 1e6, 1),
            "requests": stats.requests,
            "sample_failures": failures[:5],
        }
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="job_os.ingest.cli", description="ATS ingest for the job.os index"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    seed = sub.add_parser("seed", help="insert the bundled token corpus (idempotent)")
    seed.add_argument("--providers", nargs="*", choices=PROVIDER_NAMES, default=None)
    seed.set_defaults(handler=_cmd_seed)

    sweep = sub.add_parser("sweep", help="crawl the boards that are due")
    sweep.add_argument("--providers", nargs="*", choices=PROVIDER_NAMES, default=None)
    sweep.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_TOKEN_LIMIT,
        help=f"tokens this run may crawl (default {DEFAULT_TOKEN_LIMIT}); 0 for no limit",
    )
    sweep.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    sweep.add_argument("--group-size", type=int, default=DEFAULT_GROUP_SIZE)
    sweep.add_argument(
        "--include-retired",
        action="store_true",
        help="also re-check tokens that were given up on",
    )
    sweep.add_argument("--no-seed", action="store_true", help="skip the corpus seed step")
    sweep.add_argument("--no-dedupe", action="store_true")
    sweep.set_defaults(handler=_cmd_sweep)

    hydrate = sub.add_parser(
        "hydrate",
        help="fetch real descriptions for postings whose board list carried none",
    )
    hydrate.add_argument("--providers", nargs="*", choices=PROVIDER_NAMES, default=None)
    hydrate.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_HYDRATE_LIMIT,
        help=(
            f"postings this run may hydrate (default {DEFAULT_HYDRATE_LIMIT}). "
            "One posting is one request, so this is the whole budget"
        ),
    )
    hydrate.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    hydrate.set_defaults(handler=_cmd_hydrate)

    status = sub.add_parser("status", help="index and corpus counters")
    status.set_defaults(handler=_cmd_status)

    import_scraper = sub.add_parser(
        "import-scraper",
        help="pull from the standalone job-scraper's export and upsert into job_postings (Appwrite)",
    )
    import_scraper.set_defaults(handler=_cmd_import_scraper)

    sample = sub.add_parser(
        "sample", help="measure liveness and throughput without touching the database"
    )
    sample.add_argument("--provider", choices=PROVIDER_NAMES, required=True)
    sample.add_argument("--limit", type=int, default=120)
    sample.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    sample.add_argument("--seed", type=int, default=20260812, help="sampling seed")
    sample.set_defaults(handler=_cmd_sample)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # Only `sweep` reads 0 as "no limit". `hydrate` deliberately has no such
    # escape hatch: unbounded there means one request per unhydrated posting in
    # the whole index, which is the thing its own limit exists to prevent.
    if args.command == "sweep" and args.limit == 0:
        args.limit = None
    exit_code: int = asyncio.run(args.handler(args))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
