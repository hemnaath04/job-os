import { metadataCorsOptionsRequestHandler, protectedResourceHandlerClerk } from "@clerk/mcp-tools/next";

/**
 * What an MCP client has to ask Clerk for to reach this server.
 *
 * Without this the document advertised no `scopes_supported` at all, so a
 * client had nothing to read and sent whatever it defaults to. Clerk accepts
 * only openid, profile, email, public_metadata, private_metadata and
 * offline_access, and answered anything else by bouncing the browser to
 * `/callback?error=invalid_scope`, which surfaces as "OAuth callback failed"
 * with no indication that a scope was the problem.
 *
 * openid, profile and email are what identify the caller, which is what
 * `clerkUserId(ctx)` in app/mcp/route.ts reads; every tool scopes its own
 * reads and writes to that user afterwards.
 *
 * offline_access is here for clients that are not Claude Code. A Clerk access
 * token expires after a day and a refresh token never does, so a client that
 * reads this list, requests exactly what it says, and gets no refresh token
 * stops working every 24 hours and needs a person back at a browser to start
 * again. That is fine for an interactive session and useless for an unattended
 * agent, which is most of what connects to an MCP server. The cost is that a
 * leaked refresh token is access until it is revoked in the Clerk dashboard,
 * which is the ordinary price of a connection nobody is sitting in front of.
 *
 * Not advertised: public_metadata and private_metadata. Clerk supports both
 * and no tool here reads either, so asking for them would widen a consent
 * screen for nothing.
 *
 * Clerk also has a `default_scopes` setting for clients that omit `scope`
 * entirely; that is a dashboard/Backend API setting rather than code, and it
 * covers a different case than this does. This instance already defaults to
 * email, profile and offline_access, so advertising offline_access mostly
 * makes the document agree with what Clerk already hands out.
 */
const SCOPES = ["openid", "profile", "email", "offline_access"];

const handler = protectedResourceHandlerClerk({ scopes_supported: SCOPES });

export { handler as GET };
export const OPTIONS = metadataCorsOptionsRequestHandler();
