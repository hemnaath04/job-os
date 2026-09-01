import assert from "node:assert/strict";
import test from "node:test";

import { writeDurablyThenMirror } from "./dual-write.ts";

/**
 * The bug this guards against shipped for roughly three weeks: applications
 * were created in both stores but edited in only the display one, so the
 * durable record silently stopped matching what the board showed.
 */

test("the durable write happens before the mirror", async () => {
  const order: string[] = [];
  await writeDurablyThenMirror(
    async () => {
      order.push("durable");
      return "value";
    },
    async () => {
      order.push("mirror");
    },
    "ordering",
  );
  assert.deepEqual(order, ["durable", "mirror"]);
});

test("the mirror receives what the durable store actually returned", async () => {
  // Not the caller's patch: the record's own version of it, so the board shows
  // what was really stored rather than what was requested.
  let seen: unknown = null;
  await writeDurablyThenMirror(
    async () => ({ id: "a1", status: "applied" }),
    async (result: unknown) => {
      seen = result;
    },
    "payload",
  );
  assert.deepEqual(seen, { id: "a1", status: "applied" });
});

test("a failing mirror does not lose the edit", async () => {
  // The whole point. Appwrite answering 402 for an exhausted read quota must
  // degrade to a stale card, not to an edit that never happened.
  const errors: unknown[] = [];
  const original = console.error;
  console.error = (...args: unknown[]) => errors.push(args);
  try {
    const result = await writeDurablyThenMirror(
      async () => "stored",
      async () => {
        throw new Error("limit_databases_reads_exceeded");
      },
      "outage",
    );
    assert.equal(result, "stored");
  } finally {
    console.error = original;
  }
  assert.equal(errors.length, 1, "the dropped mirror is reported, not swallowed");
});

test("a failing durable write is raised, never swallowed", async () => {
  // The asymmetry is deliberate: the caller must find out that the record did
  // not change, even though a display failure is tolerated.
  let mirrored = false;
  await assert.rejects(
    () =>
      writeDurablyThenMirror(
        async () => {
          throw new Error("postgres is down");
        },
        async () => {
          mirrored = true;
        },
        "durable-failure",
      ),
    /postgres is down/,
  );
  assert.equal(mirrored, false, "nothing is mirrored that was never stored");
});
