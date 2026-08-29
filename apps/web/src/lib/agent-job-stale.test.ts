import assert from "node:assert/strict";
import test from "node:test";

import {
  AGENT_JOB_STALE_ERROR,
  AGENT_JOB_STALE_MS,
  isStaleAgentJob,
  type StaleCheckable,
  withStaleness,
} from "./agent-job-stale.ts";

const NOW = Date.parse("2026-08-29T12:00:00.000Z");
const ago = (ms: number) => new Date(NOW - ms).toISOString();

test("a run silent past the function's own ceiling is reported failed", () => {
  // The real incident: Appwrite's execution record read failed, 500,
  // "general_unknown", no logs. The row stayed "running" with nothing left to
  // update it.
  const running: StaleCheckable = {
    status: "running",
    created_at: ago(AGENT_JOB_STALE_MS + 60_000),
  };
  const job = withStaleness(running, NOW);

  assert.equal(job.status, "failed");
  assert.equal(job.error, AGENT_JOB_STALE_ERROR);
});

test("a run still inside the ceiling is left alone", () => {
  // Being early here sends someone to redo work that was about to land, which
  // is worse than the wait it saves.
  const job = { status: "running", created_at: ago(AGENT_JOB_STALE_MS - 60_000) };

  assert.equal(isStaleAgentJob(job, NOW), false);
  assert.equal(withStaleness(job, NOW).status, "running");
});

test("progress is the freshest sign of life and beats the created time", () => {
  // A long run that keeps reporting is alive, however old the row is.
  const job = {
    status: "running",
    created_at: ago(AGENT_JOB_STALE_MS * 3),
    updated_at: ago(AGENT_JOB_STALE_MS * 2),
    progress: { updated_at: ago(1_000) },
  };

  assert.equal(isStaleAgentJob(job, NOW), false);
});

test("a run that died before its first progress write is still caught", () => {
  // The case this exists for. `created_at` is the floor precisely because
  // there is nothing else to date it by.
  const job = { status: "running", created_at: ago(AGENT_JOB_STALE_MS + 1) };

  assert.equal(isStaleAgentJob(job, NOW), true);
});

test("only a running job can be stale", () => {
  for (const status of ["queued", "succeeded", "failed"]) {
    const job = { status, created_at: ago(AGENT_JOB_STALE_MS * 10) };
    assert.equal(isStaleAgentJob(job, NOW), false, status);
  }
});

test("a reason the run gave for itself is kept", () => {
  // If it managed to say why it was in trouble before dying, that sentence is
  // better than the generic one.
  const spoke: StaleCheckable = {
    status: "running",
    error: "The gateway refused the request.",
    created_at: ago(AGENT_JOB_STALE_MS + 60_000),
  };
  const job = withStaleness(spoke, NOW);

  assert.equal(job.error, "The gateway refused the request.");
});

test("an unparseable or missing timestamp is not treated as dead", () => {
  // Claiming a run failed on the strength of a timestamp we could not read
  // would invent the one fact this module is supposed to establish.
  assert.equal(isStaleAgentJob({ status: "running" }, NOW), false);
  assert.equal(isStaleAgentJob({ status: "running", created_at: "not a date" }, NOW), false);
});

test("the job is returned unchanged rather than rebuilt when it is alive", () => {
  const job = { status: "running", created_at: ago(1_000), extra: "kept" };
  assert.equal(withStaleness(job, NOW), job);
});
