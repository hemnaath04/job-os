import assert from "node:assert/strict";
import { test } from "node:test";
import { hasMaintenanceBypass } from "./maintenance.ts";

// The defect: the middleware compared the cookie to the env var with a bare
// `===`. On a deployment with MAINTENANCE_BYPASS_SECRET unset, both sides are
// `undefined`, so every visitor read as bypassed and the maintenance splash
// never appeared for anyone -- silently, and in the direction that matters least
// safely for a switch you flip during an incident.

test("no secret configured means nobody bypasses, even with no cookie", () => {
  assert.equal(hasMaintenanceBypass(undefined, undefined), false);
});

test("no secret configured means nobody bypasses, even carrying a cookie", () => {
  assert.equal(hasMaintenanceBypass("anything", undefined), false);
});

test("an empty secret is not a usable secret", () => {
  assert.equal(hasMaintenanceBypass("", ""), false);
});

test("the right cookie against a real secret bypasses", () => {
  assert.equal(hasMaintenanceBypass("s3cret", "s3cret"), true);
});

test("a wrong cookie against a real secret does not bypass", () => {
  assert.equal(hasMaintenanceBypass("guess", "s3cret"), false);
});

test("no cookie against a real secret does not bypass", () => {
  assert.equal(hasMaintenanceBypass(undefined, "s3cret"), false);
});
