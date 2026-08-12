// Key-free job discovery. Unlike /api/backend/* this does not proxy FastAPI:
// it fans out to public ATS boards and remote-job feeds directly from the
// serverless function, so it keeps working when TheirStack credits run out.
//
// Auth is handled by the Clerk middleware in src/middleware.ts (this path is
// not in the public matcher), so anything reaching the handler is signed in.
import { NextRequest, NextResponse } from "next/server";

import {
  discoverNoKey,
  type DiscoverNoKeyOptions,
  type NoKeySource,
} from "@/lib/discover/no-key-sources";

const VALID_SOURCES: NoKeySource[] = [
  "greenhouse",
  "lever",
  "ashby",
  "remotive",
  "remoteok",
  "jsearch",
  "adzuna",
];

// Node runtime: the orchestrator relies on AbortController timeouts across
// ~150 outbound requests (one per curated board, plus the feeds) and parses
// multi-MB JSON payloads. The count is a property of the curated list, so it
// grows with ./discover/ats-companies rather than staying put: the `timings` in
// the response is the number to trust, not this comment.
export const runtime = "nodejs";
export const dynamic = "force-dynamic";
// Worst case is every board timing out at 2.5s in a single concurrent wave,
// plus description hydration when the caller asks for it.
export const maxDuration = 60;

interface DiscoverRequestBody {
  sources?: unknown;
  title_keywords?: unknown;
  location?: unknown;
  country_codes?: unknown;
  max_age_days?: unknown;
  remote?: unknown;
  limit?: unknown;
  companies?: unknown;
  include_remote_boards?: unknown;
  hydrate_descriptions?: unknown;
  keys?: unknown;
  custom_sources?: unknown;
}

function toStringArray(value: unknown): string[] | undefined {
  if (!Array.isArray(value)) return undefined;
  return value.filter((v): v is string => typeof v === "string");
}

/**
 * Credentials for the bring-your-own-key sources. They arrive from the user's
 * localStorage on every request, are handed straight to the provider and are
 * never persisted or logged: keep it that way.
 */
function toKeys(value: unknown): DiscoverNoKeyOptions["keys"] {
  if (!value || typeof value !== "object") return undefined;
  const raw = value as Record<string, unknown>;
  const pick = (name: string): string | undefined =>
    typeof raw[name] === "string" ? (raw[name] as string) : undefined;
  const keys = {
    jsearch: pick("jsearch"),
    adzunaAppId: pick("adzuna_app_id"),
    adzunaAppKey: pick("adzuna_app_key"),
  };
  return keys.jsearch || keys.adzunaAppId || keys.adzunaAppKey
    ? keys
    : undefined;
}

/**
 * Endpoints the user hosts themselves. Same rule as the keys above: they arrive
 * from localStorage on every request, are used once and are never persisted or
 * logged, and that includes the auth header value. A malformed entry is dropped
 * rather than failing the whole search.
 */
function toCustomSources(value: unknown): DiscoverNoKeyOptions["customSources"] {
  if (!Array.isArray(value)) return undefined;
  const out: NonNullable<DiscoverNoKeyOptions["customSources"]> = [];
  for (const entry of value) {
    if (!entry || typeof entry !== "object") continue;
    const raw = entry as Record<string, unknown>;
    const pick = (name: string): string | undefined =>
      typeof raw[name] === "string" ? (raw[name] as string) : undefined;
    const id = pick("id");
    const name = pick("name");
    const url = pick("url");
    if (!id || !name || !url) continue;
    out.push({
      id,
      name,
      url,
      authHeader: pick("auth_header"),
      authValue: pick("auth_value"),
    });
  }
  return out.length > 0 ? out : undefined;
}

function toSources(value: unknown): NoKeySource[] | undefined {
  const list = toStringArray(value);
  if (!list) return undefined;
  return list.filter((s): s is NoKeySource =>
    (VALID_SOURCES as string[]).includes(s),
  );
}

export async function POST(req: NextRequest): Promise<Response> {
  let body: DiscoverRequestBody = {};
  try {
    body = (await req.json()) as DiscoverRequestBody;
  } catch {
    // An empty body is a valid "show me everything recent" search.
  }

  const startedAt = Date.now();
  try {
    const payload = await discoverNoKey({
      sources: toSources(body.sources),
      titleKeywords: toStringArray(body.title_keywords),
      location: typeof body.location === "string" ? body.location : undefined,
      countryCodes: toStringArray(body.country_codes),
      maxAgeDays:
        typeof body.max_age_days === "number" ? body.max_age_days : undefined,
      remote: typeof body.remote === "boolean" ? body.remote : undefined,
      limit: typeof body.limit === "number" ? body.limit : undefined,
      companies: toStringArray(body.companies),
      includeRemoteBoards:
        typeof body.include_remote_boards === "boolean"
          ? body.include_remote_boards
          : undefined,
      // Off unless asked for: it is an extra request per Greenhouse row.
      hydrateDescriptions:
        typeof body.hydrate_descriptions === "boolean"
          ? body.hydrate_descriptions
          : undefined,
      keys: toKeys(body.keys),
      customSources: toCustomSources(body.custom_sources),
    });

    const timings = { ...payload.timings, route_ms: Date.now() - startedAt };
    // One structured line per search, because latency here is a property of how
    // many boards answered and how big they were, and that is only knowable
    // after the fact. Labels are provider/slug, never URLs, so no key can ride
    // along into the log.
    console.log("[discover]", {
      route_ms: timings.route_ms,
      total_ms: timings.total_ms,
      phases: timings.phases,
      requests: timings.requests,
      kb: Math.round(timings.bytes / 1024),
      skipped_kb: Math.round(timings.skipped_bytes / 1024),
      results: payload.results.length,
      oversized: timings.oversized,
      slowest: timings.slowest,
      heaviest: timings.heaviest,
    });
    return NextResponse.json(
      { ...payload, timings },
      { headers: { "cache-control": "no-store" } },
    );
  } catch (e) {
    // discoverNoKey swallows per-source failures, so reaching here means
    // something structural broke.
    const err = e as Error;
    console.error("[discover] failed", err.message);
    return NextResponse.json(
      { detail: `discovery failed: ${err.message}` },
      { status: 500 },
    );
  }
}
