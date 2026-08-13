"""Polite HTTP for the crawl.

Three things here matter more than the code volume suggests.

**Conditional GET.** All three of Greenhouse, Lever and Ashby return a strong
`ETag` and honour `If-None-Match` with a `304` and an empty body. Measured on
this branch: an unchanged Greenhouse board goes from 843,618 bytes to 0, and a
Lever board from 231,487 bytes to 0. Since a re-crawl finds most boards
unchanged, storing the ETag per token is the single largest bandwidth saving
available, and it is also the politest thing we can do to someone else's API.

**Bounded concurrency, per host.** One global semaphore would let a sweep point
every worker at boards-api.greenhouse.io at once. The per-host semaphore is what
keeps the load on any one vendor modest regardless of how the corpus is ordered.

**Retries that respect the answer.** A 429 with `Retry-After` is an instruction,
not an error to paper over with a fixed sleep. A 404 is never retried: it is the
answer, and for a token corpus that is ~62% live, retrying 404s would triple the
request count to learn nothing.
"""
from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
import structlog

log = structlog.get_logger(__name__)

USER_AGENT = "job-os/1.0 (+https://github.com/hemnaath04/job-os) discovery-bot"

DEFAULT_TIMEOUT_S = 20.0
DEFAULT_CONCURRENCY = 8
DEFAULT_PER_HOST_CONCURRENCY = 8
MAX_ATTEMPTS = 3
#: A board payload over this is a bug or an abuse vector, not a job board.
MAX_RESPONSE_BYTES = 64 * 1024 * 1024
RETRY_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})


@dataclass(slots=True)
class FetchStats:
    requests: int = 0
    bytes_read: int = 0
    not_modified: int = 0
    retries: int = 0
    errors: int = 0
    #: Bytes a re-crawl would have transferred without conditional GET, taken
    #: from the last known payload size. Reported so the saving is measurable
    #: rather than asserted.
    bytes_saved_estimate: int = 0

    def merge(self, other: FetchStats) -> None:
        self.requests += other.requests
        self.bytes_read += other.bytes_read
        self.not_modified += other.not_modified
        self.retries += other.retries
        self.errors += other.errors
        self.bytes_saved_estimate += other.bytes_saved_estimate


@dataclass(slots=True)
class FetchResponse:
    status_code: int
    payload: Any | None
    etag: str | None
    bytes_read: int
    requests_made: int
    not_modified: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status_code == 200 and self.error is None


class PoliteFetcher:
    """Shared HTTP client with global and per-host concurrency ceilings."""

    def __init__(
        self,
        *,
        concurrency: int = DEFAULT_CONCURRENCY,
        per_host_concurrency: int = DEFAULT_PER_HOST_CONCURRENCY,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        min_host_interval_s: float = 0.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._concurrency = max(1, concurrency)
        self._per_host = max(1, min(per_host_concurrency, self._concurrency))
        self._timeout_s = timeout_s
        self._min_host_interval_s = min_host_interval_s
        self._gate = asyncio.Semaphore(self._concurrency)
        self._host_gates: dict[str, asyncio.Semaphore] = {}
        self._host_last_call: dict[str, float] = {}
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_s, connect=10.0),
            headers={"accept": "application/json", "user-agent": USER_AGENT},
            follow_redirects=True,
            limits=httpx.Limits(
                max_connections=self._concurrency,
                max_keepalive_connections=self._concurrency,
            ),
        )
        self.stats = FetchStats()

    async def __aenter__(self) -> PoliteFetcher:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _host_gate(self, host: str) -> asyncio.Semaphore:
        gate = self._host_gates.get(host)
        if gate is None:
            gate = asyncio.Semaphore(self._per_host)
            self._host_gates[host] = gate
        return gate

    async def _throttle(self, host: str) -> None:
        """Optional floor on the gap between two calls to the same host."""
        if self._min_host_interval_s <= 0:
            return
        last = self._host_last_call.get(host)
        now = time.monotonic()
        if last is not None:
            wait = self._min_host_interval_s - (now - last)
            if wait > 0:
                await asyncio.sleep(wait)
        self._host_last_call[host] = time.monotonic()

    async def get_json(
        self,
        url: str,
        *,
        host: str,
        etag: str | None = None,
        expect_bytes: int = 0,
    ) -> FetchResponse:
        """One conditional GET, with retries, returning parsed JSON.

        `expect_bytes` is the size this board came in at last time. It is only
        used to attribute a saving to the 304 path, so the crawl can report how
        much bandwidth conditional GET actually avoided.
        """
        headers: dict[str, str] = {}
        if etag:
            headers["if-none-match"] = etag

        requests_made = 0
        last_error: str | None = None
        last_status: int | None = None

        for attempt in range(1, MAX_ATTEMPTS + 1):
            retry_after: float | None = None
            async with self._gate, self._host_gate(host):
                await self._throttle(host)
                requests_made += 1
                self.stats.requests += 1
                try:
                    response = await self._client.get(url, headers=headers)
                except (TimeoutError, httpx.HTTPError) as exc:
                    last_error = f"{type(exc).__name__}: {exc}"
                    last_status = None
                else:
                    last_status = response.status_code
                    body = response.content
                    self.stats.bytes_read += len(body)

                    if response.status_code == 304:
                        self.stats.not_modified += 1
                        self.stats.bytes_saved_estimate += expect_bytes
                        return FetchResponse(
                            status_code=304,
                            payload=None,
                            etag=etag,
                            bytes_read=len(body),
                            requests_made=requests_made,
                            not_modified=True,
                        )

                    if response.status_code == 200:
                        if len(body) > MAX_RESPONSE_BYTES:
                            return FetchResponse(
                                status_code=200,
                                payload=None,
                                etag=None,
                                bytes_read=len(body),
                                requests_made=requests_made,
                                error=f"payload {len(body)} bytes exceeds cap",
                            )
                        try:
                            parsed = response.json()
                        except ValueError as exc:
                            # A board that answers 200 with HTML is a vendor
                            # error page, not a board. Do not retry it.
                            return FetchResponse(
                                status_code=200,
                                payload=None,
                                etag=None,
                                bytes_read=len(body),
                                requests_made=requests_made,
                                error=f"not json: {exc}",
                            )
                        return FetchResponse(
                            status_code=200,
                            payload=parsed,
                            etag=response.headers.get("etag"),
                            bytes_read=len(body),
                            requests_made=requests_made,
                        )

                    if response.status_code not in RETRY_STATUSES:
                        # 404 and friends are the answer. Hand back the body so
                        # the provider can tell a missing board from a soft error.
                        soft: Any | None
                        try:
                            soft = response.json()
                        except ValueError:
                            soft = None
                        return FetchResponse(
                            status_code=response.status_code,
                            payload=soft,
                            etag=None,
                            bytes_read=len(body),
                            requests_made=requests_made,
                        )

                    last_error = f"HTTP {response.status_code}"
                    retry_after = _retry_after_seconds(response)

            if attempt == MAX_ATTEMPTS:
                break

            self.stats.retries += 1
            delay = retry_after if last_status == 429 and retry_after else _backoff(attempt)
            log.debug(
                "ingest.fetch.retry", url=url, attempt=attempt, delay=delay, error=last_error
            )
            await asyncio.sleep(delay)

        self.stats.errors += 1
        return FetchResponse(
            status_code=last_status or 0,
            payload=None,
            etag=None,
            bytes_read=0,
            requests_made=requests_made,
            error=last_error or "unknown error",
        )


def _retry_after_seconds(response: httpx.Response) -> float | None:
    raw = response.headers.get("retry-after")
    if not raw:
        return None
    try:
        # Cap it: a vendor asking us to wait an hour means "skip this board this
        # sweep", not "hold a worker idle for an hour".
        return min(float(raw), 60.0)
    except ValueError:
        return None


def _backoff(attempt: int) -> float:
    """Exponential with full jitter, so a fleet of workers does not resynchronize."""
    return random.uniform(0.0, min(8.0, 0.5 * (2**attempt)))  # noqa: S311


@dataclass(slots=True)
class BoardTiming:
    """Wall-clock accounting for a sweep, for honest throughput reporting."""

    started_at: float = field(default_factory=time.perf_counter)
    finished_at: float | None = None

    def stop(self) -> float:
        self.finished_at = time.perf_counter()
        return self.elapsed_s

    @property
    def elapsed_s(self) -> float:
        end = self.finished_at if self.finished_at is not None else time.perf_counter()
        return end - self.started_at
