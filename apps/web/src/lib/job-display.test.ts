import assert from "node:assert/strict";
import test from "node:test";
import {
  canSaveToPipeline,
  isGuessedCompanyName,
  isPlaceholderTitle,
  jobDisplay,
} from "./job-display.ts";

/**
 * An import that has not finished reading must not look like a finished save.
 *
 * Both import routes insert the row first and parse afterwards, so between
 * those two moments the title is "Untitled" and the company is "Unknown" or a
 * squashed hostname. /tailor's picker already hides those. The Applications
 * list did not: it showed "Untitled" at "Oraclecloud" alongside real roles,
 * with a status pill and a date, as though the user had chosen to track a job
 * with no name and nothing behind it.
 *
 * Fixtures are generic. No real employer's parse failure is encoded here; the
 * hostname cases below are the SHAPE the guess takes, tested against made-up
 * hosts wherever a name is not needed to make the shape visible.
 */

const parsed = { parse_pending: false, parse_incomplete: false };

test("a job that landed properly is printed exactly as it is", () => {
  const display = jobDisplay({
    title: "Software Engineer, Platform",
    company: { name: "Northwind" },
    source_url: "https://boards.example.com/northwind/jobs/1",
    jd_parsed: parsed,
  });

  assert.deepEqual(display, {
    title: "Software Engineer, Platform",
    company: "Northwind",
    companyIsGuess: false,
    state: null,
    note: null,
    incomplete: false,
  });
});

test("a row still being read says so, in a sentence", () => {
  const display = jobDisplay({
    title: "Untitled",
    company: { name: "Unknown" },
    source_url: "https://jobs.example.com/postings/42",
    jd_parsed: { parse_pending: true },
  });

  assert.equal(display.title, "Still reading this posting");
  assert.equal(display.company, null);
  assert.equal(display.state, "reading");
  assert.equal(display.incomplete, true);
  assert.match(display.note ?? "", /still reading it/i);
});

test("a row whose read failed says that instead, and points somewhere", () => {
  const display = jobDisplay({
    title: "Untitled",
    company: { name: "Unknown" },
    source_url: "https://jobs.example.com/postings/42",
    jd_parsed: { parse_incomplete: true },
  });

  assert.equal(display.state, "unreadable");
  assert.match(display.note ?? "", /could not read this posting/i);
  assert.match(display.note ?? "", /paste the description/i);
});

test("a placeholder with no parse flag at all is still not a finished save", () => {
  // The stranded case: the deferred parse runs in the API process, so a restart
  // mid-parse leaves a row with neither flag set and no title coming.
  const display = jobDisplay({ title: "Untitled", company: { name: "Unknown" } });

  assert.equal(display.incomplete, true);
  assert.equal(display.title, "Still reading this posting");
});

test("a title read off an error page is a placeholder, not a role", () => {
  for (const title of [
    "404 Not Found",
    "Access Denied",
    "Just a moment...",
    "Sign in",
    "Page unavailable",
    "Custom Job Error",
  ]) {
    assert.ok(isPlaceholderTitle(title), title);
  }
});

test("a real role is never mistaken for a placeholder", () => {
  for (const title of [
    "Software Engineer",
    "Product Designer",
    "Site Reliability Engineer II",
    "Registered Nurse, Night Shift",
    "Barista",
    "Grade 5 Teacher",
    // Non-technical and non-English roles must survive the same filter: this
    // product is not only for engineers.
    "Chargé de communication",
    "営業担当",
  ]) {
    assert.ok(!isPlaceholderTitle(title), title);
  }
});

test("a company name taken off the URL host is marked as a guess", () => {
  // The shape `company_hint_from_url` produces for a host it does not know: the
  // registrable label, title cased, so a careers domain turns into a brand.
  assert.ok(isGuessedCompanyName("Examplecloud", "https://jobs.examplecloud.com/postings/7"));
  assert.ok(isGuessedCompanyName("Examplecareers", "https://jobs.examplecareers.com/7"));

  const display = jobDisplay({
    title: "Untitled",
    company: { name: "Examplecloud" },
    source_url: "https://jobs.examplecloud.com/postings/7",
  });
  assert.equal(display.companyIsGuess, true);
  assert.equal(display.incomplete, true);
});

test("an ATS board slug is a real name, not a guess", () => {
  // The guardrail that matters most. A Greenhouse or Lever URL carries the
  // employer's own name in its PATH, and `company_hint_from_url` reads it from
  // there on purpose. Matching the path would have flagged every correctly
  // named row on the two biggest job boards in the index.
  assert.ok(!isGuessedCompanyName("Northwind", "https://boards.example.com/northwind/jobs/9"));
  assert.ok(!isGuessedCompanyName("Northwind", ""));
  assert.ok(!isGuessedCompanyName("", "https://jobs.example.com"));

  const display = jobDisplay({
    title: "Backend Engineer",
    company: { name: "Northwind" },
    source_url: "https://boards.example.com/northwind/jobs/9",
    jd_parsed: parsed,
  });
  assert.equal(display.company, "Northwind");
  assert.equal(display.companyIsGuess, false);
});

test("only the literal placeholders lose the company name", () => {
  for (const name of ["Unknown", "unknown company", "n/a", "none"]) {
    const display = jobDisplay({ title: "Data Analyst", company: { name } });
    assert.equal(display.company, null, name);
  }
});

test("a real title with a description still loading is flagged, not renamed", () => {
  const display = jobDisplay({
    title: "Data Analyst",
    company: { name: "Northwind" },
    source_url: "https://boards.example.com/northwind/jobs/3",
    jd_parsed: { parse_pending: true },
  });

  assert.equal(display.title, "Data Analyst", "a real heading is never overwritten");
  assert.equal(display.incomplete, true);
  assert.equal(display.state, "reading");
});

test("the finder will not save a result it could not read a title from", () => {
  assert.ok(
    canSaveToPipeline({
      title: "Software Engineer",
      source_url: "https://boards.example.com/x/jobs/1",
    }),
  );
  assert.ok(!canSaveToPipeline({ title: "Untitled", source_url: "https://example.com/x" }));
  assert.ok(!canSaveToPipeline({ title: "403 Forbidden", source_url: "https://example.com/x" }));
  assert.ok(!canSaveToPipeline({ title: "", source_url: "https://example.com/x" }));
  // No link is nothing to fetch, so there is nothing to save either.
  assert.ok(!canSaveToPipeline({ title: "Software Engineer", source_url: null }));
});
