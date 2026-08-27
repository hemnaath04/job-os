/**
 * The scopes an MCP client is told to ask Clerk for.
 *
 * This document is the only place a client learns what to request. When it
 * advertised no `scopes_supported`, clients sent their own default, Clerk
 * rejected it as `invalid_scope`, and the browser landed on
 * `/callback?error=invalid_scope` reading only "OAuth callback failed" with
 * nothing pointing at scopes. Nothing failed loudly on the server, and the
 * document still looked well-formed, which is why it is worth pinning.
 *
 * Asserted against the real route handler rather than a copy of the list, so
 * the metadata that actually ships is what gets checked.
 */
import assert from "node:assert/strict";
import { before, describe, it } from "node:test";

// The handler reads this when called; Clerk derives the authorization server
// from it. Any well-formed publishable key works: nothing here is sent.
process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY ??=
  "pk_live_Y2xlcmsuam9icy5oZW1uYWF0aC50ZWNoJA";

// Clerk's authorization server accepts exactly these. Anything advertised
// that is not in this set sends clients to invalid_scope.
const CLERK_SUPPORTED = new Set([
  "openid",
  "profile",
  "email",
  "public_metadata",
  "private_metadata",
  "offline_access",
]);

let metadata: Record<string, unknown>;

describe("the MCP protected-resource metadata", () => {
  before(async () => {
    const { GET } = await import(
      "../app/.well-known/oauth-protected-resource/mcp/route.ts"
    );
    const res = GET(
      new Request("https://jobs.hemnaath.tech/.well-known/oauth-protected-resource/mcp"),
    );
    metadata = await res.json();
  });

  it("tells clients which scopes to request", () => {
    assert.ok(
      Array.isArray(metadata.scopes_supported) && metadata.scopes_supported.length > 0,
      "no scopes_supported: clients fall back to a default Clerk rejects",
    );
  });

  it("advertises only scopes Clerk will actually issue", () => {
    for (const scope of metadata.scopes_supported as string[]) {
      assert.ok(CLERK_SUPPORTED.has(scope), `Clerk does not support the scope ${scope}`);
    }
  });

  it("asks for enough to identify the caller", () => {
    // Every tool scopes its reads and writes to the calling user, which the
    // connector resolves from the token's subject. Without openid there is no
    // subject and the whole server has no one to be.
    assert.ok((metadata.scopes_supported as string[]).includes("openid"));
  });

  it("still points at the Clerk authorization server", () => {
    assert.deepEqual(metadata.authorization_servers, ["https://clerk.jobs.hemnaath.tech"]);
  });
});
