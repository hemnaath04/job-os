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

/** The shape the endpoint contract asks for. Everything but title and url is optional. */
interface CustomJob {
  id?: string | null;
  title?: string | null;
  url?: string | null;
  company?: string | null;
  company_domain?: string | null;
  location?: string | null;
  country_code?: string | null;
  posted_at?: string | null;
  description?: string | null;
}

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
  if (status === 404) return "endpoint not found";
  if (status >= 500) return "endpoint error (5xx)";
  return `HTTP ${status}`;
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

  let text: string;
  try {
    const res = await fetch(url, {
      method: "POST",
      signal: controller.signal,
      cache: "no-store",
      headers,
      body: JSON.stringify({
        title_keywords: params.titleKeywords,
        location: params.location ?? null,
        country_codes: params.countryCodes,
        max_age_days: params.maxAgeDays ?? null,
        limit: params.limit ?? DEFAULT_LIMIT,
      }),
    });
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

  // A bare array and { results: [...] } are both valid per the contract.
  const rows = Array.isArray(payload)
    ? payload
    : Array.isArray((payload as { results?: unknown })?.results)
      ? ((payload as { results: unknown[] }).results)
      : [];

  const out: DiscoveryResult[] = [];
  for (const row of rows.slice(0, MAX_ITEMS)) {
    if (!row || typeof row !== "object") continue;
    const job = row as CustomJob;
    const title = (job.title ?? "").trim();
    const jobUrl = nonEmpty(job.url);
    if (!title || !jobUrl) continue;
    out.push({
      source: `custom:${cfg.id}`,
      source_label: cfg.name,
      source_id: nonEmpty(job.id) ?? jobUrl,
      source_url: jobUrl,
      title,
      company_name: nonEmpty(job.company),
      company_domain: nonEmpty(job.company_domain),
      location: nonEmpty(job.location),
      country_code: nonEmpty(job.country_code)?.toUpperCase() ?? null,
      posted_at: toIsoDate(job.posted_at),
      description: plainText(job.description),
      technologies: [],
      already_imported: false,
    } satisfies DiscoveryResult);
  }
  return out;
}
