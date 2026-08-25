import assert from "node:assert/strict";
import { beforeEach, describe, it } from "node:test";

import {
  __resetForTests,
  clearDraft,
  getVersion,
  isEnriching,
  markRunning,
  markSettled,
  readDraft,
  setDraft,
  subscribe,
} from "./pending-enrich-store.ts";

const JOB = "job-1";
const OTHER = "job-2";

beforeEach(() => {
  __resetForTests();
});

describe("drafts belong to the job, not the panel", () => {
  it("keeps a draft per job so selecting another does not show the wrong text", () => {
    setDraft(JOB, "the BNY description");
    setDraft(OTHER, "the Disney description");

    assert.equal(readDraft(JOB), "the BNY description");
    assert.equal(readDraft(OTHER), "the Disney description");
  });

  it("survives the panel closing, which is the whole point", () => {
    // Nothing here is component state, so there is no unmount to lose it to.
    setDraft(JOB, "typed, then navigated away from");
    assert.equal(readDraft(JOB), "typed, then navigated away from");
  });

  it("reports an empty draft for a job that has none", () => {
    assert.equal(readDraft("never-touched"), "");
  });

  it("clears one job's draft without touching another's", () => {
    setDraft(JOB, "keep me");
    setDraft(OTHER, "discard me");
    clearDraft(OTHER);

    assert.equal(readDraft(JOB), "keep me");
    assert.equal(readDraft(OTHER), "");
  });
});

describe("in-flight state", () => {
  it("is per job", () => {
    markRunning(JOB);

    assert.equal(isEnriching(JOB), true);
    assert.equal(isEnriching(OTHER), false);
  });

  it("refuses a second start for the same job", () => {
    assert.equal(markRunning(JOB), true);
    assert.equal(markRunning(JOB), false, "a double click must not fire twice");
  });

  it("allows a start again once the first settled", () => {
    markRunning(JOB);
    markSettled(JOB);

    assert.equal(isEnriching(JOB), false);
    assert.equal(markRunning(JOB), true);
  });
});

describe("subscribers", () => {
  it("are notified on every change, and the version moves", () => {
    let calls = 0;
    const unsubscribe = subscribe(() => {
      calls += 1;
    });
    const before = getVersion();

    setDraft(JOB, "a");
    markRunning(JOB);
    markSettled(JOB);

    assert.equal(calls, 3);
    assert.ok(getVersion() > before, "the snapshot has to change or React will not re-render");

    unsubscribe();
    setDraft(JOB, "b");
    assert.equal(calls, 3, "an unsubscribed listener stops being called");
  });
});
