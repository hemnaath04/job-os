/**
 * Safety invariant 1: nothing is ever filled from an unverified fact, and no
 * value is ever composed.
 *
 * The competitor failure this encodes: a paying subscriber found their agent
 * had answered "Please provide an example of your exceptional ability" with a
 * fluent paragraph about leading a team project recognised at a national
 * conference. Nothing in their profile said either thing.
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { parseVerifiedProfile } from "../src/core/profile.ts";
import { buildProfileValues } from "../src/core/mapping.ts";
import { isSourced, sourced, sourcedSubstring, assertVerbatim } from "../src/core/provenance.ts";
import { runFixture, sampleFactRows, sampleProfile, sampleValues } from "./helpers.ts";
import type { VerifiedFact } from "../src/core/types.ts";

test("unverified facts are dropped at the boundary", () => {
  const rows = [
    { id: "a", kind: "contact", title: "Ada Lovelace", verified: true, payload: { email: "ada@example.com" } },
    { id: "b", kind: "contact", title: "Draft Person", verified: false, payload: { email: "draft@example.com" } },
    { id: "c", kind: "education", title: "Unconfirmed degree", payload: {} },
  ];

  const profile = parseVerifiedProfile(rows);

  assert.equal(profile.facts.length, 1, "only the verified row survives");
  assert.equal(profile.facts[0]?.id, "a");
  // A row with no `verified` key at all counts as a draft, not a default-true.
  assert.equal(profile.draftsDropped, 2);
});

test("a draft fact never reaches the value map", () => {
  const rows = sampleFactRows().map((row) => ({ ...(row as object), verified: false }));
  const values = buildProfileValues(parseVerifiedProfile(rows));

  assert.equal(values.size, 0, "an all-draft profile fills nothing at all");
});

test("every value in the map carries a real citation", () => {
  const values = sampleValues();
  assert.ok(values.size > 0);

  for (const [key, value] of values) {
    assert.ok(isSourced(value), `${key} is branded`);
    assert.ok(value.citation.factId.length > 0, `${key} names its fact`);
    assert.ok(value.citation.attribute.length > 0, `${key} names its attribute`);
  }
});

test("a forged value fails the runtime provenance check", () => {
  // What a bug, or a compromised adapter, would have to produce.
  const forged = {
    value: "I led a team project recognised at a national conference",
    citation: { factId: "x", kind: "experience", factLabel: "x", attribute: "x" },
  };

  assert.equal(isSourced(forged), false, "an object literal is not a SourcedValue");
});

test("sourced() refuses to invent text from an empty attribute", () => {
  const fact: VerifiedFact = {
    id: "f1",
    kind: "contact",
    title: "Ada Lovelace",
    org: null,
    startDate: null,
    endDate: null,
    location: null,
    payload: {},
  };

  assert.equal(sourced(fact, "payload.github", undefined), null);
  assert.equal(sourced(fact, "payload.github", ""), null);
  assert.equal(sourced(fact, "payload.github", "   "), null);
  // Structured values are refused rather than flattened, because choosing a
  // format for a list is choosing words the user did not write.
  assert.equal(sourced(fact, "payload.list", ["a", "b"]), null);
  assert.equal(sourced(fact, "payload.obj", { a: 1 }), null);
});

test("a derived value must be verbatim inside its source", () => {
  const fact: VerifiedFact = {
    id: "f1",
    kind: "contact",
    title: "Ada Lovelace",
    org: null,
    startDate: null,
    endDate: null,
    location: null,
    payload: {},
  };

  const first = sourcedSubstring(fact, "payload.name", "Ada Lovelace", "Ada");
  assert.equal(first?.value, "Ada");

  // The exact failure mode being guarded: a plausible embellishment.
  assert.throws(
    () => sourcedSubstring(fact, "payload.name", "Ada Lovelace", "Dr Ada Lovelace"),
    /provenance violation/,
  );
  assert.throws(() => assertVerbatim("Computer Science", "Computer Engineering"), /provenance violation/);
});

test("the exceptional ability question is never answered", async () => {
  const { result } = await runFixture("ashby", "https://jobs.ashbyhq.com/ashby/apply");

  const essay = result.skipped.find((s) => s.field.rawLabel.includes("exceptional ability"));
  assert.ok(essay, "the question was seen");
  assert.equal(essay.reason, "free_text_answer");

  const filledLabels = result.filled.map((f) => f.field.rawLabel);
  assert.ok(
    !filledLabels.some((l) => l.includes("exceptional ability")),
    "and nothing was written into it",
  );
});

test("no filled value is longer than the fact it came from", async () => {
  // A cheap structural proxy for "nothing was elaborated". Every value is
  // either the whole attribute or a slice of it, so it can never grow.
  const profile = sampleProfile();
  const { result } = await runFixture("greenhouse", "https://job-boards.greenhouse.io/gitlab/jobs/1");

  const factText = new Map(profile.facts.map((f) => [f.id, JSON.stringify(f)]));

  for (const fill of result.filled) {
    const source = factText.get(fill.sourced.citation.factId);
    assert.ok(source, `${fill.key} cites a fact that exists`);
    assert.ok(
      fill.sourced.value.length <= source.length,
      `${fill.key} did not grow beyond its source`,
    );
  }
});

test("every fixture fills only keys present in the value map", async () => {
  const cases: ReadonlyArray<readonly [string, string]> = [
    ["greenhouse", "https://job-boards.greenhouse.io/gitlab/jobs/1"],
    ["lever", "https://jobs.lever.co/leverdemo/abc/apply"],
    ["ashby", "https://jobs.ashbyhq.com/ashby/apply"],
    ["workday", "https://acme.myworkdayjobs.com/en-US/careers/job/apply"],
    ["smartrecruiters", "https://jobs.smartrecruiters.com/acme/123"],
  ];

  const values = sampleValues();

  for (const [name, url] of cases) {
    const { result } = await runFixture(name, url);
    for (const fill of result.filled) {
      const known = values.get(fill.key);
      assert.ok(known, `${name}: ${fill.key} exists in the profile map`);
      assert.equal(
        fill.sourced.value,
        known.value,
        `${name}: ${fill.key} was written verbatim from the profile`,
      );
    }
  }
});
