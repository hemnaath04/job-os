import assert from "node:assert/strict";
import { test } from "node:test";
import { BULLET_MAX_WORDS, checkBulletEdit } from "./bullet-edit-guard.ts";

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
