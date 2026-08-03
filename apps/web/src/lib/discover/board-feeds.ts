/**
 * Board-wide job feeds: every company, no slug list.
 *
 * The ATS path in ./no-key-sources asks one company at a time, which is why it
 * needs ./ats-companies and why it can only ever see the employers on that list.
 * A board-wide feed inverts that: one request returns postings from every company
 * on that board, so coverage stops being a function of how long the list is.
 *
 * Deliberately a config table rather than a module per provider. The reference
 * implementations in this space ship dozens of bespoke parsers, one per feed, and
 * every one is a file to maintain when a field gets renamed. `mapJobsPayload`
 * already reads an unknown payload by alias, so a feed here is a name, a URL and
 * optionally which query parameters it understands. Adding one is a line.
 *
 * A feed that breaks contributes zero results and one error row, exactly like a
 * failing ATS board, so a dead provider degrades the page rather than emptying
 * it.
 */
import type { DiscoveryResult, DiscoverySourceError } from "../types";
import { mapJobsPayload } from "./custom-fetch";

/** How a given feed spells the parameters we care about. */
interface FeedParams {
  /** Free-text search. Omitted when the feed has none, and then we filter locally. */
  query?: string;
  /** Page size. */
  limit?: string;
  /** ISO-2 country, or a feed-specific region token. */
  country?: string;
}

export interface BoardFeed {
  id: string;
  label: string;
  /** What the user sees in the source picker. */
  blurb: string;
  url: string;
  params?: FeedParams;
  /** Everything on this board is remote, so a country filter should not bin it. */
  remoteOnly?: boolean;
}

/**
 * Verified as free and key-free at the time of writing. Order is roughly by how
 * useful each is for engineering roles.
 *
 * Himalayas is first for a reason: it is the only one here that filters
 * server-side, so it returns matches rather than a page we then throw most of
 * away.
 */
export const BOARD_FEEDS: BoardFeed[] = [
  {
    id: "himalayas",
    label: "Himalayas",
    blurb: "Remote roles across every company, keyword search",
    url: "https://himalayas.app/jobs/api",
    params: { query: "search", limit: "limit" },
    remoteOnly: true,
  },
  {
    id: "jobicy",
    label: "Jobicy",
    blurb: "Remote board, all companies",
    url: "https://jobicy.com/api/v2/remote-jobs",
    params: { query: "tag", limit: "count" },
    remoteOnly: true,
  },
  {
    id: "arbeitnow",
    label: "Arbeitnow",
    blurb: "Open board feed, Europe-weighted",
    url: "https://www.arbeitnow.com/api/job-board-api",
  },
];

const MAX_RESPONSE_CHARS = 2_000_000;

/**
 * Query one feed. Never throws: the caller merges many of these and one dead
 * provider must not take the search with it.
 */
async function fetchFeed(
  feed: BoardFeed,
  opts: { keywords: string[]; limit: number; countryCodes: string[]; timeoutMs: number },
): Promise<{ results: DiscoveryResult[]; error: string | null }> {
  const url = new URL(feed.url);
  // Only the first keyword goes to the server. These feeds take a single search
  // term, and the local filter in ./no-key-sources still applies every phrase
  // afterwards, so narrowing here is a bonus rather than the contract.
  if (feed.params?.query && opts.keywords[0]) {
    url.searchParams.set(feed.params.query, opts.keywords[0]);
  }
  if (feed.params?.limit) {
    url.searchParams.set(feed.params.limit, String(Math.min(opts.limit * 5, 100)));
  }
  if (feed.params?.country && opts.countryCodes[0]) {
    url.searchParams.set(feed.params.country, opts.countryCodes[0]);
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), opts.timeoutMs);
  try {
    const res = await fetch(url, {
      signal: controller.signal,
      cache: "no-store",
      headers: { accept: "application/json", "user-agent": "job-os/1.0" },
    });
    if (!res.ok) return { results: [], error: `HTTP ${res.status}` };
    const text = await res.text();
    if (text.length > MAX_RESPONSE_CHARS) {
      return { results: [], error: "response too large" };
    }
    const results = mapJobsPayload(JSON.parse(text) as unknown, {
      source: `feed:${feed.id}`,
      sourceLabel: feed.label,
    });
    // A remote board rarely states a country, and the merge step drops a posting
    // it cannot place. Saying "remote" in the location is enough for the country
    // filter to let it through.
    if (feed.remoteOnly) {
      for (const r of results) {
        if (!r.location) r.location = "Remote";
      }
    }
    return { results, error: null };
  } catch (e) {
    const err = e as Error;
    return {
      results: [],
      error:
        err.name === "AbortError" || err.name === "TimeoutError"
          ? `timed out after ${opts.timeoutMs}ms`
          : err.message,
    };
  } finally {
    clearTimeout(timer);
  }
}

export interface BoardFeedOutcome {
  results: DiscoveryResult[];
  counts: Record<string, number>;
  errors: DiscoverySourceError[];
}

/** Query the requested feeds in parallel. There are few enough not to pool. */
export async function fetchBoardFeeds(
  feedIds: string[],
  opts: { keywords: string[]; limit: number; countryCodes: string[]; timeoutMs: number },
): Promise<BoardFeedOutcome> {
  const wanted = BOARD_FEEDS.filter((f) => feedIds.includes(`feed:${f.id}`));
  const outcome: BoardFeedOutcome = { results: [], counts: {}, errors: [] };
  if (wanted.length === 0) return outcome;

  const settled = await Promise.all(wanted.map((feed) => fetchFeed(feed, opts)));
  wanted.forEach((feed, i) => {
    const { results, error } = settled[i];
    outcome.counts[`feed:${feed.id}`] = results.length;
    outcome.results.push(...results);
    if (error) {
      outcome.errors.push({
        source: `feed:${feed.id}` as DiscoverySourceError["source"],
        message: error,
      });
    }
  });
  return outcome;
}
