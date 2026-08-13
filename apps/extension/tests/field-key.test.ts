/**
 * The free-text gate in isolation.
 *
 * This is the single most important table in the extension: it decides which
 * questions are answerable at all. A false negative here (deciding a custom
 * question is a known field) is how an autofiller ends up writing prose into a
 * question about someone's exceptional ability.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";

import { isEssayQuestion, resolveFieldKey } from "../src/core/field-key.ts";
import { describeField } from "../src/core/labels.ts";
import { byId, qs } from "./helpers.ts";

function field(html: string) {
  const dom = new JSDOM(`<form>${html}</form>`);
  const el = qs(dom.window.document, "input, textarea, select");
  return describeField(el);
}

test("ordinary identity questions resolve", () => {
  const cases: ReadonlyArray<readonly [string, string]> = [
    ['<label for="a">First Name *</label><input id="a">', "first_name"],
    ['<label for="a">Last Name</label><input id="a">', "last_name"],
    ['<label for="a">Email Address</label><input id="a">', "email"],
    ['<label for="a">Mobile number</label><input id="a">', "phone"],
    ['<label for="a">LinkedIn Profile URL</label><input id="a">', "linkedin_url"],
    ['<label for="a">Zip Code</label><input id="a">', "postal_code"],
    ['<label for="a">Cumulative GPA</label><input id="a">', "gpa"],
    ['<input id="a" autocomplete="given-name">', "first_name"],
    ['<input id="a" type="email">', "email"],
  ];

  for (const [html, expected] of cases) {
    assert.equal(resolveFieldKey(field(html)).key, expected, html);
  }
});

test("custom employer questions resolve to nothing", () => {
  const questions = [
    "Please provide an example of your exceptional ability.",
    "Why do you want to work at this company?",
    "Tell us about a time you disagreed with a manager.",
    "Describe your ideal working environment.",
    "What interests you about this role?",
    "How did you hear about this position?",
    "In your own words, what makes you a good fit?",
    "Anything else you would like us to know?",
    "Walk us through your most significant project.",
    "Share a time you failed and what you learned.",
  ];

  for (const q of questions) {
    const f = field(`<label for="a">${q}</label><input id="a">`);
    const resolved = resolveFieldKey(f);
    assert.equal(resolved.key, null, `refused: ${q}`);
    assert.equal(resolved.refusal, "essay", `classified as prose: ${q}`);
  }
});

test("a textarea is prose unless it is one of the few keys that belong in one", () => {
  // An unrecognised textarea is prose by shape, even with a short label.
  const custom = field('<label for="a">Your pitch</label><textarea id="a"></textarea>');
  assert.equal(isEssayQuestion(custom), true);

  // An address genuinely does get a textarea on some forms.
  const address = field('<label for="a">Street address</label><textarea id="a"></textarea>');
  assert.equal(isEssayQuestion(address), false);
  assert.equal(resolveFieldKey(address).key, "address_line1");
});

test("a long question mark ending is prose even with a familiar word in it", () => {
  // Mentions "company", which maps to current_company on its own.
  const f = field(
    '<label for="a">Which company that you have worked for taught you the most, and what did it teach you?</label><input id="a">',
  );
  assert.equal(resolveFieldKey(f).key, null);
});

test("an adapter hint cannot open the door to a prose question", () => {
  const f = field(
    '<label for="a">Please describe a time you led a project.</label><textarea id="a"></textarea>',
  );
  // Even told explicitly that this is the current title, the gate refuses.
  const resolved = resolveFieldKey(f, "current_title");
  assert.equal(resolved.key, null);
  assert.equal(resolved.refusal, "essay");
});

test("a compound legal question is refused rather than half answered", () => {
  const f = field(
    '<label for="a">Are you legally authorized to work in the US, and will you require sponsorship?</label><select id="a"><option>Yes</option><option>No</option></select>',
  );
  const resolved = resolveFieldKey(f);
  assert.equal(resolved.key, null);
  assert.ok(resolved.refusal !== null && ["ambiguous", "essay"].includes(resolved.refusal));
});

test("the two authorization questions resolve separately when asked separately", () => {
  const authorized = field(
    '<label for="a">Are you legally authorized to work in the United States?</label><select id="a"><option>Yes</option></select>',
  );
  assert.equal(resolveFieldKey(authorized).key, "work_authorized");

  const sponsorship = field(
    '<label for="a">Will you now or in the future require visa sponsorship?</label><select id="a"><option>Yes</option></select>',
  );
  assert.equal(resolveFieldKey(sponsorship).key, "requires_sponsorship");
});

test("demographic questions are recognised so they can be refused by name", () => {
  const cases: ReadonlyArray<readonly [string, string]> = [
    ["Gender", "eeo_gender"],
    ["Race or ethnicity", "eeo_race"],
    ["Are you Hispanic or Latino?", "eeo_hispanic"],
    ["Protected veteran status", "eeo_veteran"],
    ["Disability status", "eeo_disability"],
  ];

  for (const [label, expected] of cases) {
    const f = field(`<label for="a">${label}</label><select id="a"><option>Yes</option></select>`);
    assert.equal(resolveFieldKey(f).key, expected, label);
  }
});

test("labels are resolved from ARIA before anything structural", () => {
  // aria-labelledby beats a wrapping label, which is what Ashby and Workday
  // rely on and what a class-based selector would miss entirely.
  const dom = new JSDOM(`
    <form>
      <span id="lbl">Email Address</span>
      <label>Wrong label <input id="a" aria-labelledby="lbl"></label>
    </form>
  `);
  const el = byId(dom.window.document as unknown as Document, "a");
  assert.equal(resolveFieldKey(describeField(el)).key, "email");
});
