/**
 * Covers the real bug this fixes: fetchJson used to JSON.stringify the whole
 * parsed error body, so an HTTPException's own message ended up buried
 * inside a blob like `504: {"detail":"...use 'Paste the description'
 * instead."}` instead of being the message itself. These tests use the two
 * real backend messages this app already ships or is about to (jobs.py's
 * `create_from_url`, 502 already on main and 504 from the open
 * fix/from-url-timeout PR), plus the shapes that are NOT a plain string
 * (FastAPI validation errors, and resumes.py's finalize 409) to prove the
 * fallback degrades sensibly instead of crashing or showing "[object
 * Object]".
 */
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { ApiError, detailFromErrorBody, friendlyStatusText } from "./api-error.ts";

// Mirrors jobs.py's real 502 detail text, minus its one em dash: this repo's
// convention keeps those out of code, tests included, even when quoting
// another file's string closely.
const FROM_URL_502_DETAIL =
  "Could not fetch that job posting right now, the fetch service is " +
  "temporarily unavailable. Try again in a moment, or use " +
  "'Paste the description' instead.";

const FROM_URL_504_DETAIL =
  "That job posting is taking too long to fetch and parse. Try again in a " +
  "moment, or use 'Paste the description' instead: it skips the live fetch " +
  "entirely and finishes right away.";

describe("detailFromErrorBody", () => {
  it("uses a string detail directly, not a JSON-stringified blob", () => {
    const body = JSON.stringify({ detail: FROM_URL_504_DETAIL });
    const detail = detailFromErrorBody(504, body);
    assert.equal(detail, FROM_URL_504_DETAIL);
    // The regression this guards: the old code returned
    // `JSON.stringify(parsed)`, i.e. the whole `{"detail":"..."}` object,
    // for every JSON body regardless of shape.
    assert.ok(!detail.startsWith("{"), "must not be the raw JSON blob");
    assert.ok(!detail.includes('"detail"'), "must not still carry the JSON key");
  });

  it("does the same for the 502 fetch-failure message", () => {
    const body = JSON.stringify({ detail: FROM_URL_502_DETAIL });
    assert.equal(detailFromErrorBody(502, body), FROM_URL_502_DETAIL);
  });

  it("falls back to stringifying the whole body when detail is a list (FastAPI validation errors)", () => {
    const validationBody = {
      detail: [{ loc: ["body", "url"], msg: "field required", type: "missing" }],
    };
    const detail = detailFromErrorBody(422, JSON.stringify(validationBody));
    // Degrades sensibly rather than "[object Object]" or throwing.
    assert.ok(detail.length > 0);
    assert.doesNotThrow(() => JSON.parse(detail));
    assert.deepEqual(JSON.parse(detail), validationBody);
  });

  it("falls back to stringifying the whole body when detail is a dict (resumes.py finalize 409)", () => {
    const finalizeBody = {
      detail: {
        message: "Resume did not pass the final quality gate.",
        review: { score: 41, passed: false },
      },
    };
    const detail = detailFromErrorBody(409, JSON.stringify(finalizeBody));
    assert.doesNotThrow(() => JSON.parse(detail));
    assert.deepEqual(JSON.parse(detail), finalizeBody);
  });

  it("falls back to a friendly sentence for a non-JSON body (platform error page)", () => {
    const detail = detailFromErrorBody(502, "<html>Application Error</html>");
    assert.equal(detail, friendlyStatusText(502));
    assert.ok(!detail.includes("<html>"));
  });

  it("stringifies a JSON body with no detail key at all", () => {
    const detail = detailFromErrorBody(500, JSON.stringify({ error: "boom" }));
    assert.deepEqual(JSON.parse(detail), { error: "boom" });
  });
});

describe("ApiError", () => {
  it("keeps the numeric status off the message string, on .status", () => {
    const error = new ApiError(409, "Already applied to this job.");
    assert.equal(error.status, 409);
    assert.equal(error.detail, "Already applied to this job.");
  });

  it("still starts .message with '<status>: ' for any unmigrated string-sniffing caller", () => {
    const error = new ApiError(504, FROM_URL_504_DETAIL);
    assert.ok(error.message.startsWith("504: "));
    assert.ok(error.message.includes(FROM_URL_504_DETAIL));
  });

  it("is a real Error, so `instanceof Error` checks elsewhere keep working", () => {
    const error = new ApiError(500, "boom");
    assert.ok(error instanceof Error);
  });
});
