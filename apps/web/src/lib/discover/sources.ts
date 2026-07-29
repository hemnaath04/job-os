// Source catalogue shared by the Job Finder UI.
//
// Discovery now spans two backends and the split is not cosmetic:
//
//   FastAPI (/api/backend/discovery/search)  theirstack, github
//   this app (/api/discover)                 greenhouse, lever, ashby,
//                                            remotive, remoteok,
//                                            jsearch, adzuna
//
// The FastAPI side validates `sources` against a Pydantic
// Literal["theirstack","github"], so sending it a key-free source id is a 422,
// not a no-op. Always route a search through splitSources() before calling
// either backend.
//
// jsearch and adzuna share the second route but need a credential, and it is
// the user's own: splitSources reports them separately so the caller can drop
// the ones with no key pasted yet.
//
// Deliberately free of any import from ./no-key-sources: that module is the
// server-side fetch layer and pulling it in here would drag its country
// tables into the client bundle. ./keys is client-safe, so its type is fine.

import type { DiscoverySearchResponse, DiscoverySource } from "../types";
import type { DiscoveryKeys } from "./keys";

export const BACKEND_SOURCES: DiscoverySource[] = ["theirstack", "github"];

export const NO_KEY_SOURCES: DiscoverySource[] = [
  "greenhouse",
  "lever",
  "ashby",
  "remotive",
  "remoteok",
];

/** Served by /api/discover, but only once the user has pasted a key. */
export const BYO_KEY_SOURCES: DiscoverySource[] = ["jsearch", "adzuna"];

/** Sources that are free but still served by FastAPI. */
const FREE_BACKEND_SOURCES: DiscoverySource[] = ["github"];

/** Everything the user can run without configuring a credential. */
export const FREE_SOURCES: DiscoverySource[] = [
  ...FREE_BACKEND_SOURCES,
  ...NO_KEY_SOURCES,
];

/** Sources gated behind a credential, wherever that credential lives. */
export const KEYED_SOURCES: DiscoverySource[] = [
  "theirstack",
  ...BYO_KEY_SOURCES,
];

export interface SourceMeta {
  label: string;
  hint: string;
  /** Shown as a badge and used to group the toggle. */
  needsKey?: boolean;
  /**
   * Where the credential lives. "server" is an environment variable on the
   * API; "byo" is pasted into the browser on /jobs/keys.
   */
  credential?: "server" | "byo";
  /** Rendered by the "How to get a key" disclosure. */
  keySteps?: string[];
  keyUrl?: string;
  /** The inputs /jobs/keys renders for a "byo" source. */
  keyFields?: {
    name: keyof DiscoveryKeys;
    label: string;
    placeholder: string;
  }[];
}

export const SOURCE_META: Record<DiscoverySource, SourceMeta> = {
  github: {
    label: "GitHub",
    hint: "SimplifyJobs internships + new grad",
  },
  greenhouse: {
    label: "Greenhouse",
    hint: "Stripe, Airbnb, Anthropic and 17 more",
  },
  lever: {
    label: "Lever",
    hint: "Public Lever boards",
  },
  ashby: {
    label: "Ashby",
    hint: "OpenAI, Ramp, Notion and 7 more",
  },
  remotive: {
    label: "Remotive",
    hint: "Remote roles, worldwide",
  },
  remoteok: {
    label: "RemoteOK",
    hint: "Remote roles, newest first",
  },
  theirstack: {
    label: "TheirStack",
    hint: "LinkedIn, Lever, Greenhouse, Ashby, Workday",
    needsKey: true,
    credential: "server",
    keyUrl: "https://theirstack.com",
  },
  jsearch: {
    label: "JSearch",
    hint: "Google-for-Jobs: LinkedIn, Indeed, Glassdoor and more",
    needsKey: true,
    credential: "byo",
    keyUrl: "https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch",
    keyFields: [
      {
        name: "jsearch",
        label: "RapidAPI key",
        placeholder: "paste your X-RapidAPI-Key",
      },
    ],
    keySteps: [
      "Open the JSearch API on RapidAPI and sign in (GitHub login works).",
      "Subscribe to the free Basic plan (200 searches a month, no credit card).",
      "Open the Endpoints tab and copy the X-RapidAPI-Key value from the code snippet.",
      "Paste it below. It is stored only in this browser and sent straight to the job boards.",
    ],
  },
  adzuna: {
    label: "Adzuna",
    hint: "Aggregated postings across 15+ countries",
    needsKey: true,
    credential: "byo",
    keyUrl: "https://developer.adzuna.com/signup",
    keyFields: [
      { name: "adzuna_app_id", label: "app_id", placeholder: "Adzuna app_id" },
      {
        name: "adzuna_app_key",
        label: "app_key",
        placeholder: "Adzuna app_key",
      },
    ],
    keySteps: [
      "Register at developer.adzuna.com/signup (free).",
      "Adzuna emails you an app_id and an app_key.",
      "Paste both below. The free tier allows 2,500 searches a month.",
      "Keys stay in this browser only.",
    ],
  },
};

export interface SplitSources {
  backend: DiscoverySource[];
  noKey: DiscoverySource[];
  byoKey: DiscoverySource[];
}

export function splitSources(sources: DiscoverySource[]): SplitSources {
  return {
    backend: sources.filter((s) => BACKEND_SOURCES.includes(s)),
    noKey: sources.filter((s) => NO_KEY_SOURCES.includes(s)),
    byoKey: sources.filter((s) => BYO_KEY_SOURCES.includes(s)),
  };
}

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
