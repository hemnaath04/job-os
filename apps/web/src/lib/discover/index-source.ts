// Indexed job search, served from the crawled `job_postings` table.
//
// This is the read path that replaces the live fan-out in ./no-key-sources.ts.
// It is a NEW module and that file is deliberately untouched, so both paths exist
// side by side and the swap-over is a decision someone makes rather than a
// consequence of merging. See docs/ingest-index.md for the swap steps.
//
// The difference in one line: `discoverNoKey` fetches 85 company boards on every
// search, so its latency is the sum of someone else's; the index answers the same
// question from one Postgres query. Measured p50 against 19,461 crawled postings
// was 22ms for a browse and 83-98ms for a single-keyword search, with the worst
// case (three-phrase alternatives, or free text matched against the body) at
// roughly 215ms. Those are local-Postgres numbers with a warm cache, so treat
// them as a floor. See docs/ingest-index.md for the full table and the method.
//
// It also carries something the live path cannot: honest freshness. A crawled row
// knows when it was first seen and when it was last still listed, so the UI can
// say "first seen 3 weeks ago, still listed 1 hour ago" instead of showing a
// reposted requisition as though it went up today.
//
// SCOPE: this module is types and adapters only. It deliberately does not fetch.
// Wiring `POST /api/v1/index/search` to a client belongs with the swap-over,
// which is step 2 of the integration steps in docs/ingest-index.md and is not
// done on this branch. Nothing imports this file yet, and that is the intent.

import type { DiscoveryResult } from "../types";

export interface IndexSearchRequest {
  /** Phrases; every word of a phrase must appear, and the phrases are alternatives. */
  title_keywords?: string[];
  /** Free text matched against title, company, location and body. */
  query?: string;
  location?: string;
  country_codes?: string[];
  company?: string;
  /** Restrict to certain ATS vendors: greenhouse, lever, ashby, smartrecruiters. */
  sources?: string[];
  remote?: boolean;
  /** Age on the effective date, which is posted_at when known and first_seen_at otherwise. */
  max_age_days?: number;
  /** Stricter: only postings carrying a real published date inside the window. */
  posted_within_days?: number;
  /** Include postings the board has stopped listing, so a closure can be shown. */
  include_inactive?: boolean;
  include_duplicates?: boolean;
  /** Exclude postings whose description has not been fetched yet. */
  require_description?: boolean;
  salary_min?: number;
  limit?: number;
  offset?: number;
  /** Ask for the score components behind each row's position. */
  explain?: boolean;
}

export interface ScoreExplain {
  rank: number;
  retrieve_score: number;
  freshness_weight: number;
  mix_weight: number;
  text_rank_raw: number;
  age_days: number;
  effective_date: string;
  company_rank: number;
  matched_keywords: boolean;
  formula: string;
}

export interface IndexHit {
  id: string;
  source: string;
  source_id: string;
  source_url: string;
  title: string;
  company_name: string;
  company_domain: string | null;
  location: string | null;
  country_code: string | null;
  remote: boolean;
  department: string | null;
  employment_type: string | null;
  salary_min: number | null;
  salary_max: number | null;
  salary_currency: string | null;
  snippet: string;
  /**
   * False when the board's list endpoint carries no body and the extra
   * per-posting fetch has not run. SmartRecruiters is why this exists. When it is
   * false the snippet is provider metadata, not the job description, and the UI
   * must not present it as one.
   */
  description_available: boolean;

  // --- freshness, told honestly -------------------------------------------
  posted_at: string | null;
  /** published | created | updated | first_crawl. */
  posted_at_basis: string;
  /** True when the date was inferred rather than published by the board. */
  posted_at_estimated: boolean;
  /** First time the crawl ever saw this posting. */
  first_seen_at: string;
  /** Most recent crawl that still found it listed. */
  last_seen_at: string;
  active: boolean;
  inactive_since: string | null;
  /** Times the posting vanished from its board and came back. */
  repost_count: number;

  rank: number;
  explain?: ScoreExplain | null;
}

export interface IndexSearchResponse {
  results: IndexHit[];
  total_matched: number;
  /** True when counting stopped at the cap, so total_matched is a floor: render "1000+". */
  total_matched_capped: boolean;
  candidates_considered: number;
  took_ms: number;
  keyword_query: string | null;
}

export interface IndexStats {
  postings_total: number;
  postings_active: number;
  companies_active: number;
  duplicates_marked: number;
  posted_at_estimated: number;
  descriptions_missing: number;
  last_crawl_seen_at: string | null;
  by_source: Record<string, number>;
  tokens: Record<string, Record<string, number>>;
  ranking: Record<string, number>;
}

/**
 * Describe a posting's freshness without overstating it.
 *
 * The reason this function exists rather than a plain date: competitors re-date
 * reposts so an old requisition reads as new, and their users noticed. A crawled
 * date is evidence about when we saw something, so the copy says which fact it is
 * reporting. `posted_at_estimated` means the board gave us a last-modified stamp
 * or nothing at all, and an upper bound is not a posting date.
 */
export function describeFreshness(hit: IndexHit): string {
  const parts: string[] = [];

  if (hit.posted_at && !hit.posted_at_estimated) {
    parts.push(`Posted ${relativeTime(hit.posted_at)}`);
  } else if (hit.posted_at) {
    parts.push(`Posted on or before ${relativeTime(hit.posted_at)} (estimated)`);
  } else {
    parts.push(`First seen ${relativeTime(hit.first_seen_at)}`);
  }

  if (!hit.active) {
    parts.push(
      hit.inactive_since
        ? `no longer listed as of ${relativeTime(hit.inactive_since)}`
        : "no longer listed",
    );
  } else if (hit.posted_at) {
    parts.push(`still listed ${relativeTime(hit.last_seen_at)}`);
  }

  if (hit.repost_count > 0) {
    parts.push(
      `reposted ${hit.repost_count} ${hit.repost_count === 1 ? "time" : "times"}`,
    );
  }

  return parts.join(", ");
}

function relativeTime(iso: string): string {
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return "at an unknown time";
  const minutes = Math.round((Date.now() - then) / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days < 30) return `${days}d ago`;
  const months = Math.round(days / 30);
  if (months < 12) return `${months}mo ago`;
  return `${Math.round(months / 12)}y ago`;
}

/**
 * Adapt an index hit to the shape the existing /jobs UI already renders.
 *
 * Keeps the swap-over cheap: components that take `DiscoveryResult` keep working,
 * and the extra freshness fields ride alongside for the components that want them.
 * `already_imported` is left false here because the index does not know about a
 * user's tracked jobs; the caller annotates it the same way /discovery/search does.
 */
export function toDiscoveryResult(hit: IndexHit): DiscoveryResult & {
  first_seen_at: string;
  last_seen_at: string;
  posted_at_estimated: boolean;
  repost_count: number;
  active: boolean;
} {
  return {
    source: hit.source,
    source_label: sourceLabel(hit.source),
    source_id: hit.source_id,
    source_url: hit.source_url,
    title: hit.title,
    company_name: hit.company_name,
    company_domain: hit.company_domain,
    location: hit.location,
    country_code: hit.country_code,
    posted_at: hit.posted_at,
    description: hit.description_available ? hit.snippet : "",
    technologies: [],
    already_imported: false,
    first_seen_at: hit.first_seen_at,
    last_seen_at: hit.last_seen_at,
    posted_at_estimated: hit.posted_at_estimated,
    repost_count: hit.repost_count,
    active: hit.active,
  };
}

const SOURCE_LABELS: Record<string, string> = {
  greenhouse: "Greenhouse",
  lever: "Lever",
  ashby: "Ashby",
  smartrecruiters: "SmartRecruiters",
};

function sourceLabel(source: string): string {
  return SOURCE_LABELS[source] ?? source;
}
