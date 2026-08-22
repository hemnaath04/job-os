import { NextResponse, type NextRequest } from "next/server";
import { MAINTENANCE_BYPASS_COOKIE } from "@/lib/maintenance";

/**
 * Visit `/api/maintenance-bypass?token=<MAINTENANCE_BYPASS_SECRET>` to keep
 * working while the maintenance splash is on for everyone else. Sets a
 * long-lived cookie the middleware checks before it consults Edge Config, so
 * one visit covers the whole deploy window, not just this request.
 */
export function GET(req: NextRequest): NextResponse {
  const token = req.nextUrl.searchParams.get("token");
  const secret = process.env.MAINTENANCE_BYPASS_SECRET;

  if (!secret || token !== secret) {
    return NextResponse.json({ error: "Invalid or missing token." }, { status: 403 });
  }

  const response = NextResponse.redirect(new URL("/", req.url));
  response.cookies.set(MAINTENANCE_BYPASS_COOKIE, secret, {
    httpOnly: true,
    secure: true,
    sameSite: "lax",
    maxAge: 60 * 60 * 24, // one day -- long enough for a deploy window, not forever
  });
  return response;
}
