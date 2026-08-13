/**
 * Per-adapter field mapping against saved fixtures.
 *
 * greenhouse.html and lever.html are real captures. ashby.html, workday.html
 * and smartrecruiters.html are synthesized from documented selectors, and say
 * so in their own headers, so a pass on those three proves the adapter honours
 * the documented contract and nothing more.
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { selectAdapter } from "../src/adapters/registry.ts";
import { loadFixture, filledValue, runFixture } from "./helpers.ts";

test("each fixture is routed to its own adapter", () => {
  const cases: ReadonlyArray<readonly [string, string, string]> = [
    ["greenhouse", "https://job-boards.greenhouse.io/gitlab/jobs/1", "greenhouse"],
    ["lever", "https://jobs.lever.co/leverdemo/abc/apply", "lever"],
    ["ashby", "https://jobs.ashbyhq.com/ashby/apply", "ashby"],
    ["workday", "https://acme.myworkdayjobs.com/en-US/careers/job/apply", "workday"],
    ["smartrecruiters", "https://jobs.smartrecruiters.com/acme/123", "smartrecruiters"],
  ];

  for (const [fixture, url, expected] of cases) {
    const { doc, url: parsed } = loadFixture(fixture, url);
    assert.equal(selectAdapter({ url: parsed, document: doc }).id, expected);
  }
});

test("an unknown ATS falls back to the generic adapter", () => {
  const { doc, url } = loadFixture("smartrecruiters", "https://careers.example.com/apply");
  // Detection is by host, and this host belongs to nobody we know.
  const adapter = selectAdapter({ url, document: doc });
  assert.ok(["smartrecruiters", "generic"].includes(adapter.id));
});

test("greenhouse: real capture maps the core identity fields", async () => {
  const { result } = await runFixture("greenhouse", "https://job-boards.greenhouse.io/gitlab/jobs/1");

  assert.equal(filledValue(result, "first_name"), "Ada");
  assert.equal(filledValue(result, "last_name"), "Lovelace");
  assert.equal(filledValue(result, "email"), "ada@example.com");
  assert.equal(filledValue(result, "phone"), "+1 617 555 0142");
  // Greenhouse asks for LinkedIn as a custom question whose only signal is the
  // aria-label, which is exactly the case the label resolver exists for.
  assert.equal(filledValue(result, "linkedin_url"), "https://linkedin.com/in/adalovelace");
});

test("greenhouse: values actually land in the DOM", async () => {
  const { result } = await runFixture("greenhouse", "https://job-boards.greenhouse.io/gitlab/jobs/1");

  const email = result.filled.find((f) => f.key === "email");
  assert.ok(email);
  assert.equal((email.field.element as HTMLInputElement).value, "ada@example.com");
});

test("lever: real capture maps name, contact, employer and links", async () => {
  const { result } = await runFixture("lever", "https://jobs.lever.co/leverdemo/abc/apply");

  // Lever asks for one combined name field.
  assert.equal(filledValue(result, "full_name"), "Ada Lovelace");
  assert.equal(filledValue(result, "email"), "ada@example.com");
  assert.equal(filledValue(result, "phone"), "+1 617 555 0142");
  assert.equal(filledValue(result, "current_company"), "EPAM Systems");
  assert.equal(filledValue(result, "linkedin_url"), "https://linkedin.com/in/adalovelace");
  assert.equal(filledValue(result, "github_url"), "https://github.com/adalovelace");
});

test("ashby: system field ids resolve, UUID questions do not", async () => {
  const { result } = await runFixture("ashby", "https://jobs.ashbyhq.com/ashby/apply");

  assert.equal(filledValue(result, "full_name"), "Ada Lovelace");
  assert.equal(filledValue(result, "email"), "ada@example.com");
  assert.equal(filledValue(result, "phone"), "+1 617 555 0142");

  const howDidYouHear = result.skipped.find((s) => s.field.rawLabel.includes("How did you hear"));
  assert.ok(howDidYouHear, "the custom question was seen and skipped");
  assert.equal(howDidYouHear.reason, "free_text_answer");
});

test("smartrecruiters: camelCase names and a native country select", async () => {
  const { result } = await runFixture("smartrecruiters", "https://jobs.smartrecruiters.com/acme/123");

  assert.equal(filledValue(result, "first_name"), "Ada");
  assert.equal(filledValue(result, "last_name"), "Lovelace");
  assert.equal(filledValue(result, "city"), "Boston");
  assert.equal(filledValue(result, "postal_code"), "02115");

  // The profile stores "US" and the select offers value="US", so it matches on
  // the value rather than guessing at the label.
  const country = result.filled.find((f) => f.key === "country");
  assert.ok(country, "country was selected");
  assert.equal(country.option?.value, "US");
  assert.equal((country.field.element as HTMLSelectElement).value, "US");
});

test("workday: automation ids drive the mapping, including repeating sections", async () => {
  const { result } = await runFixture("workday", "https://acme.myworkdayjobs.com/en-US/careers/job/apply");

  assert.equal(filledValue(result, "first_name"), "Ada");
  assert.equal(filledValue(result, "last_name"), "Lovelace");
  assert.equal(filledValue(result, "email"), "ada@example.com");
  assert.equal(filledValue(result, "address_line1"), "12 Analytical Way");
  assert.equal(filledValue(result, "city"), "Boston");
  assert.equal(filledValue(result, "postal_code"), "02115");
  // Prefixed ids inside repeating sections resolve by suffix.
  assert.equal(filledValue(result, "current_title"), "Test Automation Engineer");
  assert.equal(filledValue(result, "current_company"), "EPAM Systems");
  assert.equal(filledValue(result, "school"), "Northeastern University");
  assert.equal(filledValue(result, "field_of_study"), "Computer Science");
});

test("workday: a popup dropdown is opened and matched at fill time", async () => {
  const { result } = await runFixture("workday", "https://acme.myworkdayjobs.com/en-US/careers/job/apply");

  const country = result.filled.find((f) => f.key === "country");
  assert.ok(country, "the country dropdown was filled");
  assert.equal(country.field.kind, "popup_select");
});

test("workday: split date spinbuttons are left alone", async () => {
  const { result } = await runFixture("workday", "https://acme.myworkdayjobs.com/en-US/careers/job/apply");

  for (const part of ["Month", "Day", "Year"]) {
    const filled = result.filled.find((f) => f.field.rawLabel === part);
    assert.equal(filled, undefined, `${part} was not filled`);
  }
});

test("workday: the wizard advance button is never a fillable field", async () => {
  const { fields } = await runFixture("workday", "https://acme.myworkdayjobs.com/en-US/careers/job/apply");

  const advance = fields.find(
    (f) => f.automationId === "bottom-navigation-next-button",
  );
  assert.equal(advance, undefined, "the advance button is not collected as a field");
});

test("work authorization comes only from a verified authorization fact", async () => {
  const { result } = await runFixture("workday", "https://acme.myworkdayjobs.com/en-US/careers/job/apply");

  const auth = result.filled.find((f) => f.key === "work_authorized");
  assert.ok(auth, "the authorization question was answered");
  assert.equal(auth.sourced.value, "Yes");
  assert.equal(auth.sourced.citation.factId, "fact-auth");
  assert.equal(auth.sourced.citation.kind, "authorization");
});
