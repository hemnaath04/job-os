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
  const { path } = await ctx.params;
  const { userId, sessionId, getToken } = await auth();
  const token = await getToken();

  const upstream =
    `${API}/api/v1/${(path ?? []).join("/")}${req.nextUrl.search}`;

  const headers = new Headers();
  for (const name of FORWARD_HEADERS) {
    const v = req.headers.get(name);
    if (v) headers.set(name, v);
  }
  if (token) headers.set("authorization", `Bearer ${token}`);

  if (!userId || !token) {
    // No session — let upstream return its standard 401 instead of forwarding
    // an empty Authorization header that would mask the cause.
    return NextResponse.json({ detail: "not authenticated" }, { status: 401 });
  }

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
    return NextResponse.json(
      { detail: `upstream unreachable: ${(e as Error).message}` },
      { status: 502 },
    );
  }

  const outHeaders = new Headers(resp.headers);
  ["transfer-encoding", "connection", "keep-alive"].forEach((h) =>
    outHeaders.delete(h),
  );
  return new Response(resp.body, { status: resp.status, headers: outHeaders });
}

export const GET = proxy;
export const POST = proxy;
export const PATCH = proxy;
export const PUT = proxy;
export const DELETE = proxy;
