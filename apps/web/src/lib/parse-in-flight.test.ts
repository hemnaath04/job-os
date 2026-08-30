import assert from "node:assert/strict";
import test from "node:test";
import {
  PARSE_POLL_CEILING_MS,
  PARSE_POLL_INTERVAL_MS,
  isParseInFlight,
  parsePollInterval,
} from "./parse-in-flight.ts";

const NOW = Date.parse("2026-08-30T12:00:00Z");

function app(opts: { pending: boolean; agedMs?: number }) {
  return {
    created_at: new Date(NOW - (opts.agedMs ?? 0)).toISOString(),
    job: { jd_parsed: opts.pending ? { parse_pending: true } : {} },
  };
}

test("a row still being read keeps the list asking", () => {
  assert.equal(isParseInFlight(app({ pending: true, agedMs: 5_000 }), NOW), true);
  assert.equal(
    parsePollInterval([app({ pending: true, agedMs: 5_000 })], NOW),
    PARSE_POLL_INTERVAL_MS,
  );
});

test("a settled board is silent", () => {
  // The normal case, and the one that has to cost nothing: false, not zero,
  // because React Query reads any number as an interval and would poll a
  // finished board forever.
  const settled = [app({ pending: false }), app({ pending: false })];
  assert.equal(parsePollInterval(settled, NOW), false);
});

test("an empty board is silent", () => {
  assert.equal(parsePollInterval([], NOW), false);
  assert.equal(parsePollInterval(undefined, NOW), false);
});

test("one unread row is enough to keep the whole list asking", () => {
  const mixed = [
    app({ pending: false }),
    app({ pending: true, agedMs: 2_000 }),
    app({ pending: false }),
  ];
  assert.equal(parsePollInterval(mixed, NOW), PARSE_POLL_INTERVAL_MS);
});

test("a row stranded past the ceiling stops being asked about", () => {
  // The deferred parse runs in the web process, so a dyno restart mid-parse
  // leaves a row at parse_pending with nothing coming for it ever. Polling it
  // forever would spend a request every few seconds, in every open tab, for a
  // result that cannot arrive.
  const stranded = app({ pending: true, agedMs: PARSE_POLL_CEILING_MS + 1_000 });
  assert.equal(isParseInFlight(stranded, NOW), false);
  assert.equal(parsePollInterval([stranded], NOW), false);
});

test("a row with no timestamp is not treated as running", () => {
  // An absent or unparseable date is not evidence that work is in flight, and
  // guessing that it is would poll forever on a malformed row.
  assert.equal(
    isParseInFlight({ created_at: null, job: { jd_parsed: { parse_pending: true } } }, NOW),
    false,
  );
  assert.equal(
    isParseInFlight({ created_at: "not a date", job: { jd_parsed: { parse_pending: true } } }, NOW),
    false,
  );
});

test("a job that never carried a parse block is not pending", () => {
  assert.equal(isParseInFlight({ created_at: new Date(NOW).toISOString(), job: null }, NOW), false);
  assert.equal(isParseInFlight({ created_at: new Date(NOW).toISOString() }, NOW), false);
});
