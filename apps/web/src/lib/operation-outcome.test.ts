import assert from "node:assert/strict";
import test from "node:test";
import {
  isUnrecoverablePollError,
  MAX_AGE_MS,
  OPERATION_FAILURE,
  QUEUED_GRACE_MS,
  readAgentJob,
} from "./operation-outcome.ts";

/**
 * A run that is slow, that timed out, or that the agent failed is never
 * described as something the user did.
 *
 * The live failure: a tailoring run sat on "Finding the real gaps" at around
 * 20% -- one model call that genuinely takes minutes -- and the activity card
 * ended up reading "You canceled this run." Nobody canceled anything. Nothing
 * in the browser can abort a server run, so leaving the page was being written
 * down as a cancellation, and marking the card failed also killed the poll
 * that would have delivered the resume the agent went on to save.
 *
 * These pin both halves: every outcome this module can reach describes the
 * server, and none of them blames the person waiting.
 */

const fresh = 30_000;

/** Anything that would read as "you did this" rather than "this happened". */
function blamesTheUser(message: string): boolean {
  return /\byou\b|\bcancel/i.test(message);
}

test("a failed agent job is reported as a failure, not as a cancel", () => {
  const outcome = readAgentJob({ status: "failed", error: null }, fresh);

  assert.equal(outcome.kind, "failed");
  assert.equal(outcome.kind === "failed" && outcome.message, OPERATION_FAILURE.agentFailed);
  assert.ok(!blamesTheUser(OPERATION_FAILURE.agentFailed));
});

test("a timed-out run is reported as taking too long, not as a cancel", () => {
  const outcome = readAgentJob({ status: "running" }, MAX_AGE_MS + 1);

  assert.equal(outcome.kind, "failed");
  assert.equal(outcome.kind === "failed" && outcome.message, OPERATION_FAILURE.timedOut);
  assert.ok(!blamesTheUser(OPERATION_FAILURE.timedOut));
});

test("no failure sentence this module can produce blames the user", () => {
  for (const [name, message] of Object.entries(OPERATION_FAILURE)) {
    assert.ok(!blamesTheUser(message), `${name}: ${message}`);
  }
});

test("a long model call still counts as running", () => {
  // The exact shape of the run that broke: eight minutes in, still on the gaps
  // step, agent reporting progress. Well inside MAX_AGE_MS, so it keeps going.
  const outcome = readAgentJob({ status: "running" }, 8 * 60 * 1_000);

  assert.deepEqual(outcome, { kind: "running" });
});

test("the browser waits at least as long as the agent's own kill and reap", () => {
  // The agent function is killed at 900s and its reaper marks a stranded row
  // failed after another 900s. Giving up first is how a slow run became a
  // "failed" one in the UI while the server was still working on it.
  assert.ok(MAX_AGE_MS >= 15 * 60 * 1_000);
});

test("the agent's own reason survives when it wrote one", () => {
  const outcome = readAgentJob(
    { status: "failed", error: "The model gateway was unreachable." },
    fresh,
  );

  assert.equal(
    outcome.kind === "failed" && outcome.message,
    "The model gateway was unreachable.",
  );
});

test("an empty agent reason falls back rather than showing a blank card", () => {
  const outcome = readAgentJob({ status: "failed", error: "   " }, fresh);

  assert.equal(outcome.kind === "failed" && outcome.message, OPERATION_FAILURE.agentFailed);
});

test("a succeeded job with output is done", () => {
  const outcome = readAgentJob(
    { status: "succeeded", output: { resume_id: "r1", id: "v1" } },
    fresh,
  );

  assert.deepEqual(outcome, { kind: "done" });
});

test("a succeeded job with no output is a failure, honestly named", () => {
  const outcome = readAgentJob({ status: "succeeded", output: null }, fresh);

  assert.equal(outcome.kind === "failed" && outcome.message, OPERATION_FAILURE.emptyResult);
});

test("a queued job is given a cold-start grace period before it is written off", () => {
  assert.deepEqual(readAgentJob({ status: "queued" }, QUEUED_GRACE_MS - 1), {
    kind: "running",
  });

  const givenUp = readAgentJob({ status: "queued" }, QUEUED_GRACE_MS + 1);
  assert.equal(givenUp.kind === "failed" && givenUp.message, OPERATION_FAILURE.neverStarted);
});

test("an unreadable start time is treated as too old rather than as brand new", () => {
  const outcome = readAgentJob({ status: "running" }, Number.NaN);

  assert.equal(outcome.kind === "failed" && outcome.message, OPERATION_FAILURE.timedOut);
});

test("only a missing job row ends the poll; a network blip is retried", () => {
  assert.ok(isUnrecoverablePollError("404: job not found"));
  assert.ok(isUnrecoverablePollError("Document with the requested ID could not be found"));

  for (const blip of ["fetch failed", "network error", "503 service unavailable"]) {
    assert.ok(!isUnrecoverablePollError(blip), blip);
  }
});
