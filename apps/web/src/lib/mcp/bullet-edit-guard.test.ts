import assert from "node:assert/strict";
import { test } from "node:test";
import {
  BULLET_MAX_WORDS,
  checkBulletEdit,
  REWORD_SLACK_WORDS,
} from "./bullet-edit-guard.ts";

// His real 46-word ClaimFarm bullet, the one that keeps coming back flagged.
const CLAIMFARM =
  "Built an AI agent that turns a farmer's crop photo into a filed insurance " +
  "claim in under a minute: a vision model grades damage, weather corroborates " +
  "it, embeddings retrieve similar claims, and an LLM drafts a localized " +
  "confirmation in 10 languages, behind a 6-signal fraud check.";
const SHORT = "Built a scheduler.";

test("tightening an over-long bullet is the point of this", () => {
  const trimmed =
    "Built an AI agent that turns a crop photo into a filed insurance claim: a " +
    "vision model grades damage and an LLM drafts the confirmation.";
  assert.equal(checkBulletEdit(CLAIMFARM, trimmed).ok, true);
});

test("varying a repeated opening verb is allowed", () => {
  assert.equal(checkBulletEdit(SHORT, "Shipped a scheduler.").ok, true);
});

test("a metric that appears from nowhere is refused", () => {
  const inflated = SHORT.replace("a scheduler", "a scheduler serving 40000 users");
  const verdict = checkBulletEdit(SHORT, inflated);
  assert.equal(verdict.ok, false);
  assert.match(verdict.reason ?? "", /40000/);
  assert.match(verdict.reason ?? "", /vouch for it/);
});

test("a number the bullet already states may be moved around", () => {
  const reworded =
    "Built an AI agent that files a crop-insurance claim in under a minute, " +
    "drafting a localized confirmation in 10 languages behind a 6-signal fraud check.";
  assert.equal(checkBulletEdit(CLAIMFARM, reworded).ok, true);
});

test("a number the FACT states, but this bullet does not, is allowed", () => {
  const verdict = checkBulletEdit(
    SHORT,
    "Built a scheduler across 381 basins.",
    'BedRocked {"basins":381}',
  );
  assert.equal(verdict.ok, true, "the evidence is right there on the fact");
});

test("padding a long bullet is refused", () => {
  const padded = `${CLAIMFARM} It demonstrates strong cross-functional collaboration and ownership.`;
  const verdict = checkBulletEdit(CLAIMFARM, padded);
  assert.equal(verdict.ok, false);
  assert.match(verdict.reason ?? "", /not for growing it/);
});

test("a short bullet has room to grow, up to the cap", () => {
  const grown = `Built a scheduler ${"word ".repeat(BULLET_MAX_WORDS - 4)}`.trim();
  assert.equal(checkBulletEdit(SHORT, grown).ok, true);
  const over = `Built a scheduler ${"word ".repeat(BULLET_MAX_WORDS)}`.trim();
  assert.equal(checkBulletEdit(SHORT, over).ok, false);
});

test("a long bullet may stay as long as it already is", () => {
  assert.equal(checkBulletEdit(CLAIMFARM, CLAIMFARM).ok, true);
});

test("an empty bullet is refused rather than silently written", () => {
  const verdict = checkBulletEdit(CLAIMFARM, "   ");
  assert.equal(verdict.ok, false);
  assert.match(verdict.reason ?? "", /cannot be empty/);
});

test("a thousands separator is not a different number", () => {
  const withComma = "Scored all 2,404 sewer segments.";
  const without = "Scored all 2404 sewer segments.";
  assert.equal(checkBulletEdit(withComma, without).ok, true);
});


// ---------------------------------------------------------------------------
// The guard blocked an honesty fix.
//
// The first real use of this tool was correcting his EPAM bullet from "Owned
// and extended the Go test suite" to "Worked on and extended", because he did
// not own it. A claim getting SMALLER, the safest edit this tool can make, and
// the length rule refused it: the bullet is 35 words against a 35-word ceiling,
// and "Owned" is one word where "Worked on" is two.
//
// The edit had to go around the guard, which is precisely the workaround a
// guard exists to prevent.
// ---------------------------------------------------------------------------

const EPAM_OWNED =
  "Owned and extended the Go test suite for the Fares team's pricing engine " +
  "across most of the platform's city footprint; triaged daily failures, fixed " +
  "flaky cases, and added regression coverage as new pricing rules shipped.";
const EPAM_HONEST = EPAM_OWNED.replace("Owned and", "Worked on and");

test("the honesty fix the guard used to refuse", () => {
  assert.equal(EPAM_OWNED.trim().split(/\s+/).length, 35);
  assert.equal(EPAM_HONEST.trim().split(/\s+/).length, 36);
  assert.equal(checkBulletEdit(EPAM_OWNED, EPAM_HONEST).ok, true);
});

test("de-escalating a claim is never blocked for being a word longer", () => {
  const owned = "Led the migration of the billing service.";
  assert.equal(checkBulletEdit(owned, "Worked on the migration of the billing service.").ok, true);
  assert.equal(checkBulletEdit(owned, "Contributed to the migration of the billing service.").ok, true);
});

test("the slack is small enough that it cannot carry padding", () => {
  const long = `Built a thing ${"word ".repeat(40)}`.trim();
  const padded = `${long} demonstrating strong cross-functional collaboration and end-to-end ownership`;
  const verdict = checkBulletEdit(long, padded);
  assert.equal(verdict.ok, false);
  assert.match(verdict.reason ?? "", /not for growing it/);
});

test("the slack is exactly what it says", () => {
  const base = `Built ${"word ".repeat(BULLET_MAX_WORDS + 9)}`.trim();
  const at = `${base}${" more".repeat(REWORD_SLACK_WORDS)}`;
  const over = `${base}${" more".repeat(REWORD_SLACK_WORDS + 1)}`;
  assert.equal(checkBulletEdit(base, at).ok, true);
  assert.equal(checkBulletEdit(base, over).ok, false);
});

test("the checks that actually stop fabrication are untouched", () => {
  const short = "Built a scheduler.";
  assert.equal(checkBulletEdit(short, "Built a scheduler serving 40000 users.").ok, false);
  assert.equal(checkBulletEdit(short, "   ").ok, false);
});
