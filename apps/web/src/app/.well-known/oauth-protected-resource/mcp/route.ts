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
 * These three and no more: the connector needs to know which user is calling,
 * which is what `clerkUserId(ctx)` in app/mcp/route.ts reads, and every tool
 * scopes its own reads and writes to that user afterwards. Nothing here should
 * grow without a tool that needs it.
 *
 * Clerk also has a `default_scopes` setting for clients that omit `scope`
 * entirely; that is a dashboard/Backend API setting rather than code, and it
 * covers a different case than this does.
 */
const SCOPES = ["openid", "profile", "email"];

const handler = protectedResourceHandlerClerk({ scopes_supported: SCOPES });

export { handler as GET };
export const OPTIONS = metadataCorsOptionsRequestHandler();
