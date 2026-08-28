import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";
import { NextResponse } from "next/server";
import {
  hasMaintenanceBypass,
  isMaintenanceModeOn,
  MAINTENANCE_BYPASS_COOKIE,
} from "@/lib/maintenance";

// Public routes: marketing page + auth handlers
const isPublic = createRouteMatcher([
  "/",
  "/sign-in(.*)",
  "/sign-up(.*)",
  "/docs(.*)",
  // Status-only endpoint used to wake the API before authenticated data calls.
  "/api/backend/health(.*)",
  // The MCP connector does its own auth: verifies a Clerk OAuth access token
  // per-request rather than a browser session, so it must not go through
  // auth.protect() here. Metadata endpoints are fetched by MCP clients with
  // no Clerk session at all, ahead of any OAuth flow, so they stay public too.
  "/mcp(.*)",
  "/.well-known/oauth-authorization-server(.*)",
  "/.well-known/oauth-protected-resource(.*)",
  "/maintenance",
  "/api/maintenance-bypass(.*)",
]);

// Reachable during maintenance regardless of the bypass cookie: the splash
// itself, the one route that sets that cookie, and the health check the FE
// polls from the splash page and elsewhere.
const isMaintenanceExempt = createRouteMatcher([
  "/maintenance",
  "/api/maintenance-bypass(.*)",
  "/api/backend/health(.*)",
]);

export default clerkMiddleware(async (auth, req) => {
  if (!isMaintenanceExempt(req)) {
    const bypassed = hasMaintenanceBypass(
      req.cookies.get(MAINTENANCE_BYPASS_COOKIE)?.value,
      process.env.MAINTENANCE_BYPASS_SECRET,
    );
    if (!bypassed && (await isMaintenanceModeOn())) {
      if (req.nextUrl.pathname.startsWith("/api/")) {
        return NextResponse.json(
          {
            error:
              "job.os is temporarily unavailable while we deploy an update. Try again in a moment.",
          },
          { status: 503 },
        );
      }
      const url = req.nextUrl.clone();
      url.pathname = "/maintenance";
      return NextResponse.rewrite(url);
    }
  }

  if (!isPublic(req)) {
    await auth.protect();
  }
});

export const config = {
  matcher: [
    // skip Next.js internals + static files
    "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
    "/(api|trpc)(.*)",
  ],
};
