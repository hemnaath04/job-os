import assert from "node:assert/strict";
import { test } from "node:test";
import {
  isTransient,
  partitionErrors,
  retryOnceIfTransient,
  transientNotice,
} from "./transient.ts";

// The two he actually saw, right after the v69 release.
const INDEX_503 =
  "503: temporarily unavailable, possibly restarting after a deploy";
const GITLAB_TIMEOUT = "gitlab timed out after 2500ms";

test("a restarting backend is transient", () => {
  assert.equal(isTransient(INDEX_503), true);
  assert.equal(isTransient("502 Bad Gateway"), true);
  assert.equal(isTransient("504 Gateway Timeout"), true);
  assert.equal(isTransient("failed to fetch"), true);
});

test("a slow board is transient", () => {
  assert.equal(isTransient(GITLAB_TIMEOUT), true);
  assert.equal(isTransient("ECONNRESET"), true);
});

test("things a retry cannot fix stay loud", () => {
  assert.equal(isTransient("TheirStack rejected the request (401)"), false);
  assert.equal(isTransient("THEIRSTACK_API_KEY is not configured"), false);
  assert.equal(isTransient("404: board slug has moved"), false);
  assert.equal(isTransient("Out of credits"), false);
  assert.equal(isTransient("returned too much data"), false);
});

test("a status code inside a longer number is not a 503", () => {
  assert.equal(isTransient("matched 1503 postings"), false);
});

test("the banner blames the restart only when it was a restart", () => {
  // The wording was hardcoded to "restarting", which was true for the deploy
  // window it was written for and stopped being true: the index now fails
  // mostly because its own Appwrite query runs out of time, and blaming a
  // deploy for that sends the reader to look at a log with nothing in it.
  assert.match(
    transientNotice([{ source: "index", message: INDEX_503 }]) ?? "",
    /^The saved index was restarting, so these results came from live sources only\./,
  );
  assert.match(
    transientNotice([
      { source: "index", message: "Appwrite TablesDB call failed (408): Database timed out." },
    ]) ?? "",
    /^The saved index did not answer in time, so these results came from live sources only\./,
  );
  assert.match(
    transientNotice([{ source: "index", message: "the saved index timed out after 75s" }]) ?? "",
    /did not answer in time/,
  );
});

test("a transient failure is retried once, and the retry is believed", async () => {
  let calls = 0;
  const result = await retryOnceIfTransient(async () => {
    calls += 1;
    if (calls === 1) throw new Error(INDEX_503);
    return "index results";
  }, 0);
  assert.equal(result, "index results");
  assert.equal(calls, 2);
});

test("a second failure is surfaced, so a real outage still reports", async () => {
  let calls = 0;
  await assert.rejects(
    retryOnceIfTransient(async () => {
      calls += 1;
      throw new Error(INDEX_503);
    }, 0),
    /temporarily unavailable/,
  );
  assert.equal(calls, 2);
});

test("a non-transient failure is not retried", async () => {
  let calls = 0;
  await assert.rejects(
    retryOnceIfTransient(async () => {
      calls += 1;
      throw new Error("401 unauthorized");
    }, 0),
    /401/,
  );
  assert.equal(calls, 1, "retrying a 401 just makes the user wait longer");
});

test("actionable and transient are separated", () => {
  const { actionable, transient } = partitionErrors([
    { source: "index", message: INDEX_503 },
    { source: "greenhouse", message: GITLAB_TIMEOUT },
    { source: "theirstack", message: "401 unauthorized" },
  ]);
  assert.deepEqual(
    actionable.map((e) => e.source),
    ["theirstack"],
  );
  assert.deepEqual(
    transient.map((e) => e.source),
    ["index", "greenhouse"],
  );
});

test("the index is named separately, because its absence changes the results", () => {
  assert.equal(
    transientNotice([{ source: "index", message: INDEX_503 }]),
    "The saved index was restarting, so these results came from live sources " +
      "only. Searching again usually picks it up.",
  );
});

test("slow boards are one line, not one line each", () => {
  const notice = transientNotice([
    { source: "greenhouse", message: GITLAB_TIMEOUT },
    { source: "lever", message: "timed out after 2500ms" },
    { source: "ashby", message: "timed out after 2500ms" },
  ]);
  assert.equal(
    notice,
    "ashby, greenhouse and lever were slow to answer and were skipped. " +
      "Searching again usually picks them up.",
  );
});

test("one slow board reads as one board", () => {
  assert.equal(
    transientNotice([{ source: "greenhouse", message: GITLAB_TIMEOUT }]),
    "greenhouse was slow to answer and was skipped. Searching again usually picks it up.",
  );
});

test("nothing transient says nothing", () => {
  assert.equal(transientNotice([]), null);
});
