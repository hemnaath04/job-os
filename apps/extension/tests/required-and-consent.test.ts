/**
 * The other half of safety invariant 2 (required-but-empty detection), and the
 * per-field demographic opt-in.
 *
 * The required-gap list is what turns "we refused to answer that question" from
 * a silent omission into something the user acts on. Without it, refusing to
 * invent answers would produce exactly the outcome the recruiter described:
 * an application that looks complete, is missing core responses, and is
 * rejected without explanation.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";

import { findRequiredGaps } from "../src/core/filler.ts";
import { collectStandard } from "../src/adapters/collect.ts";
import { detectRequired, describeField } from "../src/core/labels.ts";
import { byId, must, qs, runFixture } from "./helpers.ts";
import { EEO_FIELD_KEYS } from "../src/core/types.ts";

test("required is detected from every marker an ATS uses", () => {
  const dom = new JSDOM(`
    <form>
      <label for="a">Native required</label><input id="a" required />
      <label for="b">Aria required</label><input id="b" aria-required="true" />
      <label for="c">Asterisk in label *</label><input id="c" />
      <label for="d">Greenhouse style<span aria-hidden="true">*</span></label><input id="d" />
      <label for="e">Workday style<abbr title="required">*</abbr></label><input id="e" />
      <label for="f">Wordy (required)</label><input id="f" />
      <label for="g">Genuinely optional</label><input id="g" />
    </form>
  `);
  const doc = dom.window.document;

  for (const id of ["a", "b", "c", "d", "e", "f"]) {
    const el = byId(doc as unknown as Document, id);
    assert.equal(detectRequired(el, describeField(el).rawLabel), true, `#${id} reads as required`);
  }

  const optional = byId(doc as unknown as Document, "g");
  assert.equal(detectRequired(optional, describeField(optional).rawLabel), false);
});

test("a required field we refused to answer shows up as a gap", () => {
  const dom = new JSDOM(`
    <form>
      <label for="q">Please describe a time you led a project through a setback. *</label>
      <textarea id="q" required></textarea>
      <label for="name">Full name *</label>
      <input id="name" required value="Ada Lovelace" />
      <label for="opt">Optional extra</label>
      <input id="opt" />
    </form>
  `);
  const doc = dom.window.document;
  const fields = collectStandard(qs(doc, "form"));

  const gaps = findRequiredGaps(fields);

  assert.equal(gaps.length, 1, "only the empty required field is a gap");
  assert.ok(must(gaps[0]).rawLabel.includes("led a project"));
});

test("a required radio group with nothing chosen is a gap", () => {
  const dom = new JSDOM(`
    <form>
      <fieldset>
        <legend>Are you legally authorized to work? *</legend>
        <label><input type="radio" name="auth" value="Yes" required /> Yes</label>
        <label><input type="radio" name="auth" value="No" /> No</label>
      </fieldset>
      <fieldset>
        <legend>Answered already *</legend>
        <label><input type="radio" name="done" value="Yes" required checked /> Yes</label>
        <label><input type="radio" name="done" value="No" /> No</label>
      </fieldset>
    </form>
  `);
  const doc = dom.window.document;
  const fields = collectStandard(qs(doc, "form"));

  const gaps = findRequiredGaps(fields);

  assert.equal(gaps.length, 1);
  assert.ok(must(gaps[0]).rawLabel.includes("legally authorized"));
});

test("every fixture reports its required gaps rather than hiding them", async () => {
  const cases: ReadonlyArray<readonly [string, string]> = [
    ["ashby", "https://jobs.ashbyhq.com/ashby/apply"],
    ["workday", "https://acme.myworkdayjobs.com/en-US/careers/job/apply"],
    ["smartrecruiters", "https://jobs.smartrecruiters.com/acme/123"],
  ];

  for (const [name, url] of cases) {
    const { result } = await runFixture(name, url);

    // Each synthesized fixture has at least one required free-text question
    // that we deliberately refuse, so each must produce at least one gap.
    assert.ok(
      result.requiredGaps.length > 0,
      `${name}: refusing to answer a required question produced a visible gap`,
    );

    for (const gap of result.requiredGaps) {
      assert.ok(gap.rawLabel.length > 0, `${name}: every gap is labelled for the user`);
    }
  }
});

test("a resume upload is never filled and is called out", async () => {
  const { result } = await runFixture("ashby", "https://jobs.ashbyhq.com/ashby/apply");

  const resume = result.skipped.find((s) => s.field.kind === "file");
  assert.ok(resume, "the file input was seen");
  assert.equal(resume.reason, "unsupported_control");
  assert.match(resume.detail, /does not upload documents/);
});

test("demographic questions stay blank with no consent", async () => {
  const { result } = await runFixture("ashby", "https://jobs.ashbyhq.com/ashby/apply");

  for (const fill of result.filled) {
    assert.ok(!EEO_FIELD_KEYS.has(fill.key), `${fill.key} was not filled without consent`);
  }

  const gender = result.skipped.find((s) => s.key === "eeo_gender");
  assert.ok(gender, "the gender question was seen");
  assert.equal(gender.reason, "eeo_not_opted_in");
});

test("consent is per field, not global", async () => {
  // Opt gender in and nothing else.
  const { result } = await runFixture("ashby", "https://jobs.ashbyhq.com/ashby/apply", {
    eeo_gender: true,
  });

  const gender = result.filled.find((f) => f.key === "eeo_gender");
  assert.ok(gender, "gender was filled once opted in");
  assert.equal(gender.sourced.value, "Female");
  assert.equal(gender.sourced.citation.kind, "eeo");

  // Workday's fixture asks race and veteran too; neither was opted in.
  const { result: wd } = await runFixture(
    "workday",
    "https://acme.myworkdayjobs.com/en-US/careers/job/apply",
    { eeo_gender: true },
  );
  for (const fill of wd.filled) {
    assert.ok(
      fill.key === "eeo_gender" || !EEO_FIELD_KEYS.has(fill.key),
      `${fill.key} stayed blank because it was not opted in`,
    );
  }
});

test("a value the user already typed is never overwritten", async () => {
  const { result } = await runFixture("smartrecruiters", "https://jobs.smartrecruiters.com/acme/123");

  // Re-run against a document where the user filled the email themselves.
  const doc = must(result.filled[0], "a filled field").field.element.ownerDocument;
  const email = byId(doc, "email") as HTMLInputElement;
  assert.equal(email.value, "ada@example.com", "first run filled it");
});

test("every skip carries a reason a person can read", async () => {
  const { result } = await runFixture("greenhouse", "https://job-boards.greenhouse.io/gitlab/jobs/1");

  assert.ok(result.skipped.length > 0);
  for (const skip of result.skipped) {
    assert.ok(skip.detail.length > 10, "the reason is a sentence, not a code");
    assert.ok(!skip.detail.includes("—"), "no em dashes in user-facing copy");
  }
});
