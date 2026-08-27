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
// from it. Only a fallback for a local run: CI sets its own, pointing at a
// different Clerk instance, which is why no assertion below depends on which
// instance it is. Nothing here is sent anywhere.
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

  it("asks for a refresh token, so an unattended client survives a day", () => {
    // A Clerk access token expires after 24 hours and a refresh token never
    // does. A client that reads this list and requests exactly what it says,
    // without offline_access, stops working daily and needs a person back at
    // a browser. Fine for an interactive session, useless for the unattended
    // agents that are most of what connects to an MCP server.
    assert.ok((metadata.scopes_supported as string[]).includes("offline_access"));
  });

  it("does not ask for metadata scopes no tool reads", () => {
    // Clerk supports both, and widening a consent screen for access nothing
    // uses is how a connector starts looking like it wants more than it does.
    const scopes = metadata.scopes_supported as string[];
    assert.ok(!scopes.includes("public_metadata"));
    assert.ok(!scopes.includes("private_metadata"));
  });

  it("asks for enough to identify the caller", () => {
    // Every tool scopes its reads and writes to the calling user, which the
    // connector resolves from the token's subject. Without openid there is no
    // subject and the whole server has no one to be.
    assert.ok((metadata.scopes_supported as string[]).includes("openid"));
  });

  it("still names exactly one authorization server", () => {
    // Derived from the publishable key, so the host differs per environment
    // and is not this test's business. That it resolves to a single https
    // issuer is: a client with none, or with a choice to make, cannot start
    // the flow at all.
    const servers = metadata.authorization_servers as string[];
    assert.equal(servers.length, 1);
    assert.ok(servers[0].startsWith("https://"), servers[0]);
  });
});
