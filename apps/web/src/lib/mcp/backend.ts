/**
 * MCP tool handlers call the same FastAPI backend the web app's proxy uses,
 * forwarding the caller's verified Clerk OAuth access token as-is.
 *
 * That token is a Clerk-issued JWT with the same `sub` (Clerk user id) a
 * normal session token carries, so job_os.auth.get_current_user resolves it
 * to the same account the user already has on the web app, no backend
 * changes needed.
 */
const API = process.env.API_BASE_URL ?? "http://localhost:8000";

export class BackendError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

/**
 * Read a backend response body that is supposed to be JSON but might not be.
 *
 * `JSON.parse(text)` used to run before the `resp.ok` check, so any non-JSON
 * body took the whole call down with `SyntaxError: Unexpected token '<'`
 * before the status code was ever looked at. Every tool here is a JSON API
 * over another JSON API, but the things BETWEEN them are not: a platform's
 * "Application Error" page for a crashed or sleeping dyno, a CDN's 502/503/504
 * page and a proxy's own timeout page are all HTML, and any of them can appear
 * in front of a perfectly healthy handler. `search_jobs` is the one an agent
 * hits hardest, and a stack trace about a "<" is neither a JSON error nor
 * anything the caller can act on.
 *
 * So the body is parsed defensively and a non-JSON body is reported as what it
 * actually is: the transport failed, with the status that says how.
 */
function readJsonBody(status: number, statusText: string, text: string): unknown {
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    throw new BackendError(
      status >= 400 ? status : 502,
      status >= 400
        ? `The server returned an error page instead of a result (HTTP ${status} ${statusText}). Try again in a moment.`
        : "The server returned a page instead of a result, which usually means it is restarting. Try again in a moment.",
    );
  }
}

export async function callBackend(
  token: string,
  method: "GET" | "POST" | "PATCH" | "DELETE",
  path: string,
  body?: unknown,
): Promise<unknown> {
  const resp = await fetch(`${API}/api/v1${path}`, {
    method,
    headers: {
      authorization: `Bearer ${token}`,
      ...(body !== undefined ? { "content-type": "application/json" } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });

  if (resp.status === 204) return null;

  const text = await resp.text();
  const data = readJsonBody(resp.status, resp.statusText, text);

  if (!resp.ok) {
    const detail =
      (data as { detail?: string } | null)?.detail ??
      (typeof data === "string" ? data : text ? text.slice(0, 300) : resp.statusText);
    throw new BackendError(resp.status, detail);
  }
  return data;
}

/**
 * Like callBackend, but for endpoints that answer with a raw file body
 * (e.g. GET .../download, which streams `application/pdf`) rather than JSON.
 */
export async function callBackendBinary(
  token: string,
  path: string,
): Promise<{ bytes: Uint8Array; contentType: string } | null> {
  const resp = await fetch(`${API}/api/v1${path}`, {
    headers: { authorization: `Bearer ${token}` },
  });
  if (resp.status === 404) return null;
  if (!resp.ok) {
    const text = await resp.text();
    throw new BackendError(resp.status, text || resp.statusText);
  }
  const buf = await resp.arrayBuffer();
  return {
    bytes: new Uint8Array(buf),
    contentType: resp.headers.get("content-type") ?? "application/octet-stream",
  };
}

export async function callBackendMultipart(
  token: string,
  path: string,
  form: FormData,
): Promise<unknown> {
  const resp = await fetch(`${API}/api/v1${path}`, {
    method: "POST",
    headers: { authorization: `Bearer ${token}` },
    body: form,
  });

  const text = await resp.text();
  const data = readJsonBody(resp.status, resp.statusText, text);

  if (!resp.ok) {
    const detail =
      (data as { detail?: string } | null)?.detail ??
      (typeof data === "string" ? data : text ? text.slice(0, 300) : resp.statusText);
    throw new BackendError(resp.status, detail);
  }
  return data;
}

const MAX_FETCHED_FILE_BYTES = 25 * 1024 * 1024; // 25MB — a resume PDF/DOCX is nowhere near this.
const FETCH_TIMEOUT_MS = 20_000;
const BLOCKED_HOSTNAMES = new Set(["localhost", "metadata.google.internal", "169.254.169.254"]);

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
  if (/^f[cd][0-9a-f]{2}:/.test(host) || /^fe[89ab][0-9a-f]:/.test(host)) return true;
  if (host.startsWith("::ffff:")) {
    const ipv4Part = host.substring(7);
    return isPrivateIpv4(ipv4Part);
  }
  return false;
}

/**
 * Deliberate copy of the guard in lib/discover/custom-fetch.ts rather than an
 * import from it — that module keeps its internals private on purpose, and
 * this is a few lines. Same reasoning: https only, no loopback/private/
 * link-local/cloud-metadata target, best-effort (checks the hostname as
 * written, not resolve-then-pin).
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
  if (host.endsWith(".local") || host.endsWith(".internal")) throw new Error("blocked host");
  if (isPrivateIpv4(host) || isPrivateIpv6(host)) throw new Error("blocked host");
  return url;
}

const MAX_REDIRECTS = 3;

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
    current = assertFetchableUrl(new URL(location, current).toString());
  }
}

/**
 * Fetch a resume file the caller already has hosted somewhere (they built it,
 * uploaded it to their own storage, whatever) instead of requiring it be
 * inlined as base64 in the tool call — the same reasoning add_job_from_url
 * uses for job postings: the server does the fetching, so the caller never
 * has to round-trip a large file through the model's own context.
 */
export async function fetchExternalFile(
  rawUrl: string,
): Promise<{ bytes: Uint8Array; contentType: string }> {
  const url = assertFetchableUrl(rawUrl);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
  try {
    const resp = await fetchFollowingValidatedRedirects(url, { signal: controller.signal, cache: "no-store" });
    if (!resp.ok) throw new Error(`could not fetch source_url (HTTP ${resp.status})`);
    const contentLength = resp.headers.get("content-length");
    if (contentLength && Number(contentLength) > MAX_FETCHED_FILE_BYTES) {
      throw new Error("file at source_url is too large (25MB limit)");
    }
    const buf = await resp.arrayBuffer();
    if (buf.byteLength > MAX_FETCHED_FILE_BYTES) {
      throw new Error("file at source_url is too large (25MB limit)");
    }
    return {
      bytes: new Uint8Array(buf),
      contentType: resp.headers.get("content-type") ?? "application/octet-stream",
    };
  } catch (e) {
    const err = e as Error;
    if (err.name === "AbortError") throw new Error(`timed out fetching source_url after ${FETCH_TIMEOUT_MS}ms`);
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

export function toolText(data: unknown) {
  return { content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }] };
}

export function toolError(err: unknown) {
  const message = err instanceof BackendError ? `(${err.status}) ${err.message}` : String(err);
  return { content: [{ type: "text" as const, text: message }], isError: true };
}
