// Source catalogue for the Job Finder's search. No per-user picker: which
// sources run is a code change to the fixed set below (see LIVE_SOURCES in
// jobs/page.tsx), not a runtime UI -- there is one operator of this
// deployment, not many tenants who each want their own board selection.
//
// Discovery spans two backends:
//
//   FastAPI (/api/backend/discovery/search)  theirstack, github
//   this app (/api/discover)                 greenhouse, lever, ashby,
//                                            remotive, remoteok, feed:*
//
// The FastAPI side validates `sources` against a Pydantic
// Literal["theirstack","github"], so sending it a key-free source id is a 422,
// not a no-op -- always split by BACKEND_SOURCES/NO_KEY_SOURCES before
// calling either backend.
//
// Deliberately free of any import from ./no-key-sources: that module is the
// server-side fetch layer and pulling it in here would drag its country
// tables into the client bundle.

import type {
  DiscoveryMergeStats,
  DiscoveryResult,
  DiscoverySearchResponse,
  DiscoverySource,
} from "../types";
import { jobIdentity } from "./job-identity.ts";

export const BACKEND_SOURCES: DiscoverySource[] = ["theirstack", "github"];

/**
 * Board-wide feeds. Every company on the board, no slug list.
 *
 * Listed apart from the ATS sources because the difference matters to the user:
 * Greenhouse and friends only ever show the companies in ./ats-companies, while
 * these return whatever the board has. When a search comes back thin, these are
 * the ones worth turning on.
 */
export const FEED_SOURCES: DiscoverySource[] = [
  "feed:himalayas",
  "feed:jobicy",
  "feed:arbeitnow",
];

export const NO_KEY_SOURCES: DiscoverySource[] = [
  "greenhouse",
  "lever",
  "ashby",
  "remotive",
  "remoteok",
  ...FEED_SOURCES,
];

/** Sources that are free but still served by FastAPI. */
const FREE_BACKEND_SOURCES: DiscoverySource[] = ["github"];

/** Everything a search runs without configuring a credential. */
export const FREE_SOURCES: DiscoverySource[] = [
  ...FREE_BACKEND_SOURCES,
  ...NO_KEY_SOURCES,
];

/**
 * Merge the halves of a split search.
 *
 * Interleaved rather than concatenated, for the same reason the server-side
 * orchestrator round-robins its own sources: `limit` applies per backend, so
 * a straight concatenation followed by a cap would let whichever half is
 * listed first swallow the whole page.
 *
 * Dedupe uses the same identity rule as the orchestrator (./job-identity), and
 * it earns its keep here: the SimplifyJobs tables are a curated list of links
 * into Greenhouse and Lever, which is exactly where the key-free half is
 * already looking, so the same requisition arrives twice on most searches.
 *
 * Deduping BEFORE the cap rather than after is the part that changes what the
 * user sees: Verkada's intern requisition 5211595007 came back from both
 * halves and spent two of the sixty slots on one job.
 */
export function mergeDiscoveryResponses(
  parts: DiscoverySearchResponse[],
  // `string[]` rather than DiscoverySource[]: a user's own feed is selected as
  // "custom:<id>", which no union can enumerate. DiscoverySource[] still
  // assigns cleanly, so every existing call site is unchanged.
  selected: string[],
  limit?: number,
): DiscoverySearchResponse {
  const seen = new Set<string>();
  const results: DiscoveryResult[] = [];
  const cap = limit && limit > 0 ? limit : Infinity;
  const longest = Math.max(0, ...parts.map((p) => p.results.length));
  // Everything the sources handed over, counted before anything is dropped.
  // This is the number that has to be reported alongside the page: the per-
  // source counts summed to 109 while the header said 60 results, and nothing
  // on the screen accounted for the other 49.
  const received = parts.reduce((sum, p) => sum + p.results.length, 0);
  let duplicates = 0;

  outer: for (let i = 0; i < longest; i += 1) {
    for (const part of parts) {
      const r = part.results[i];
      if (!r) continue;
      const identity = jobIdentity(r);
      const keys = [
        identity.ats && `ats:${identity.ats}`,
        identity.url && `url:${identity.url}`,
        identity.loose.split("|").every(Boolean) ? `loose:${identity.loose}` : null,
      ].filter((k): k is string => Boolean(k));
      if (keys.some((k) => seen.has(k))) {
        duplicates += 1;
        continue;
      }
      for (const key of keys) seen.add(key);
      results.push(r);
      if (results.length >= cap) break outer;
    }
  }

  // Report a count for every selected source, including the ones that came
  // back empty: the warning banner keys off a zero to explain itself. Every key
  // a backend reports is merged in rather than only the pre-seeded ones, which
  // is what lets a "custom:<id>" count through: a backend only answers for what
  // it was asked, so nothing unselected can appear this way.
  const source_counts: Record<string, number> = {};
  for (const s of selected) source_counts[s] = 0;
  for (const part of parts) {
    for (const [source, count] of Object.entries(part.source_counts ?? {})) {
      source_counts[source] = (source_counts[source] ?? 0) + count;
    }
  }

  return {
    results,
    source_counts,
    errors: parts.flatMap((p) => p.errors ?? []),
    merge: {
      received,
      duplicates,
      // A floor, not a total: the loop stops as soon as the page is full, so
      // rows behind the cap were never examined for duplication. Said here
      // rather than left for the reader of the header to assume either way.
      capped: results.length >= cap && received > results.length,
    },
  };
}

/**
 * Where the rows that did not make the page went.
 *
 * Renders next to the result count, and only names the things that actually
 * happened, so a clean search says nothing rather than reciting three zeroes.
 * It exists because the header used to read "60 results" beside per-source
 * counts summing to 109, and the difference -- the same job arriving from two
 * sources, plus the limit -- was invisible.
 */
export function describeNarrowing(
  merge: DiscoveryMergeStats,
  droppedByIntent: number,
  limit: number,
): string {
  const parts: string[] = [];
  if (merge.duplicates > 0) {
    parts.push(`${merge.duplicates} duplicate${merge.duplicates === 1 ? "" : "s"} merged`);
  }
  if (droppedByIntent > 0) {
    parts.push(
      `${droppedByIntent} placeholder${droppedByIntent === 1 ? "" : "s"} hidden`,
    );
  }
  if (merge.capped) parts.push(`capped at ${limit}`);
  return parts.length ? ` · ${parts.join(" · ")}` : "";
}

/** An empty response, used when one half of the search is not selected. */
export function emptyDiscoveryResponse(): DiscoverySearchResponse {
  return { results: [], source_counts: {}, errors: [] };
}
