// Source catalogue shared by the Job Finder UI.
//
// Discovery now spans two backends and the split is not cosmetic:
//
//   FastAPI (/api/backend/discovery/search)  theirstack, github
//   this app (/api/discover)                 greenhouse, lever, ashby,
//                                            remotive, remoteok
//
// The FastAPI side validates `sources` against a Pydantic
// Literal["theirstack","github"], so sending it a key-free source id is a 422,
// not a no-op. Always route a search through splitSources() before calling
// either backend.
//
// Deliberately free of any import from ./no-key-sources: that module is the
// server-side fetch layer and pulling it in here would drag its country
// tables into the client bundle.

import type { DiscoverySearchResponse, DiscoverySource } from "../types";

export const BACKEND_SOURCES: DiscoverySource[] = ["theirstack", "github"];

export const NO_KEY_SOURCES: DiscoverySource[] = [
  "greenhouse",
  "lever",
  "ashby",
  "remotive",
  "remoteok",
];

/** Sources that are free but still served by FastAPI. */
const FREE_BACKEND_SOURCES: DiscoverySource[] = ["github"];

/** Everything the user can run without configuring a credential. */
export const FREE_SOURCES: DiscoverySource[] = [
  ...FREE_BACKEND_SOURCES,
  ...NO_KEY_SOURCES,
];

/** Sources gated behind a credential the user has to supply. */
export const KEYED_SOURCES: DiscoverySource[] = ["theirstack"];

export interface SourceMeta {
  label: string;
  hint: string;
  /** Shown as a badge and used to group the toggle. */
  needsKey?: boolean;
  /** Rendered by the "How to get a key" disclosure. */
  keySteps?: string[];
  keyUrl?: string;
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
    keyUrl: "https://theirstack.com",
    keySteps: [
      "Create an account at theirstack.com and open Settings, then API keys.",
      "Copy the key. The free tier includes a starting credit balance.",
      "In the Render dashboard, open the job-os-api service and add an environment variable named THEIRSTACK_API_KEY.",
      "Redeploy the service. TheirStack charges one credit per result it returns.",
    ],
  },
};

export interface SplitSources {
  backend: DiscoverySource[];
  noKey: DiscoverySource[];
}

export function splitSources(sources: DiscoverySource[]): SplitSources {
  return {
    backend: sources.filter((s) => BACKEND_SOURCES.includes(s)),
    noKey: sources.filter((s) => NO_KEY_SOURCES.includes(s)),
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
  selected: DiscoverySource[],
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
  // back empty: the warning banner keys off a zero to explain itself. Sources
  // the user did not select are left out entirely.
  const source_counts: Record<string, number> = {};
  for (const s of selected) source_counts[s] = 0;
  for (const part of parts) {
    for (const [source, count] of Object.entries(part.source_counts ?? {})) {
      if (source in source_counts) source_counts[source] += count;
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
