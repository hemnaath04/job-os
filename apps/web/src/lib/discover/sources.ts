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

import type { DiscoverySearchResponse, DiscoverySource } from "../types";

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
 * Dedupe uses the same identity rule as the orchestrator, and it earns its
 * keep here: TheirStack scrapes the very ATS boards the key-free half queries
 * directly, so the overlap is real rather than theoretical.
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
  const results = [];
  const cap = limit && limit > 0 ? limit : Infinity;
  const longest = Math.max(0, ...parts.map((p) => p.results.length));

  outer: for (let i = 0; i < longest; i += 1) {
    for (const part of parts) {
      const r = part.results[i];
      if (!r) continue;
      const url = r.source_url.toLowerCase();
      const identity = [
        (r.company_domain ?? r.company_name ?? "").toLowerCase(),
        r.title.toLowerCase(),
        (r.location ?? "").toLowerCase(),
      ].join("|");
      if ((url && seen.has(url)) || seen.has(identity)) continue;
      if (url) seen.add(url);
      seen.add(identity);
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
  };
}

/** An empty response, used when one half of the search is not selected. */
export function emptyDiscoveryResponse(): DiscoverySearchResponse {
  return { results: [], source_counts: {}, errors: [] };
}
