// Catch-all proxy: client calls /api/backend/<path> → forward to FastAPI at
// API_BASE_URL/api/v1/<path> with the signed-in user's Clerk JWT injected as
// a Bearer token. Keeps tokens out of the client.
//
// Builds a fresh Headers object (only forwards the few headers the upstream
// actually needs) — copying req.headers wholesale brings along Next.js /
// middleware internals that interact badly with undici's fetch on Node 22+
// and can cause the Authorization header to be silently dropped.
import { auth } from "@clerk/nextjs/server";
import { NextRequest, NextResponse } from "next/server";

const API = process.env.API_BASE_URL ?? "http://localhost:8000";

// Resume upload calls Claude through the Zavora gateway and can take 30–60s.
// Default Hobby/Fluid maxDuration is conservative; pin it long so PDF
// extraction doesn't get killed mid-flight.
export const maxDuration = 300;
// Force Node runtime — Edge doesn't support arbitrary outbound HTTP to
// non-HTTPS hosts and our upstream is over a public IP at :8000 via Render.
export const runtime = "nodejs";
// We're a proxy; no caching, ever.
export const dynamic = "force-dynamic";

const FORWARD_HEADERS = [
  "content-type",
  "accept",
  "accept-encoding",
  "user-agent",
];

async function proxy(
  req: NextRequest,
  ctx: { params: Promise<{ path: string[] }> },
): Promise<Response> {
  const t0 = Date.now();
  const { path } = await ctx.params;
  const upstream =
    `${API}/api/v1/${(path ?? []).join("/")}${req.nextUrl.search}`;

  const { userId, sessionId, getToken } = await auth();
  const token = await getToken();
  if (!userId || !token) {
    return NextResponse.json({ detail: "not authenticated" }, { status: 401 });
  }

  const headers = new Headers();
  for (const name of FORWARD_HEADERS) {
    const v = req.headers.get(name);
    if (v) headers.set(name, v);
  }
  headers.set("authorization", `Bearer ${token}`);

  const init: RequestInit = {
    method: req.method,
    headers,
    redirect: "manual",
  };
  if (!["GET", "HEAD"].includes(req.method)) {
    init.body = await req.arrayBuffer();
    (init as RequestInit & { duplex?: string }).duplex = "half";
  }

  let resp: Response;
  try {
    resp = await fetch(upstream, init);
  } catch (e) {
    const ms = Date.now() - t0;
    console.error("[proxy] fetch threw", {
      upstream,
      ms,
      method: req.method,
      error: (e as Error).message,
    });
    return NextResponse.json(
      { detail: `upstream unreachable: ${(e as Error).message}` },
      { status: 502 },
    );
  }

  // Buffer the response body before sending it on. Streaming resp.body
  // directly into a new Response is unreliable on Vercel's serverless
  // runtime — when the function tears down (or the upstream sends
  // chunked encoding the platform doesn't like), the client sees a 500
  // even though the upstream returned a clean 200.
  let body: ArrayBuffer;
  try {
    body = await resp.arrayBuffer();
  } catch (e) {
    const ms = Date.now() - t0;
    console.error("[proxy] body read threw", {
      upstream,
      ms,
      upstreamStatus: resp.status,
      error: (e as Error).message,
    });
    return NextResponse.json(
      { detail: `upstream body read failed: ${(e as Error).message}` },
      { status: 502 },
    );
  }

  const ms = Date.now() - t0;
  if (!resp.ok) {
    console.warn("[proxy] upstream non-2xx", {
      upstream,
      method: req.method,
      status: resp.status,
      ms,
      bodyPreview: new TextDecoder().decode(body.slice(0, 300)),
    });
  } else {
    console.log("[proxy]", {
      upstream,
      method: req.method,
      status: resp.status,
      ms,
      bodyBytes: body.byteLength,
    });
  }

  const outHeaders = new Headers();
  // Only forward headers the browser actually needs. Strips
  // transfer-encoding / connection / etc which can confuse fetch.
  const ct = resp.headers.get("content-type");
  if (ct) outHeaders.set("content-type", ct);
  const cd = resp.headers.get("content-disposition");
  if (cd) outHeaders.set("content-disposition", cd);
  // Help Vercel/Next not cache this.
  outHeaders.set("cache-control", "no-store");

  return new Response(body, { status: resp.status, headers: outHeaders });
}

export const GET = proxy;
export const POST = proxy;
export const PATCH = proxy;
export const PUT = proxy;
export const DELETE = proxy;
