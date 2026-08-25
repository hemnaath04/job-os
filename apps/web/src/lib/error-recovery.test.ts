/**
 * Covers the real bug: reportFailure used to lead a toast with recoveryFor's
 * generic, pattern-matched sentence ("The service did not respond. Try
 * again in a moment.") and bury the backend's own, specific message in a
 * parenthetical at the end, which nobody's eye lands on. That defeated the
 * whole point of jobs.py's `create_from_url` writing a 504 that tells the
 * user to use "Paste the description" instead, and its 502 that says the
 * fetch service itself is down.
 *
 * `failureDescription` is what `reportFailure` hands to `toast.error`'s
 * `description`, extracted into error-recovery.ts (a leaf module, no sonner
 * import) so it can be asserted on directly here without a DOM or a real
 * Toaster.
 */
import assert from "node:assert/strict";
import { before, describe, it } from "node:test";

import { ApiError } from "./api-error.ts";
import { backendDetail, failureDescription, recoveryFor } from "./error-recovery.ts";

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

const GENERIC_5XX_FALLBACK = "The service did not respond. Try again in a moment.";

before(() => {
  // recoveryFor's offline check reads the real navigator.onLine, which is
  // `undefined` under Node (no browser). Set it to the ordinary "online"
  // default these tests want, the same state a real user's browser reports
  // most of the time.
  (navigator as { onLine: boolean }).onLine = true;
});

describe("failureDescription: backend detail leads, acceptance cases", () => {
  it("504 from-url timeout: leads with the backend's own guidance, not the generic 5xx line", () => {
    const error = new ApiError(504, FROM_URL_504_DETAIL);
    const description = failureDescription(error);
    assert.equal(description, FROM_URL_504_DETAIL);
    assert.ok(
      !description.startsWith(GENERIC_5XX_FALLBACK),
      "the generic 5xx fallback must not be the prominent (leading) text",
    );
  });

  it("502 from-url fetch failure: leads with the backend's own guidance, not the generic 5xx line", () => {
    const error = new ApiError(502, FROM_URL_502_DETAIL);
    const description = failureDescription(error);
    assert.equal(description, FROM_URL_502_DETAIL);
    assert.ok(!description.startsWith(GENERIC_5XX_FALLBACK));
  });

  it("an explicit recovery override still wins over the backend detail (existing convention)", () => {
    const error = new ApiError(409, '{"message":"blocked","review":{}}');
    const description = failureDescription(error, "Fix the flagged issues and try again.");
    assert.equal(description, `Fix the flagged issues and try again. (${error.message})`);
  });
});

describe("failureDescription: no real detail still falls back to recoveryFor (no regression)", () => {
  it("a raw fetch/network failure (no ApiError, no status) gets the connection fallback", () => {
    const error = new TypeError("Failed to fetch");
    const description = failureDescription(error);
    assert.equal(description, `Check your connection and try again. (Failed to fetch)`);
  });

  it("a plain thrown string with no useful text falls back to 'Try again.'", () => {
    const description = failureDescription("");
    assert.equal(description, "Try again.");
  });

  it("recoveryFor itself still keys off status/keyword text the same way as before", () => {
    assert.equal(recoveryFor("409: already exists"), "It already exists, so nothing was changed.");
    assert.equal(recoveryFor("504: gateway timeout"), GENERIC_5XX_FALLBACK);
  });
});

describe("backendDetail", () => {
  it("returns null for anything that is not an ApiError", () => {
    assert.equal(backendDetail(new Error("Failed to fetch")), null);
    assert.equal(backendDetail("plain string"), null);
    assert.equal(backendDetail(undefined), null);
  });

  it("returns the clean detail text for an ApiError", () => {
    assert.equal(backendDetail(new ApiError(504, FROM_URL_504_DETAIL)), FROM_URL_504_DETAIL);
  });
});
