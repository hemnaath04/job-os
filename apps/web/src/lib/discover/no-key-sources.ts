// Key-free job discovery.
//
// Every source here is a public JSON endpoint that needs no API key and no
// account: the three big ATS vendors expose each company's board, and
// Remotive / RemoteOK expose their remote-job feeds. That makes this the
// zero-cost fallback for discovery when TheirStack credits run out.
//
// Everything normalizes to DiscoveryResult so the existing /jobs UI and the
// /discovery/import backend call work unchanged.
//
// Attribution note: Remotive and RemoteOK both require that listings link
// back to their URL and name them as the source. We satisfy that by keeping
// their `url` as source_url and setting source_label to their name; keep it
// that way.

import type { DiscoveryResult, DiscoverySourceError } from "../types";
import { ATS_COMPANIES, type AtsCompany, type AtsProvider } from "./ats-companies";

export type NoKeySource = AtsProvider | "remotive" | "remoteok";

const SOURCE_LABELS: Record<NoKeySource, string> = {
  greenhouse: "Greenhouse",
  lever: "Lever",
  ashby: "Ashby",
  remotive: "Remotive",
  remoteok: "RemoteOK",
};

/** One slow board must never hold up the whole search. */
const DEFAULT_TIMEOUT_MS = 6_000;
const DEFAULT_LIMIT = 60;
/** Per-company cap applied before the global cap, newest first. */
const MAX_PER_COMPANY = 40;
const MAX_DESCRIPTION_CHARS = 6_000;
const MAX_TECHNOLOGIES = 8;
/** Boards are fast (sub-second) but Greenhouse payloads run to megabytes;
 *  a bounded pool keeps peak memory sane on a serverless function. */
const FETCH_CONCURRENCY = 8;

const USER_AGENT =
  "job-os/1.0 (+https://github.com/hemnaath04/job-os) discovery-bot";

// ---------------------------------------------------------------------------
// low-level helpers
// ---------------------------------------------------------------------------

async function fetchJson<T>(
  url: string,
  opts: { timeoutMs?: number; headers?: Record<string, string> } = {},
): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(
    () => controller.abort(),
    opts.timeoutMs ?? DEFAULT_TIMEOUT_MS,
  );
  try {
    const res = await fetch(url, {
      signal: controller.signal,
      cache: "no-store",
      headers: {
        accept: "application/json",
        // RemoteOK 403s a bare fetch; the others are happy either way.
        "user-agent": USER_AGENT,
        ...opts.headers,
      },
    });
    if (!res.ok) throw new Error(`HTTP ${res.status} ${res.statusText}`);
    return (await res.json()) as T;
  } catch (e) {
    const err = e as Error;
    if (err.name === "AbortError" || err.name === "TimeoutError") {
      throw new Error(`timed out after ${opts.timeoutMs ?? DEFAULT_TIMEOUT_MS}ms`);
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

/** Promise.allSettled with a ceiling on how many run at once. */
async function mapPool<T, R>(
  items: T[],
  concurrency: number,
  fn: (item: T) => Promise<R>,
): Promise<PromiseSettledResult<R>[]> {
  const out = new Array<PromiseSettledResult<R>>(items.length);
  let cursor = 0;
  const worker = async (): Promise<void> => {
    for (;;) {
      const i = cursor++;
      if (i >= items.length) return;
      try {
        out[i] = { status: "fulfilled", value: await fn(items[i]) };
      } catch (reason) {
        out[i] = { status: "rejected", reason };
      }
    }
  };
  await Promise.all(
    Array.from({ length: Math.min(concurrency, items.length) }, worker),
  );
  return out;
}

const NAMED_ENTITIES: Record<string, string> = {
  amp: "&",
  lt: "<",
  gt: ">",
  quot: '"',
  apos: "'",
  nbsp: " ",
  ndash: "-",
  mdash: "-",
  hellip: "...",
  rsquo: "'",
  lsquo: "'",
  rdquo: '"',
  ldquo: '"',
  bull: "*",
};

function decodeEntities(input: string): string {
  return input.replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (match, body: string) => {
    if (body.startsWith("#x") || body.startsWith("#X")) {
      const code = Number.parseInt(body.slice(2), 16);
      return Number.isNaN(code) ? match : String.fromCodePoint(code);
    }
    if (body.startsWith("#")) {
      const code = Number.parseInt(body.slice(1), 10);
      return Number.isNaN(code) ? match : String.fromCodePoint(code);
    }
    return NAMED_ENTITIES[body.toLowerCase()] ?? match;
  });
}

function collapseWhitespace(text: string): string {
  return text
    .replace(/\r/g, "")
    .replace(/[ \t ]+/g, " ")
    .replace(/ ?\n ?/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

/**
 * Greenhouse returns `content` as entity-encoded HTML (&lt;p&gt;...), while
 * Lever / Ashby / Remotive / RemoteOK return real HTML. Detect the encoded
 * case and unwrap it once before stripping tags.
 */
function htmlToText(html: string | null | undefined): string {
  if (!html) return "";
  let text = html;
  if (text.includes("&lt;") && !text.includes("<")) text = decodeEntities(text);
  text = text
    .replace(/<(script|style)[\s\S]*?<\/\1>/gi, " ")
    .replace(/<br\s*\/?>/gi, "\n")
    .replace(/<\/(p|div|li|ul|ol|h[1-6]|tr|table|section)>/gi, "\n")
    .replace(/<li[^>]*>/gi, "- ")
    .replace(/<[^>]+>/g, " ");
  return truncate(collapseWhitespace(decodeEntities(text)), MAX_DESCRIPTION_CHARS);
}

function truncate(text: string, max: number): string {
  if (text.length <= max) return text;
  const cut = text.slice(0, max);
  const lastSpace = cut.lastIndexOf(" ");
  return (lastSpace > max * 0.8 ? cut.slice(0, lastSpace) : cut).trimEnd();
}

/** Accepts ISO strings, epoch millis (Lever) and naive UTC stamps (Remotive). */
function toIsoDate(value: unknown): string | null {
  if (value === null || value === undefined) return null;
  let ms: number;
  if (typeof value === "number") {
    ms = value < 1e11 ? value * 1000 : value;
  } else if (typeof value === "string") {
    const trimmed = value.trim();
    if (!trimmed) return null;
    if (/^\d{10}$/.test(trimmed)) ms = Number(trimmed) * 1000;
    else if (/^\d{13}$/.test(trimmed)) ms = Number(trimmed);
    else {
      // "2026-07-24T10:33:35" with no zone: Remotive publishes UTC, so pin it
      // rather than letting the runtime's local zone shift the date.
      const naive = /^\d{4}-\d{2}-\d{2}T[\d:.]+$/.test(trimmed);
      ms = new Date(naive ? `${trimmed}Z` : trimmed).getTime();
    }
  } else {
    return null;
  }
  return Number.isNaN(ms) ? null : new Date(ms).toISOString();
}

const US_STATE_CODES = new Set([
  "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DC", "DE", "FL", "GA", "HI", "IA",
  "ID", "IL", "IN", "KS", "KY", "LA", "MA", "MD", "ME", "MI", "MN", "MO", "MS",
  "MT", "NC", "ND", "NE", "NH", "NJ", "NM", "NV", "NY", "OH", "OK", "OR", "PA",
  "RI", "SC", "SD", "TN", "TX", "UT", "VA", "VT", "WA", "WI", "WV", "WY",
]);

const COUNTRY_BY_NAME: Record<string, string> = {
  "united states of america": "US", "united states": "US", usa: "US", "u.s.": "US",
  "u.s.a.": "US", america: "US",
  // Safe as a word match: location labels say "Remote US" / "US-Remote", and
  // the boundary check keeps "Columbus" or "Belarus" from matching.
  us: "US",
  canada: "CA", "united kingdom": "GB", uk: "GB", england: "GB", scotland: "GB",
  wales: "GB", ireland: "IE", germany: "DE", deutschland: "DE", france: "FR",
  spain: "ES", portugal: "PT", netherlands: "NL", belgium: "BE",
  switzerland: "CH", sweden: "SE", norway: "NO", denmark: "DK", finland: "FI",
  poland: "PL", italy: "IT", austria: "AT", czechia: "CZ", "czech republic": "CZ",
  romania: "RO", greece: "GR", hungary: "HU", bulgaria: "BG", serbia: "RS",
  india: "IN", singapore: "SG", japan: "JP", australia: "AU",
  "new zealand": "NZ", brazil: "BR", mexico: "MX", argentina: "AR",
  chile: "CL", colombia: "CO", peru: "PE", israel: "IL",
  "united arab emirates": "AE", uae: "AE", "south africa": "ZA", china: "CN",
  "hong kong": "HK", taiwan: "TW", "south korea": "KR", philippines: "PH",
  indonesia: "ID", vietnam: "VN", thailand: "TH", malaysia: "MY",
  nigeria: "NG", kenya: "KE", egypt: "EG", turkey: "TR", ukraine: "UA",
};

// Boards routinely give a bare city ("San Francisco Bay Area", "London") with
// no country at all, so a small hint table lifts country_code coverage a lot.
const COUNTRY_BY_CITY: Record<string, string> = {
  "san francisco": "US", "bay area": "US", "new york": "US", "nyc": "US",
  seattle: "US", austin: "US", boston: "US", chicago: "US",
  "los angeles": "US", denver: "US", atlanta: "US", "washington dc": "US",
  miami: "US", "salt lake city": "US", "san diego": "US", portland: "US",
  london: "GB", manchester: "GB", edinburgh: "GB",
  toronto: "CA", vancouver: "CA", montreal: "CA", ottawa: "CA",
  bengaluru: "IN", bangalore: "IN", mumbai: "IN", hyderabad: "IN",
  pune: "IN", chennai: "IN", gurgaon: "IN", gurugram: "IN", noida: "IN",
  "new delhi": "IN",
  berlin: "DE", munich: "DE", hamburg: "DE", paris: "FR", amsterdam: "NL",
  dublin: "IE", madrid: "ES", barcelona: "ES", lisbon: "PT", zurich: "CH",
  stockholm: "SE", copenhagen: "DK", oslo: "NO", helsinki: "FI",
  warsaw: "PL", krakow: "PL", bucharest: "RO", milan: "IT", rome: "IT",
  vienna: "AT", prague: "CZ",
  sydney: "AU", melbourne: "AU", tokyo: "JP", "tel aviv": "IL",
  "sao paulo": "BR", "são paulo": "BR", "mexico city": "MX", bogota: "CO",
};

/** Best-effort ISO-3166 alpha-2 from a free-text location label. */
export function inferCountryCode(location: string | null | undefined): string | null {
  if (!location) return null;
  const text = location.trim();
  if (!text) return null;

  // "San Francisco, CA" / "Austin, TX (Remote)" -> US
  const stateMatch = text.match(/,\s*([A-Z]{2})\b/);
  if (stateMatch && US_STATE_CODES.has(stateMatch[1])) return "US";

  const lower = text.toLowerCase();
  for (const [name, code] of Object.entries(COUNTRY_BY_NAME)) {
    if (wordMatch(lower, name)) return code;
  }
  for (const [city, code] of Object.entries(COUNTRY_BY_CITY)) {
    if (wordMatch(lower, city)) return code;
  }
  return null;
}

function wordMatch(haystack: string, needle: string): boolean {
  return new RegExp(`(^|[^a-z])${escapeRegExp(needle)}([^a-z]|$)`, "i").test(
    haystack,
  );
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function cleanTechnologies(raw: unknown): string[] {
  const list = Array.isArray(raw) ? raw : [];
  const seen = new Set<string>();
  const out: string[] = [];
  for (const item of list) {
    if (typeof item !== "string") continue;
    const tag = item.trim();
    if (!tag || tag.length > 30) continue;
    const key = tag.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(tag);
    if (out.length >= MAX_TECHNOLOGIES) break;
  }
  return out;
}

function nonEmpty(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

// ---------------------------------------------------------------------------
// per-source fetchers
// ---------------------------------------------------------------------------

interface GreenhouseJob {
  id: number;
  title: string;
  absolute_url: string;
  location?: { name?: string } | null;
  offices?: { name?: string }[] | null;
  updated_at?: string | null;
  first_published?: string | null;
  content?: string | null;
}

/**
 * `content=true` returns the full HTML of every posting, which runs from
 * 400KB (Discord) to 9MB (Databricks) per board and 56MB across the curated
 * list. We fetch the light index by default and hydrate descriptions for the
 * handful of postings that survive filtering (see hydrateGreenhouseContent).
 */
export async function fetchGreenhouse(
  company: AtsCompany,
  opts: { timeoutMs?: number; content?: boolean } = {},
): Promise<DiscoveryResult[]> {
  const url =
    `https://boards-api.greenhouse.io/v1/boards/${company.slug}/jobs` +
    (opts.content ? "?content=true" : "");
  const payload = await fetchJson<{ jobs?: GreenhouseJob[] }>(url, opts);
  const jobs = Array.isArray(payload?.jobs) ? payload.jobs : [];

  return jobs.map((job) => {
    const location =
      nonEmpty(job.location?.name) ?? nonEmpty(job.offices?.[0]?.name);
    return {
      source: "greenhouse",
      source_label: SOURCE_LABELS.greenhouse,
      source_id: `${company.slug}:${job.id}`,
      source_url: job.absolute_url,
      title: (job.title ?? "").trim(),
      company_name: company.name,
      company_domain: company.domain,
      location,
      country_code: inferCountryCode(location),
      posted_at: toIsoDate(job.first_published ?? job.updated_at),
      description: htmlToText(job.content),
      technologies: [],
      already_imported: false,
    } satisfies DiscoveryResult;
  });
}

interface LeverPosting {
  id: string;
  text: string;
  hostedUrl?: string | null;
  applyUrl?: string | null;
  createdAt?: number | string | null;
  country?: string | null;
  categories?: {
    location?: string | null;
    team?: string | null;
    commitment?: string | null;
  } | null;
  descriptionPlain?: string | null;
  description?: string | null;
}

/**
 * An unknown slug gets a 404 whose body is `{ok:false,error:"..."}` rather
 * than an array, so guard the shape as well as the status: an empty board and
 * a soft error must not look the same.
 */
export async function fetchLever(
  company: AtsCompany,
  opts: { timeoutMs?: number } = {},
): Promise<DiscoveryResult[]> {
  const url = `https://api.lever.co/v0/postings/${company.slug}?mode=json`;
  const payload = await fetchJson<LeverPosting[] | { error?: string }>(url, opts);
  if (!Array.isArray(payload)) {
    const message =
      typeof payload?.error === "string" ? payload.error : "unexpected payload";
    throw new Error(message);
  }

  return payload.map((job) => {
    const location = nonEmpty(job.categories?.location);
    return {
      source: "lever",
      source_label: SOURCE_LABELS.lever,
      source_id: `${company.slug}:${job.id}`,
      source_url: job.hostedUrl ?? job.applyUrl ?? "",
      title: (job.text ?? "").trim(),
      company_name: company.name,
      company_domain: company.domain,
      location,
      // Lever is the one board that hands us a real ISO country code.
      country_code: nonEmpty(job.country)?.toUpperCase() ?? inferCountryCode(location),
      posted_at: toIsoDate(job.createdAt),
      description: job.descriptionPlain
        ? truncate(collapseWhitespace(job.descriptionPlain), MAX_DESCRIPTION_CHARS)
        : htmlToText(job.description),
      technologies: [],
      already_imported: false,
    } satisfies DiscoveryResult;
  });
}

interface AshbyJob {
  id: string;
  title: string;
  location?: string | null;
  isRemote?: boolean;
  workplaceType?: string | null;
  employmentType?: string | null;
  publishedAt?: string | null;
  jobUrl?: string | null;
  applyUrl?: string | null;
  isListed?: boolean;
  descriptionPlain?: string | null;
  descriptionHtml?: string | null;
  address?: { postalAddress?: { addressCountry?: string | null } | null } | null;
}

export async function fetchAshby(
  company: AtsCompany,
  opts: { timeoutMs?: number } = {},
): Promise<DiscoveryResult[]> {
  const url =
    `https://api.ashbyhq.com/posting-api/job-board/${company.slug}` +
    "?includeCompensation=true";
  const payload = await fetchJson<{ jobs?: AshbyJob[] }>(url, opts);
  const jobs = Array.isArray(payload?.jobs) ? payload.jobs : [];

  return jobs
    .filter((job) => job.isListed !== false)
    .map((job) => {
      // `isRemote` is true on most Ashby postings even for hybrid roles, so
      // workplaceType is the trustworthy signal. Surface it in the location
      // when the label does not already say so, otherwise a remote-only
      // search silently drops these.
      let location = nonEmpty(job.location);
      const remote = (job.workplaceType ?? "").toLowerCase() === "remote";
      if (remote && (!location || !/remote/i.test(location))) {
        location = location ? `Remote - ${location}` : "Remote";
      }
      const countryHint = nonEmpty(job.address?.postalAddress?.addressCountry);
      return {
        source: "ashby",
        source_label: SOURCE_LABELS.ashby,
        source_id: `${company.slug}:${job.id}`,
        source_url: job.jobUrl ?? job.applyUrl ?? "",
        title: (job.title ?? "").trim(),
        company_name: company.name,
        company_domain: company.domain,
        location,
        country_code: inferCountryCode(countryHint) ?? inferCountryCode(location),
        posted_at: toIsoDate(job.publishedAt),
        description: job.descriptionPlain
          ? truncate(collapseWhitespace(job.descriptionPlain), MAX_DESCRIPTION_CHARS)
          : htmlToText(job.descriptionHtml),
        technologies: [],
        already_imported: false,
      } satisfies DiscoveryResult;
    });
}

interface RemotiveJob {
  id: number | string;
  title: string;
  company_name?: string | null;
  url: string;
  category?: string | null;
  tags?: string[] | null;
  job_type?: string | null;
  publication_date?: string | null;
  candidate_required_location?: string | null;
  description?: string | null;
}

export async function fetchRemotive(
  query: string,
  opts: { timeoutMs?: number; limit?: number } = {},
): Promise<DiscoveryResult[]> {
  const params = new URLSearchParams();
  if (query.trim()) params.set("search", query.trim());
  params.set("limit", String(opts.limit ?? 100));
  const payload = await fetchJson<{ jobs?: RemotiveJob[] }>(
    `https://remotive.com/api/remote-jobs?${params.toString()}`,
    opts,
  );
  const jobs = Array.isArray(payload?.jobs) ? payload.jobs : [];

  return jobs.map((job) => {
    const location = nonEmpty(job.candidate_required_location);
    return {
      source: "remotive",
      source_label: SOURCE_LABELS.remotive,
      source_id: String(job.id),
      source_url: job.url,
      title: (job.title ?? "").trim(),
      company_name: nonEmpty(job.company_name),
      company_domain: null,
      location,
      country_code: inferCountryCode(location),
      posted_at: toIsoDate(job.publication_date),
      description: htmlToText(job.description),
      technologies: cleanTechnologies(job.tags),
      already_imported: false,
    } satisfies DiscoveryResult;
  });
}

interface RemoteOkJob {
  id?: string | number;
  slug?: string;
  position?: string;
  company?: string;
  location?: string | null;
  tags?: string[] | null;
  date?: string | null;
  epoch?: number | null;
  url?: string | null;
  apply_url?: string | null;
  description?: string | null;
}

/**
 * The feed's first element is a legal/terms object rather than a job, so
 * filter on shape instead of slicing a fixed index. A User-Agent is
 * mandatory here; without one the endpoint returns 403.
 */
export async function fetchRemoteOK(
  opts: { timeoutMs?: number } = {},
): Promise<DiscoveryResult[]> {
  const payload = await fetchJson<RemoteOkJob[]>("https://remoteok.com/api", opts);
  const jobs = Array.isArray(payload) ? payload : [];

  return jobs
    .filter((job) => Boolean(job && job.id && job.position))
    .map((job) => {
      const location = nonEmpty(job.location) ?? "Remote";
      return {
        source: "remoteok",
        source_label: SOURCE_LABELS.remoteok,
        source_id: String(job.id),
        source_url: job.url ?? job.apply_url ?? "",
        title: (job.position ?? "").trim(),
        company_name: nonEmpty(job.company),
        company_domain: null,
        location,
        country_code: inferCountryCode(location),
        posted_at: toIsoDate(job.date ?? job.epoch),
        description: htmlToText(job.description),
        technologies: cleanTechnologies(job.tags),
        already_imported: false,
      } satisfies DiscoveryResult;
    });
}

/**
 * Fill in descriptions for Greenhouse rows fetched from the light index.
 * One small request per posting (~6KB, ~75ms) beats pulling `content=true`
 * for every board, and it only runs on the results we are about to return.
 */
async function hydrateGreenhouseContent(
  results: DiscoveryResult[],
  opts: { timeoutMs?: number } = {},
): Promise<void> {
  const pending = results.filter(
    (r) => r.source === "greenhouse" && !r.description,
  );
  if (pending.length === 0) return;

  await mapPool(pending, FETCH_CONCURRENCY, async (result) => {
    const [slug, id] = result.source_id.split(":");
    if (!slug || !id) return;
    try {
      const job = await fetchJson<GreenhouseJob>(
        `https://boards-api.greenhouse.io/v1/boards/${slug}/jobs/${id}`,
        opts,
      );
      result.description = htmlToText(job.content);
    } catch {
      // A missing description is not worth failing the search over.
    }
  });
}

// ---------------------------------------------------------------------------
// orchestrator
// ---------------------------------------------------------------------------

export interface DiscoverNoKeyOptions {
  /** Which of the five sources to query. Defaults to all of them. */
  sources?: NoKeySource[];
  /** Case-insensitive substring match against the job title. ANY match wins. */
  titleKeywords?: string[];
  /** Case-insensitive substring match against the location label. */
  location?: string;
  /** ISO-3166 alpha-2 codes matched against the inferred country_code. */
  countryCodes?: string[];
  /** Drop postings older than this many days. Undated postings are kept. */
  maxAgeDays?: number;
  /** When true, keep only remote-friendly postings. False/undefined: no filter. */
  remote?: boolean;
  limit?: number;
  /** Board slugs from ATS_COMPANIES. Defaults to the whole list. */
  companies?: string[];
  /** Skip the remote aggregators and search only the curated ATS boards. */
  includeRemoteBoards?: boolean;
  /** Fetch Greenhouse descriptions for the final result set. Default true. */
  hydrateDescriptions?: boolean;
  timeoutMs?: number;
}

export interface DiscoverNoKeyResponse {
  results: DiscoveryResult[];
  source_counts: Record<NoKeySource, number>;
  errors: DiscoverySourceError[];
}

const REMOTE_PATTERN = /\b(remote|anywhere|worldwide|distributed)\b/i;

function matchesTitle(title: string, keywords: string[]): boolean {
  if (keywords.length === 0) return true;
  const haystack = title.toLowerCase();
  return keywords.some((k) => haystack.includes(k));
}

function isRemoteResult(result: DiscoveryResult): boolean {
  if (result.source === "remotive" || result.source === "remoteok") return true;
  return REMOTE_PATTERN.test(result.location ?? "");
}

/**
 * A hire-from-anywhere posting has no country to infer but is open to every
 * country the user might filter on, so it passes. A posting whose location we
 * simply could not parse ("AMER") does not.
 */
function matchesCountry(result: DiscoveryResult, codes: string[]): boolean {
  if (result.country_code) return codes.includes(result.country_code);
  return /\b(worldwide|anywhere)\b/i.test(result.location ?? "");
}

function newestFirst(a: DiscoveryResult, b: DiscoveryResult): number {
  const at = a.posted_at ? Date.parse(a.posted_at) : 0;
  const bt = b.posted_at ? Date.parse(b.posted_at) : 0;
  return bt - at;
}

/**
 * Two passes: exact URL, then company + title + location. The second pass
 * matters because companies file one opening per location as separate
 * requisitions (Affirm lists "Product Security Engineer II / Remote Canada"
 * twice), and the same role often appears on both an ATS board and a remote
 * aggregator. Callers sort newest-first beforehand so the survivor is fresh.
 */
function dedupe(results: DiscoveryResult[]): DiscoveryResult[] {
  const seen = new Set<string>();
  const out: DiscoveryResult[] = [];
  for (const r of results) {
    const identity = [
      (r.company_domain ?? r.company_name ?? "").toLowerCase(),
      r.title.toLowerCase(),
      (r.location ?? "").toLowerCase(),
    ].join("|");
    const url = r.source_url.toLowerCase();
    if (seen.has(url) || seen.has(identity)) continue;
    seen.add(url);
    seen.add(identity);
    out.push(r);
  }
  return out;
}

/** Take one item from each bucket in turn until `limit` is reached. */
function roundRobin(
  buckets: DiscoveryResult[][],
  limit: number,
): DiscoveryResult[] {
  const picked: DiscoveryResult[] = [];
  for (let round = 0; picked.length < limit; round += 1) {
    let tookAny = false;
    for (const bucket of buckets) {
      if (round >= bucket.length) continue;
      picked.push(bucket[round]);
      tookAny = true;
      if (picked.length >= limit) break;
    }
    if (!tookAny) break;
  }
  return picked;
}

function groupBy(
  results: DiscoveryResult[],
  key: (r: DiscoveryResult) => string,
): DiscoveryResult[][] {
  const groups = new Map<string, DiscoveryResult[]>();
  for (const r of results) {
    const k = key(r);
    const bucket = groups.get(k);
    if (bucket) bucket.push(r);
    else groups.set(k, [r]);
  }
  return [...groups.values()];
}

/**
 * Pick `limit` results by fair share rather than taking the globally newest
 * N, which two things would otherwise ruin:
 *
 *   - the 30 ATS boards repost constantly and bury Remotive/RemoteOK entirely
 *   - one company mid-hiring-spree fills every slot its source gets
 *
 * So we round-robin across sources, and within each source round-robin across
 * companies. Newest still wins inside a company. The caller re-sorts for
 * display, so this only decides *which* postings make the page.
 */
function selectAcrossSources(
  results: DiscoveryResult[],
  limit: number,
): DiscoveryResult[] {
  const perSource = groupBy(results, (r) => r.source).map((rows) => {
    // The aggregators carry one job per company, so grouping them by company
    // would turn every posting into its own bucket and starve everyone else.
    if (rows[0].source === "remotive" || rows[0].source === "remoteok") {
      return [...rows].sort(newestFirst);
    }
    const byCompany = groupBy(rows, (r) => r.company_domain ?? r.company_name ?? "");
    for (const bucket of byCompany) bucket.sort(newestFirst);
    return roundRobin(byCompany, limit);
  });
  return roundRobin(perSource, limit);
}

/**
 * Query every key-free source, normalize, filter and merge.
 *
 * Never throws: a source that 404s, times out or changes shape contributes an
 * entry to `errors` and zero results. Per-provider failures are aggregated so
 * one bad board slug does not produce twenty error rows.
 */
export async function discoverNoKey(
  options: DiscoverNoKeyOptions = {},
): Promise<DiscoverNoKeyResponse> {
  const keywords = (options.titleKeywords ?? [])
    .map((k) => k.trim().toLowerCase())
    .filter(Boolean);
  const locationFilter = (options.location ?? "").trim().toLowerCase();
  const limit = Math.max(1, options.limit ?? DEFAULT_LIMIT);
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const countryCodes = (options.countryCodes ?? [])
    .map((c) => c.trim().toUpperCase())
    .filter(Boolean);
  const cutoff =
    options.maxAgeDays && options.maxAgeDays > 0
      ? Date.now() - options.maxAgeDays * 86_400_000
      : null;

  const enabled = new Set<NoKeySource>(
    options.sources?.length
      ? options.sources
      : ["greenhouse", "lever", "ashby", "remotive", "remoteok"],
  );
  const includeRemoteBoards =
    (options.includeRemoteBoards ?? true) &&
    (enabled.has("remotive") || enabled.has("remoteok"));

  const wanted = options.companies?.length
    ? new Set(options.companies.map((s) => s.toLowerCase()))
    : null;
  const companies = ATS_COMPANIES.filter(
    (c) => enabled.has(c.ats) && (!wanted || wanted.has(c.slug.toLowerCase())),
  );

  const errors: DiscoverySourceError[] = [];
  const failuresByProvider = new Map<NoKeySource, string[]>();
  const collected: DiscoveryResult[] = [];

  const keep = (result: DiscoveryResult): boolean => {
    if (!result.title || !result.source_url) return false;
    if (!matchesTitle(result.title, keywords)) return false;
    if (locationFilter) {
      if (!result.location) return false;
      if (!result.location.toLowerCase().includes(locationFilter)) return false;
    }
    if (countryCodes.length && !matchesCountry(result, countryCodes)) return false;
    // Ashby in particular carries postings first published years ago, so an
    // age filter is what keeps a "last 30 days" search honest. An undated
    // posting is kept: unknown is not the same as old.
    if (cutoff && result.posted_at && Date.parse(result.posted_at) < cutoff) {
      return false;
    }
    if (options.remote === true && !isRemoteResult(result)) return false;
    return true;
  };

  // --- curated ATS boards -------------------------------------------------
  const boardResults = await mapPool(companies, FETCH_CONCURRENCY, (company) => {
    switch (company.ats) {
      case "greenhouse":
        return fetchGreenhouse(company, { timeoutMs });
      case "lever":
        return fetchLever(company, { timeoutMs });
      case "ashby":
        return fetchAshby(company, { timeoutMs });
    }
  });

  boardResults.forEach((settled, i) => {
    const company = companies[i];
    if (settled.status === "rejected") {
      const list = failuresByProvider.get(company.ats) ?? [];
      list.push(`${company.slug}: ${(settled.reason as Error)?.message ?? "failed"}`);
      failuresByProvider.set(company.ats, list);
      return;
    }
    const matched = settled.value.filter(keep).sort(newestFirst);
    collected.push(...matched.slice(0, MAX_PER_COMPANY));
  });

  // --- remote aggregators -------------------------------------------------
  if (includeRemoteBoards) {
    // Remotive's `search` takes a single string, so run one query per keyword
    // (capped) to honour the ANY-keyword semantics the ATS path gets locally.
    const queries = keywords.length ? keywords.slice(0, 3) : [""];
    const [remotive, remoteok] = await Promise.allSettled([
      mapPool(queries, queries.length, (q) =>
        fetchRemotive(q, { timeoutMs, limit: 100 }),
      ),
      fetchRemoteOK({ timeoutMs }),
    ]);

    if (remotive.status === "fulfilled") {
      const failed: string[] = [];
      remotive.value.forEach((settled, i) => {
        if (settled.status === "rejected") {
          failed.push(
            `"${queries[i]}": ${(settled.reason as Error)?.message ?? "failed"}`,
          );
          return;
        }
        collected.push(...settled.value.filter(keep));
      });
      // Only surface an error if every query failed; a partial result is
      // still a useful result.
      if (failed.length === queries.length) {
        failuresByProvider.set("remotive", failed);
      }
    } else {
      failuresByProvider.set("remotive", [
        (remotive.reason as Error)?.message ?? "failed",
      ]);
    }

    if (remoteok.status === "fulfilled") {
      collected.push(...remoteok.value.filter(keep));
    } else {
      failuresByProvider.set("remoteok", [
        (remoteok.reason as Error)?.message ?? "failed",
      ]);
    }
  }

  for (const [source, messages] of failuresByProvider) {
    const shown = messages.slice(0, 3).join("; ");
    const extra = messages.length > 3 ? ` (+${messages.length - 3} more)` : "";
    errors.push({ source, message: `${shown}${extra}` });
  }

  const deduped = dedupe(collected.sort(newestFirst));
  const results = selectAcrossSources(deduped, limit).sort(newestFirst);

  if (options.hydrateDescriptions !== false) {
    await hydrateGreenhouseContent(results, { timeoutMs });
  }

  const source_counts: Record<NoKeySource, number> = {
    greenhouse: 0,
    lever: 0,
    ashby: 0,
    remotive: 0,
    remoteok: 0,
  };
  for (const r of results) {
    const key = r.source as NoKeySource;
    if (key in source_counts) source_counts[key] += 1;
  }

  return { results, source_counts, errors };
}
