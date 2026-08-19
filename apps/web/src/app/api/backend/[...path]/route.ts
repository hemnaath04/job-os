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
// non-HTTPS hosts, and our upstream isn't guaranteed HTTPS in every deployment.
export const runtime = "nodejs";
// We're a proxy; no caching, ever.
export const dynamic = "force-dynamic";

const FORWARD_HEADERS = [
  "content-type",
  "accept",
  "user-agent",
];

async function proxy(
  req: NextRequest,
  ctx: { params: Promise<{ path: string[] }> },
): Promise<Response> {
  try {
    return await proxyInner(req, ctx);
  } catch (e) {
    // Any uncaught throw lands here — log it loudly so it shows in Vercel
    // runtime logs instead of bubbling to a bare 500.
    const err = e as Error;
    console.error("[proxy] UNCAUGHT", {
      name: err.name,
      message: err.message,
      stack: err.stack?.split("\n").slice(0, 5).join(" | "),
    });
    return NextResponse.json(
      { detail: `proxy crashed: ${err.name}: ${err.message}` },
      { status: 500 },
    );
  }
}

async function proxyInner(
  req: NextRequest,
  ctx: { params: Promise<{ path: string[] }> },
): Promise<Response> {
  const t0 = Date.now();
  const { path } = await ctx.params;
  const pathStr = (path ?? []).join("/");
  // FastAPI exposes health at the root, outside the authenticated /api/v1
  // routers. Keeping this path token-free makes cold-start wakeups cheap.
  const isHealthCheck =
    req.method === "GET" && (pathStr === "health" || pathStr === "health/ready");
  const upstreamPath = isHealthCheck ? `/${pathStr}` : `/api/v1/${pathStr}`;
  const upstream = `${API}${upstreamPath}${req.nextUrl.search}`;

  let token: string | null = null;
  if (!isHealthCheck) {
    const authState = await auth();
    token = await authState.getToken();
    if (!authState.userId || !token) {
      return NextResponse.json({ detail: "not authenticated" }, { status: 401 });
    }
  }

  const headers = new Headers();
  for (const name of FORWARD_HEADERS) {
    const v = req.headers.get(name);
    if (v) headers.set(name, v);
  }
  if (token) headers.set("authorization", `Bearer ${token}`);

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
  } else if (ms > 1_000) {
    // Successful fast requests do not need a serverless log entry. Keep only
    // slow responses so production logs remain useful and inexpensive.
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

  // WHATWG fetch forbids a body on null-body statuses (204 / 205 / 304).
  // Our backend's DELETE handlers return 204 No Content; even an empty
  // ArrayBuffer trips the Response constructor's spec check ("Invalid
  // response status code 204"). Pass null in those cases.
  const nullBodyStatus = resp.status === 204 || resp.status === 205 || resp.status === 304;
  return new Response(nullBodyStatus ? null : body, {
    status: resp.status,
    headers: outHeaders,
  });
}

export const GET = proxy;
export const POST = proxy;
export const PATCH = proxy;
export const PUT = proxy;
export const DELETE = proxy;
