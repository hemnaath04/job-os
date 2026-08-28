import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { fetchCustomSource, type CustomSearchParams } from "./custom-fetch.ts";

// assertFetchableUrl checks the URL as written, but fetch defaults to
// `redirect: "follow"` — so before this was fixed the check only ever governed
// the FIRST hop. A custom source on an allowed https host could answer
// `302 Location: http://169.254.169.254/…` and undici would follow it, turning
// the serverless function into the private-network probe the check exists to
// prevent. Unlike the DNS-rebinding case the docstring scopes out, this needs no
// DNS control at all — just an HTTP redirect.

const REAL_FETCH = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = REAL_FETCH;
});

const PARAMS: CustomSearchParams = { titleKeywords: [], countryCodes: [] };

function sourceAt(url: string) {
  return { id: "s1", name: "Mine", url };
}

/** Records every URL fetch is asked for, and redirects the first hop to `to`. */
function stubRedirectingTo(to: string): { seen: string[] } {
  const seen: string[] = [];
  globalThis.fetch = (async (input: URL | RequestInfo) => {
    const url = String(input);
    seen.push(url);
    if (seen.length === 1) {
      return new Response(null, { status: 302, headers: { location: to } });
    }
    return new Response(
      JSON.stringify([
        { title: "Software Engineer", url: "https://example.com/jobs/1" },
      ]),
      { status: 200, headers: { "content-type": "application/json" } },
    );
  }) as typeof globalThis.fetch;
  return { seen };
}

test("a redirect to cloud metadata is refused, and never requested", async () => {
  const target = "http://169.254.169.254/latest/meta-data/";
  const { seen } = stubRedirectingTo(target);

  await assert.rejects(
    fetchCustomSource(sourceAt("https://feed.example.com/jobs"), PARAMS),
  );
  assert.equal(seen.length, 1, "followed the redirect instead of stopping");
  assert.ok(!seen.some((u) => u.includes("169.254.169.254")));
});

test("a redirect to loopback is refused", async () => {
  const { seen } = stubRedirectingTo("http://127.0.0.1:8000/api/v1/me");
  await assert.rejects(
    fetchCustomSource(sourceAt("https://feed.example.com/jobs"), PARAMS),
  );
  assert.equal(seen.length, 1);
});

test("a redirect downgrading to plain http is refused", async () => {
  const { seen } = stubRedirectingTo("http://feed.example.com/jobs");
  await assert.rejects(
    fetchCustomSource(sourceAt("https://feed.example.com/jobs"), PARAMS),
  );
  assert.equal(seen.length, 1);
});

test("a redirect to a private address is refused", async () => {
  const { seen } = stubRedirectingTo("https://10.0.0.1/internal");
  await assert.rejects(
    fetchCustomSource(sourceAt("https://feed.example.com/jobs"), PARAMS),
  );
  assert.equal(seen.length, 1);
});

test("an ordinary redirect between public https hosts still works", async () => {
  // The fix must not break feeds that legitimately redirect.
  const { seen } = stubRedirectingTo("https://cdn.example.com/jobs.json");
  const out = await fetchCustomSource(
    sourceAt("https://feed.example.com/jobs"),
    PARAMS,
  );
  assert.equal(out.length, 1);
  assert.equal(seen.length, 2, "did not follow the safe redirect");
  assert.ok(seen[1].startsWith("https://cdn.example.com/"));
});
