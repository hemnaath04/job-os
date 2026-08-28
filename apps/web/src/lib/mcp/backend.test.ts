import assert from "node:assert/strict";
import test, { afterEach } from "node:test";
import { BackendError, callBackend, fetchExternalFile } from "./backend.ts";

/**
 * A JSON tool never hands its caller an HTML page or a parser crash.
 *
 * Every MCP tool here is a JSON API in front of another JSON API, and the
 * backend itself is well behaved: FastAPI answers `{"detail": ...}` for an
 * HTTPException and for its own 500. The things BETWEEN them are not. A
 * platform's "Application Error" page for a crashed or sleeping dyno, a CDN's
 * 502/503/504 page and a proxy timeout page are all HTML documents, and any of
 * them can appear in front of a perfectly healthy handler.
 *
 * `JSON.parse` used to run on that body before the status code was looked at,
 * so `search_jobs` -- the tool an agent hits hardest -- died with
 * "SyntaxError: Unexpected token '<'" and the agent was told nothing it could
 * act on. These pin that every path returns either JSON or a BackendError
 * carrying a status and a sentence.
 */

const realFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = realFetch;
});

/** Stand in for whatever answered, with a body and a status of our choosing. */
function respondWith(status: number, body: string, statusText = "") {
  globalThis.fetch = (async () =>
    // 204 forbids a body at the Response constructor, which is the same rule
    // the real server obeys.
    new Response(status === 204 ? null : body, { status, statusText })) as typeof fetch;
}

const HEROKU_ERROR_PAGE =
  "<!DOCTYPE html><html><head><title>Application Error</title></head>" +
  "<body><h2>Application error</h2></body></html>";

test("an HTML error page becomes a JSON-shaped error, not a parser crash", async () => {
  respondWith(503, HEROKU_ERROR_PAGE, "Service Unavailable");

  const error = await callBackend("tok", "POST", "/index/search", { limit: 10 }).then(
    () => null,
    (e: unknown) => e,
  );

  assert.ok(error instanceof BackendError, `got ${String(error)}`);
  assert.equal(error.status, 503);
  assert.doesNotMatch(error.message, /SyntaxError|Unexpected token|</);
  assert.match(error.message, /try again/i);
});

test("an HTML page served with a 200 is still not a result", async () => {
  // A maintenance page or an auth redirect rendered as a document. The status
  // says nothing is wrong, the body says otherwise, and returning it as a
  // "result" would hand the agent a web page as its job search.
  respondWith(200, "<html><body>Signing you in…</body></html>");

  const error = await callBackend("tok", "POST", "/index/search", {}).then(
    () => null,
    (e: unknown) => e,
  );

  assert.ok(error instanceof BackendError, `got ${String(error)}`);
  assert.equal(error.status, 502);
  assert.doesNotMatch(error.message, /Unexpected token|</);
});

test("a gateway timeout page names the status the caller should back off on", async () => {
  respondWith(504, "<html><body>504 Gateway Time-out</body></html>", "Gateway Timeout");

  const error = (await callBackend("tok", "GET", "/jobs").catch(
    (e: unknown) => e,
  )) as BackendError;

  assert.equal(error.status, 504);
});

test("the backend's own JSON error still reaches the caller word for word", async () => {
  // The regression this must not cause: a real FastAPI detail is the most
  // useful message there is, and it has to survive the defensive parse.
  respondWith(409, JSON.stringify({ detail: "No master resume found." }));

  const error = (await callBackend("tok", "POST", "/cover-letters/generate", {}).catch(
    (e: unknown) => e,
  )) as BackendError;

  assert.equal(error.status, 409);
  assert.equal(error.message, "No master resume found.");
});

test("a successful search still returns its parsed body", async () => {
  respondWith(200, JSON.stringify({ results: [{ id: "1" }], total: 1 }));

  const data = await callBackend("tok", "POST", "/index/search", { limit: 1 });

  assert.deepEqual(data, { results: [{ id: "1" }], total: 1 });
});

test("a 204 and an empty body are both null rather than a parse error", async () => {
  respondWith(204, "");
  assert.equal(await callBackend("tok", "DELETE", "/applications/1"), null);

  respondWith(200, "");
  assert.equal(await callBackend("tok", "GET", "/applications"), null);
});

function stubRedirectingTo(to: string): { seen: string[] } {
  const seen: string[] = [];
  globalThis.fetch = (async (input: URL | RequestInfo) => {
    const url = String(input);
    seen.push(url);
    if (seen.length === 1) {
      return new Response(null, { status: 302, headers: { location: to } });
    }
    return new Response(
      "fake file content",
      { status: 200, headers: { "content-type": "application/pdf", "content-length": "17" } }
    );
  }) as typeof globalThis.fetch;
  return { seen };
}

test("MCP fetchExternalFile: a redirect to cloud metadata is refused, and never requested", async () => {
  const target = "http://169.254.169.254/latest/meta-data/";
  const { seen } = stubRedirectingTo(target);

  await assert.rejects(
    fetchExternalFile("https://trusted.com/resume.pdf"),
  );
  assert.equal(seen.length, 1, "followed the redirect instead of stopping");
  assert.ok(!seen.some((u) => u.includes("169.254.169.254")));
});

test("MCP fetchExternalFile: a redirect to loopback is refused", async () => {
  const { seen } = stubRedirectingTo("http://127.0.0.1:8000/api/v1/me");
  await assert.rejects(
    fetchExternalFile("https://trusted.com/resume.pdf"),
  );
  assert.equal(seen.length, 1);
});

test("MCP fetchExternalFile: a redirect downgrading to plain http is refused", async () => {
  const { seen } = stubRedirectingTo("http://trusted.com/resume.pdf");
  await assert.rejects(
    fetchExternalFile("https://trusted.com/resume.pdf"),
  );
  assert.equal(seen.length, 1);
});

test("MCP fetchExternalFile: a redirect to a private address is refused", async () => {
  const { seen } = stubRedirectingTo("https://10.0.0.1/internal");
  await assert.rejects(
    fetchExternalFile("https://trusted.com/resume.pdf"),
  );
  assert.equal(seen.length, 1);
});

test("MCP fetchExternalFile: an ordinary redirect between public https hosts still works", async () => {
  const { seen } = stubRedirectingTo("https://cdn.trusted.com/resume.pdf");
  const out = await fetchExternalFile("https://trusted.com/resume.pdf");
  assert.equal(seen.length, 2, "did not follow the safe redirect");
  assert.ok(seen[1].startsWith("https://cdn.trusted.com/"));
  assert.equal(new TextDecoder().decode(out.bytes), "fake file content");
});
