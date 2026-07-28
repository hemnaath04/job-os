// Discovery sources that run on a key the user brings.
//
// JSearch (Google for Jobs, via RapidAPI) and Adzuna both need a credential,
// but unlike TheirStack it is the user's own free-tier key rather than a
// server secret: it arrives in the request body from localStorage, is used
// once, and is never stored or logged. See ./keys for the browser half.
//
// Server-side only, same as ./no-key-sources: never import this from a client
// component. Everything normalizes to DiscoveryResult so the /jobs UI and the
// /discovery/import backend call work unchanged.

import type { DiscoveryResult } from "../types";

/** Both providers answer in one round trip, so they get a little more rope. */
const DEFAULT_TIMEOUT_MS = 8_000;
const DEFAULT_LIMIT = 60;
const MAX_DESCRIPTION_CHARS = 6_000;
/** Adzuna's own ceiling for results_per_page. */
const ADZUNA_MAX_PER_PAGE = 50;

/**
 * Adzuna keys the country into the path and 404s on anything outside this
 * list, so an unsupported filter falls back to the US rather than failing the
 * whole search.
 */
const ADZUNA_COUNTRIES = new Set([
  "gb", "us", "at", "au", "be", "br", "ca", "ch", "de", "es", "fr", "in",
  "it", "mx", "nl", "nz", "pl", "sg", "za",
]);
const ADZUNA_DEFAULT_COUNTRY = "us";

export interface KeyedSourceOptions {
  /** Title keywords, already joined into one string. */
  query: string;
  /** ISO-3166 alpha-2, as typed in the Job Finder. */
  countryCode?: string;
  location?: string;
  /** Maps to the provider's own recency filter. */
  datePostedDays?: number;
  limit?: number;
  timeoutMs?: number;
}

// ---------------------------------------------------------------------------
// low-level helpers
//
// Deliberate copies of the tiny helpers in ./no-key-sources rather than new
// exports from it: that module keeps its internals private, and these are a
// few lines each.
// ---------------------------------------------------------------------------

async function fetchJson<T>(
  url: string,
  opts: { timeoutMs?: number; headers?: Record<string, string> } = {},
): Promise<T> {
  const controller = new AbortController();
  const timeoutMs = opts.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(url, {
      signal: controller.signal,
      cache: "no-store",
      headers: { accept: "application/json", ...opts.headers },
    });
    if (!res.ok) throw new Error(describeStatus(res.status));
    return (await res.json()) as T;
  } catch (e) {
    const err = e as Error;
    if (err.name === "AbortError" || err.name === "TimeoutError") {
      throw new Error(`timed out after ${timeoutMs}ms`);
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

/**
 * The provider name is already on the error row, so these read as
 * "JSEARCH key rejected (401)" in the warning banner. A bad key and an
 * exhausted quota are the two failures the user can actually act on, so they
 * get plain language instead of a bare status.
 */
function describeStatus(status: number): string {
  if (status === 401 || status === 403) return `key rejected (${status})`;
  if (status === 429) return `free-tier quota reached (${status})`;
  return `HTTP ${status}`;
}

function nonEmpty(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function toIsoDate(value: unknown): string | null {
  const raw = nonEmpty(value);
  if (!raw) return null;
  const ms = new Date(raw).getTime();
  return Number.isNaN(ms) ? null : new Date(ms).toISOString();
}

/** Bound the payload size; the caller decides whether to strip markup first. */
function plainText(value: unknown): string {
  const raw = typeof value === "string" ? value.trim() : "";
  if (raw.length <= MAX_DESCRIPTION_CHARS) return raw;
  const cut = raw.slice(0, MAX_DESCRIPTION_CHARS);
  const lastSpace = cut.lastIndexOf(" ");
  return (lastSpace > MAX_DESCRIPTION_CHARS * 0.8
    ? cut.slice(0, lastSpace)
    : cut
  ).trimEnd();
}

/**
 * Adzuna wraps the searched terms in <strong class="highlight"> in both the
 * title and the description, so its text arrives as HTML. React would render
 * those tags as literal characters, so drop them and decode the handful of
 * entities the feed actually emits.
 */
function stripHtml(value: unknown): string {
  const raw = typeof value === "string" ? value : "";
  return raw
    .replace(/<[^>]*>/g, "")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;|&apos;/g, "'")
    .replace(/&hellip;/g, "...")
    .replace(/&nbsp;/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

/** JSearch is the only source here that hands us a company website. */
function hostnameOf(value: unknown): string | null {
  const raw = nonEmpty(value);
  if (!raw) return null;
  try {
    return new URL(raw).hostname.replace(/^www\./, "") || null;
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// JSearch (RapidAPI)
// ---------------------------------------------------------------------------

interface JSearchJob {
  job_id?: string;
  job_title?: string;
  employer_name?: string | null;
  employer_website?: string | null;
  job_apply_link?: string;
  job_description?: string | null;
  job_posted_at_datetime_utc?: string | null;
  job_city?: string | null;
  job_state?: string | null;
  job_country?: string | null;
  job_location?: string | null;
}

/** JSearch takes a coarse bucket rather than a day count. */
function jsearchDatePosted(days: number | undefined): string {
  if (days === undefined) return "all";
  if (days <= 1) return "today";
  if (days <= 3) return "3days";
  if (days <= 7) return "week";
  if (days <= 31) return "month";
  return "all";
}

/**
 * JSearch reads the location out of the query string ("react developer in
 * boston"), so there is no separate location parameter to set.
 */
export async function fetchJSearch(
  key: string,
  opts: KeyedSourceOptions,
): Promise<DiscoveryResult[]> {
  const query = [opts.query.trim(), (opts.location ?? "").trim()]
    .filter(Boolean)
    .join(" in ");
  const params = new URLSearchParams({
    query,
    page: "1",
    num_pages: "1",
    date_posted: jsearchDatePosted(opts.datePostedDays),
  });
  const country = (opts.countryCode ?? "").trim().toLowerCase();
  if (country) params.set("country", country);

  const payload = await fetchJson<{ data?: JSearchJob[] }>(
    `https://jsearch.p.rapidapi.com/search?${params.toString()}`,
    {
      timeoutMs: opts.timeoutMs,
      headers: {
        "X-RapidAPI-Key": key,
        "X-RapidAPI-Host": "jsearch.p.rapidapi.com",
      },
    },
  );
  const jobs = Array.isArray(payload?.data) ? payload.data : [];

  return jobs.slice(0, opts.limit ?? DEFAULT_LIMIT).map((job) => {
    const location =
      nonEmpty(job.job_location) ??
      nonEmpty([job.job_city, job.job_state].filter(Boolean).join(", "));
    return {
      source: "jsearch",
      source_label: "JSearch",
      source_id: nonEmpty(job.job_id) ?? job.job_apply_link ?? "",
      source_url: job.job_apply_link ?? "",
      title: (job.job_title ?? "").trim(),
      company_name: nonEmpty(job.employer_name),
      company_domain: hostnameOf(job.employer_website),
      location,
      country_code: nonEmpty(job.job_country)?.toUpperCase() ?? null,
      posted_at: toIsoDate(job.job_posted_at_datetime_utc),
      description: plainText(job.job_description),
      technologies: [],
      already_imported: false,
    } satisfies DiscoveryResult;
  });
}

// ---------------------------------------------------------------------------
// Adzuna
// ---------------------------------------------------------------------------

interface AdzunaJob {
  id?: string | number;
  title?: string;
  redirect_url?: string;
  description?: string | null;
  created?: string | null;
  company?: { display_name?: string | null } | null;
  location?: { display_name?: string | null; area?: string[] | null } | null;
}

export async function fetchAdzuna(
  appId: string,
  appKey: string,
  opts: KeyedSourceOptions,
): Promise<DiscoveryResult[]> {
  const requested = (opts.countryCode ?? "").trim().toLowerCase();
  const country = ADZUNA_COUNTRIES.has(requested)
    ? requested
    : ADZUNA_DEFAULT_COUNTRY;

  const params = new URLSearchParams({
    app_id: appId,
    app_key: appKey,
    results_per_page: String(
      Math.min(opts.limit ?? DEFAULT_LIMIT, ADZUNA_MAX_PER_PAGE),
    ),
    sort_by: "date",
    "content-type": "application/json",
  });
  const what = opts.query.trim();
  if (what) params.set("what", what);
  const where = (opts.location ?? "").trim();
  if (where) params.set("where", where);
  if (opts.datePostedDays && opts.datePostedDays > 0) {
    params.set("max_days_old", String(Math.round(opts.datePostedDays)));
  }

  const payload = await fetchJson<{ results?: AdzunaJob[] }>(
    `https://api.adzuna.com/v1/api/jobs/${country}/search/1?${params.toString()}`,
    { timeoutMs: opts.timeoutMs },
  );
  const jobs = Array.isArray(payload?.results) ? payload.results : [];

  return jobs.map((job) => {
    return {
      source: "adzuna",
      source_label: "Adzuna",
      source_id: String(job.id ?? job.redirect_url ?? ""),
      source_url: job.redirect_url ?? "",
      title: stripHtml(job.title),
      company_name: nonEmpty(job.company?.display_name),
      // Adzuna publishes no website or logo for the hiring company.
      company_domain: null,
      location: nonEmpty(job.location?.display_name),
      // The feed carries no country field: the country is the one we queried.
      country_code: country.toUpperCase(),
      posted_at: toIsoDate(job.created),
      description: plainText(stripHtml(job.description)),
      technologies: [],
      already_imported: false,
    } satisfies DiscoveryResult;
  });
}
