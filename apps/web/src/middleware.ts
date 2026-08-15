import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";

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
]);

export default clerkMiddleware(async (auth, req) => {
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
