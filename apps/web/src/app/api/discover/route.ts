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
// ~30 outbound requests and parses multi-hundred-KB JSON payloads.
export const runtime = "nodejs";
export const dynamic = "force-dynamic";
// Worst case is a few waves of 6s timeouts plus description hydration.
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
  keys?: unknown;
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
      keys: toKeys(body.keys),
    });
    return NextResponse.json(payload, {
      headers: { "cache-control": "no-store" },
    });
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
