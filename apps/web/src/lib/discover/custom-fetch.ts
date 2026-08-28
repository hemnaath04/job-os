// The fetcher behind a user's own job-feed endpoint.
//
// job.os POSTs the search filters to an HTTPS URL the user hosts and normalizes
// whatever it answers with. It never reaches the job sites itself: the endpoint
// does, on the user's own infrastructure, under the acceptance recorded in
// ./custom-sources.
//
// Server-side only, same as ./no-key-sources and ./keyed-sources: never import
// this from a client component. The definitions arrive in the request body from
// the browser's localStorage, are used once, and are never stored or logged.

import type { DiscoveryResult } from "../types";

const DEFAULT_TIMEOUT_MS = 15_000;
const DEFAULT_LIMIT = 60;
const MAX_DESCRIPTION_CHARS = 6_000;
/** A runaway endpoint must not be able to fill the function's heap. */
const MAX_RESPONSE_CHARS = 2_000_000;
/** However much the endpoint returns, only this many rows are mapped. */
const MAX_ITEMS = 200;

const USER_AGENT = "job-os-custom-source/1.0";

export interface CustomFetchInput {
  id: string;
  name: string;
  url: string;
  authHeader?: string;
  authValue?: string;
}

export interface CustomSearchParams {
  titleKeywords: string[];
  location?: string;
  countryCodes: string[];
  maxAgeDays?: number;
  limit?: number;
}

// There is deliberately no interface for an incoming job here any more. A fixed
// shape was the thing standing between this feature and the endpoints people
// actually own; the field names are recognised by alias at read time instead.

// ---------------------------------------------------------------------------
// low-level helpers
//
// Deliberate copies of the tiny helpers in ./keyed-sources rather than new
// exports from it: those modules keep their internals private, and these are a
// few lines each.
// ---------------------------------------------------------------------------

function nonEmpty(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function toIsoDate(value: unknown): string | null {
  const raw = nonEmpty(value);
  if (!raw) return null;
  const ms = new Date(raw).getTime();
  return Number.isNaN(ms) ? null : new Date(ms).toISOString();
}

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
 * The endpoint is the user's own, so a status is about their deployment rather
 * than about job.os. Say which half is at fault in plain language.
 */
function describeStatus(status: number): string {
  if (status === 401 || status === 403) return "endpoint rejected the request";
  if (status === 404) return "endpoint not found, check the path";
  // The most common way a working endpoint still fails here: it was written to
  // answer GET, and job.os posts the filters as a JSON body. Worth naming
  // outright, because every other clue points at the URL or the key instead.
  if (status === 405) {
    return "endpoint does not accept POST, and job.os posts the filters as JSON";
  }
  if (status === 400 || status === 422) {
    return "endpoint rejected the request body, check the endpoint contract";
  }
  if (status === 429) return "endpoint rate-limited the request";
  if (status >= 500) return `endpoint error (HTTP ${status})`;
  return `HTTP ${status}`;
}

// ---------------------------------------------------------------------------
// Universal shape adapter
//
// The documented contract asks for POST, a `results` array and a fixed set of
// field names. That only ever suited an endpoint written for job.os. People
// point this at whatever they already have: a GET route, a third-party API, a
// scraper someone else built. So rather than reject those, recognise them.
//
// Two problems to solve, and both are pattern matching rather than
// configuration, because a field-mapping UI is a form nobody wants to fill in
// to try a URL once.
//   1. Where is the list? Providers nest it under results, data, jobs, hits,
//      and sometimes two levels down.
//   2. What are the fields called? job_title, jobTitle, position and name all
//      mean title.
// ---------------------------------------------------------------------------

/** Keys that commonly hold the array of postings, tried in this order. */
const ROW_KEYS = [
  "results", "data", "jobs", "items", "hits", "records", "postings",
  "positions", "listings", "docs", "elements", "content", "vacancies",
];

// Compared after normalising to lowercase alphanumerics, so job_title,
// jobTitle, JobTitle and "job title" all collapse to jobtitle.
const TITLE_KEYS = ["title", "jobtitle", "position", "role", "name", "headline", "vacancyname"];
const URL_KEYS = [
  "url", "joburl", "applyurl", "applylink", "jobapplylink", "absoluteurl",
  "link", "redirecturl", "permalink", "href", "detailsurl", "canonicalurl",
];
const COMPANY_KEYS = [
  "company", "companyname", "employer", "employername", "organization",
  "organisation", "hiringorganization", "org", "brand", "accountname",
];
const DOMAIN_KEYS = ["companydomain", "domain", "companywebsite", "employerwebsite", "website"];
const LOCATION_KEYS = [
  "location", "joblocation", "candidaterequiredlocation", "city", "area",
  "region", "place", "locationname", "formattedlocation",
];
const COUNTRY_KEYS = ["countrycode", "country", "jobcountry"];
const DATE_KEYS = [
  "postedat", "dateposted", "publicationdate", "publishedat", "pubdate",
  "createdat", "created", "listedat", "firstpublished", "postingdate",
  "updatedat", "jobpostedat",
];
const DESC_KEYS = [
  "description", "jobdescription", "snippet", "summary", "contents", "content",
  "abstract", "body", "text", "jobsummary",
];
const ID_KEYS = ["id", "jobid", "slug", "guid", "reference", "ref", "uuid", "externalid"];

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

function normalizeKey(key: string): string {
  return key.toLowerCase().replace(/[^a-z0-9]/g, "");
}

/** A row's own keys, normalised once so every lookup below is a map hit. */
function keyIndex(row: Record<string, unknown>): Map<string, unknown> {
  const index = new Map<string, unknown>();
  for (const [key, value] of Object.entries(row)) {
    const norm = normalizeKey(key);
    // First writer wins: `title` should beat a later `seoTitle`.
    if (!index.has(norm)) index.set(norm, value);
  }
  return index;
}

/**
 * First candidate key that holds usable text.
 *
 * Unwraps one level of object or array, because providers routinely nest what
 * you want: `company: { name }`, `location: { city }`, `locations: [{ name }]`.
 */
function pickText(index: Map<string, unknown>, keys: string[]): string | null {
  for (const key of keys) {
    const found = unwrapText(index.get(key));
    if (found) return found;
  }
  return null;
}

function unwrapText(value: unknown, depth = 0): string | null {
  if (typeof value === "string") return value.trim() || null;
  if (typeof value === "number") return String(value);
  if (depth > 1) return null;
  if (Array.isArray(value)) {
    for (const item of value) {
      const found = unwrapText(item, depth + 1);
      if (found) return found;
    }
    return null;
  }
  if (isRecord(value)) {
    for (const key of ["name", "title", "label", "value", "text", "display", "city"]) {
      const found = unwrapText(value[normalizeKey(key)] ?? value[key], depth + 1);
      if (found) return found;
    }
  }
  return null;
}

/** Enough of a job to be worth showing: something to read and somewhere to go. */
function looksLikeJob(row: Record<string, unknown>): boolean {
  const index = keyIndex(row);
  return Boolean(pickText(index, TITLE_KEYS)) && Boolean(pickText(index, URL_KEYS));
}

/**
 * Find the postings anywhere in the response.
 *
 * Depth-limited and preference-ordered rather than "first array wins", because
 * responses often carry a facets or filters array before the results, and
 * grabbing that would silently return nothing useful.
 */
function findRows(payload: unknown, depth = 0): Record<string, unknown>[] {
  if (depth > 4) return [];

  if (Array.isArray(payload)) {
    const rows = payload.filter(isRecord);
    return rows.some(looksLikeJob) ? rows : [];
  }
  if (!isRecord(payload)) return [];

  for (const key of ROW_KEYS) {
    const direct = payload[key] ?? payload[normalizeKey(key)];
    const found = findRows(direct, depth + 1);
    if (found.length) return found;
  }
  for (const value of Object.values(payload)) {
    const found = findRows(value, depth + 1);
    if (found.length) return found;
  }
  return [];
}

// ---------------------------------------------------------------------------
// SSRF guards
// ---------------------------------------------------------------------------

const BLOCKED_HOSTNAMES = new Set([
  "localhost",
  "metadata.google.internal",
  "169.254.169.254",
]);

/**
 * Keep a user-supplied URL from turning this serverless function into a probe
 * of the private network it runs in: https only, and no loopback, private,
 * link-local or cloud-metadata target.
 *
 * Best-effort by design. It checks the hostname as written, so it does not
 * cover DNS rebinding or a public name that resolves to 10.0.0.1. Closing that
 * needs resolve-then-pin (look the name up, validate every address, then
 * connect to the pinned IP), which is out of scope here.
 */
function assertFetchableUrl(raw: string): URL {
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    throw new Error("must be an https URL");
  }
  if (url.protocol !== "https:") throw new Error("must be an https URL");

  const host = url.hostname.toLowerCase().replace(/^\[|\]$/g, "");
  if (BLOCKED_HOSTNAMES.has(host)) throw new Error("blocked host");
  if (host.endsWith(".local") || host.endsWith(".internal")) {
    throw new Error("blocked host");
  }
  if (isPrivateIpv4(host) || isPrivateIpv6(host)) throw new Error("blocked host");
  return url;
}

function isPrivateIpv4(host: string): boolean {
  const parts = host.split(".");
  if (parts.length !== 4) return false;
  const octets = parts.map((p) => Number(p));
  if (octets.some((n) => !Number.isInteger(n) || n < 0 || n > 255)) return false;
  const [a, b] = octets;
  if (a === 0 || a === 127 || a === 10) return true;
  if (a === 169 && b === 254) return true;
  if (a === 192 && b === 168) return true;
  if (a === 172 && b >= 16 && b <= 31) return true;
  return false;
}

function isPrivateIpv6(host: string): boolean {
  if (host === "::1" || host === "::") return true;
  // fc00::/7 is the unique-local range; fe80::/10 is link-local.
  return /^f[cd][0-9a-f]{2}:/.test(host) || /^fe[89ab][0-9a-f]:/.test(host);
}

// ---------------------------------------------------------------------------
// fetcher
// ---------------------------------------------------------------------------

/** How many hops a custom endpoint may redirect through before we give up. */
const MAX_REDIRECTS = 3;

/**
 * `fetch`, with every hop of a redirect chain validated the way the first URL was.
 *
 * `assertFetchableUrl` checks the URL *as written*, but fetch defaults to
 * `redirect: "follow"`, so it only ever governed the first hop. A custom source
 * at an allowed https host could answer `302 Location: http://169.254.169.254/…`
 * (or a private address, or plain http) and undici would follow it without
 * re-checking anything — which hands back exactly the private-network probe the
 * check exists to prevent, and needs only an HTTP redirect rather than the DNS
 * control the docstring above scopes out.
 *
 * So: follow redirects manually and re-validate each `Location`. Legitimate
 * feeds that redirect keep working; a redirect into the private network does not.
 * Re-issuing the same method and body on each hop is deliberate — these are all
 * requests the caller already authorised to the same logical endpoint, and a
 * 303's GET semantics are not worth diverging for when the alternative is
 * silently dropping the query.
 */
async function fetchFollowingValidatedRedirects(
  start: URL,
  init: RequestInit,
): Promise<Response> {
  let current = start;
  for (let hop = 0; ; hop++) {
    const res = await fetch(current, { ...init, redirect: "manual" });
    if (res.status < 300 || res.status >= 400) return res;
    const location = res.headers.get("location");
    if (!location) return res;
    if (hop >= MAX_REDIRECTS) throw new Error("too many redirects");
    // Resolved against the current URL so a relative Location works, then put
    // through the same gate the original URL passed.
    current = assertFetchableUrl(new URL(location, current).toString());
  }
}

/**
 * Query one custom endpoint and normalize its answer.
 *
 * The filters go out with the request so an endpoint can narrow server-side,
 * but nothing depends on it honouring them: the caller re-applies the same
 * filters it applies to the ATS boards, so a source may return a whole board
 * and let job.os do the narrowing.
 */
export async function fetchCustomSource(
  cfg: CustomFetchInput,
  params: CustomSearchParams,
  opts: { timeoutMs?: number } = {},
): Promise<DiscoveryResult[]> {
  const url = assertFetchableUrl(cfg.url);
  const timeoutMs = opts.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  const headers: Record<string, string> = {
    accept: "application/json",
    "content-type": "application/json",
    "user-agent": USER_AGENT,
  };
  const authHeader = nonEmpty(cfg.authHeader);
  const authValue = nonEmpty(cfg.authValue);
  // A value with nowhere to go used to be dropped here without a word, so the
  // request went out unauthenticated and the endpoint answered 404 or 401. The
  // user then sees "endpoint not found" while looking at a filled-in secret,
  // which points at the wrong problem entirely.
  if (authValue && !authHeader) {
    throw new Error(
      "auth value set but no auth header name, so the key cannot be sent",
    );
  }
  if (authHeader) headers[authHeader] = authValue ?? "";

  const limit = params.limit ?? DEFAULT_LIMIT;
  const body = JSON.stringify({
    title_keywords: params.titleKeywords,
    location: params.location ?? null,
    country_codes: params.countryCodes,
    max_age_days: params.maxAgeDays ?? null,
    limit,
  });

  // The same filters as a query string, for an endpoint that only reads GET.
  // `q` is sent alongside the canonical name because it is what most existing
  // job APIs call the search term, and an endpoint that ignores it loses
  // nothing by receiving it.
  const getUrl = new URL(url.toString());
  if (params.titleKeywords.length) {
    getUrl.searchParams.set("q", params.titleKeywords.join(" "));
    getUrl.searchParams.set("title_keywords", params.titleKeywords.join(","));
  }
  if (params.location) getUrl.searchParams.set("location", params.location);
  if (params.countryCodes.length) {
    getUrl.searchParams.set("country_codes", params.countryCodes.join(","));
  }
  if (params.maxAgeDays) {
    getUrl.searchParams.set("max_age_days", String(params.maxAgeDays));
  }
  getUrl.searchParams.set("limit", String(limit));

  let text: string;
  try {
    let res = await fetchFollowingValidatedRedirects(url, {
      method: "POST",
      signal: controller.signal,
      cache: "no-store",
      headers,
      body,
    });
    // 405 says the route exists and refuses the verb, which is the single most
    // common shape for an endpoint that was not written for job.os. Retry it
    // the way it wants to be called rather than reporting a failure the user
    // would have to read the contract to understand.
    if (res.status === 405 || res.status === 501) {
      res = await fetchFollowingValidatedRedirects(getUrl, {
        method: "GET",
        signal: controller.signal,
        cache: "no-store",
        headers,
      });
    }
    if (!res.ok) throw new Error(describeStatus(res.status));
    text = await res.text();
  } catch (e) {
    const err = e as Error;
    if (err.name === "AbortError" || err.name === "TimeoutError") {
      throw new Error(`timed out after ${timeoutMs}ms`);
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }

  if (text.length > MAX_RESPONSE_CHARS) throw new Error("response too large");

  let payload: unknown;
  try {
    payload = JSON.parse(text) as unknown;
  } catch {
    throw new Error("endpoint did not return JSON");
  }

  return mapJobsPayload(payload, {
    source: `custom:${cfg.id}`,
    sourceLabel: cfg.name,
  });
}

/**
 * Turn any JSON payload into DiscoveryResults, without being told its shape.
 *
 * Exported because this is the interesting half of the custom-source work and it
 * generalises: a board-wide job feed poses exactly the same problem as a
 * user-supplied endpoint, which is an unknown wrapper key and unknown field
 * names. Sharing it means a new feed is a line of config rather than a new
 * parser, and it inherits every alias and nesting case already handled here.
 */
export function mapJobsPayload(
  payload: unknown,
  opts: { source: string; sourceLabel: string | null },
): DiscoveryResult[] {
  const rows = findRows(payload);
  if (!rows.length) {
    // Distinguishing "nothing matched your filters" from "I could not read
    // this" matters: the first is a search result, the second is a setup
    // problem, and they need opposite responses from the user.
    throw new Error(
      "no job list found in the response, expected an array of objects with a title and a url",
    );
  }

  const out: DiscoveryResult[] = [];
  for (const row of rows.slice(0, MAX_ITEMS)) {
    const index = keyIndex(row);
    const title = pickText(index, TITLE_KEYS);
    const jobUrl = pickText(index, URL_KEYS);
    // A posting with nothing to click is not something we can hand to the user,
    // and one with no title is not something they could read.
    if (!title || !jobUrl) continue;

    const country = pickText(index, COUNTRY_KEYS);
    out.push({
      source: opts.source,
      source_label: opts.sourceLabel,
      source_id: pickText(index, ID_KEYS) ?? jobUrl,
      source_url: jobUrl,
      title,
      company_name: pickText(index, COMPANY_KEYS),
      company_domain: pickText(index, DOMAIN_KEYS),
      location: pickText(index, LOCATION_KEYS),
      // Only a 2-letter code is a country code. Providers put "United States"
      // in the same field, and passing that through breaks the country filter
      // silently instead of just leaving it unknown.
      country_code:
        country && country.trim().length === 2 ? country.trim().toUpperCase() : null,
      posted_at: toIsoDate(pickText(index, DATE_KEYS)),
      description: plainText(pickText(index, DESC_KEYS) ?? ""),
      technologies: [],
      already_imported: false,
    } satisfies DiscoveryResult);
  }
  return out;
}
