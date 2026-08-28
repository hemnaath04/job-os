import assert from "node:assert/strict";
import { test } from "node:test";
import type { DiscoveryResult } from "../types.ts";
import {
  isPlaceholderPosting,
  rankByIntent,
  relevanceOf,
  searchIntent,
  stem,
} from "./relevance.ts";

const INTERN_SEARCH = searchIntent(["software engineer intern"]);

function row(title: string, over: Partial<DiscoveryResult> = {}): DiscoveryResult {
  return {
    source: "greenhouse",
    source_label: null,
    source_id: title,
    source_url: `https://boards.greenhouse.io/acme/jobs/${title.length}`,
    title,
    company_name: "Acme",
    company_domain: null,
    location: "Remote",
    country_code: "US",
    posted_at: "2026-08-20T00:00:00.000Z",
    description: "",
    technologies: [],
    already_imported: false,
    ...over,
  };
}

/**
 * The page that started this, in the order it actually rendered. Every title
 * here is real, and the first eight of them are what a search for "software
 * engineer intern" opened with.
 */
const QA_PAGE = [
  row("Platform Security Engineer", { company_name: "Glean" }),
  row("Principal Enterprise Technology Architect"),
  row("Localization Manager"),
  row("Executive Assistant to the CRO"),
  row("Director of Litigation"),
  row("Revenue Accountant Lead"),
  row("Equipment Maintenance", { company_name: "JD.com" }),
  row("Various roles", {
    company_name: "Sherborne Schools",
    description: "Keep an eye on our website for future vacancies.",
  }),
  row("Software Engineer Intern, Summer 2027", { company_name: "Ramp" }),
  row("Software Engineering Intern", { company_name: "Verkada" }),
];

test("a title that matches the search outranks one that only mentions it", () => {
  const onTitle = relevanceOf(row("Software Engineering Intern"), INTERN_SEARCH);
  const inBodyOnly = relevanceOf(
    row("Director of Litigation", {
      description: "Our software engineer intern programme is thriving.",
    }),
    INTERN_SEARCH,
  );
  assert.ok(onTitle.tier > inBodyOnly.tier, `${onTitle.tier} vs ${inBodyOnly.tier}`);
});

test("engineering and engineer are the same word for ranking", () => {
  assert.equal(stem("engineering"), "engineer");
  assert.equal(stem("internships"), "intern");
  assert.equal(stem("analysts"), "analyst");
  // Short words are left alone: stemming "ring" to "r" helps nobody.
  assert.equal(stem("ring"), "ring");
  assert.equal(stem("internal"), "internal");
});

test("a senior title on an intern search is a contradiction, not a near miss", () => {
  const principal = relevanceOf(row("Principal Enterprise Technology Architect"), INTERN_SEARCH);
  const director = relevanceOf(row("Director of Litigation"), INTERN_SEARCH);
  const manager = relevanceOf(row("Localization Manager"), INTERN_SEARCH);
  const intern = relevanceOf(row("Software Engineering Intern"), INTERN_SEARCH);
  for (const senior of [principal, director, manager]) {
    assert.ok(senior.tier < intern.tier, `${senior.tier} !< ${intern.tier}`);
    assert.ok(senior.tier < 0, `${senior.tier} should be negative`);
  }
});

test("an intern title is safe even when it also says lead", () => {
  const verdict = relevanceOf(row("Intern - Technical Lead Program"), INTERN_SEARCH);
  assert.ok(verdict.reasons.includes("early-career role, as searched"));
  assert.ok(verdict.tier >= 0, `${verdict.tier}`);
});

test("placeholder postings are recognised by title", () => {
  for (const title of [
    "Various roles",
    "General Application",
    "Talent Community",
    "Future Opportunities",
    "Don't see a role for you?",
    "Jobs",
  ]) {
    assert.equal(isPlaceholderPosting({ title }), true, title);
  }
});

test("a short title plus a nothing-to-apply-for body is a placeholder", () => {
  assert.equal(
    isPlaceholderPosting({
      title: "Open positions",
      description: "Keep an eye on our website.",
    }),
    true,
  );
});

test("a real posting is not mistaken for a placeholder", () => {
  for (const title of [
    "Software Engineering Intern",
    "Various Data Pipelines Engineer",
    "Site Reliability Engineer, Platform",
  ]) {
    assert.equal(
      isPlaceholderPosting({ title, description: "You will build things." }),
      false,
      title,
    );
  }
});

test("the QA page reorders so the internships lead and the placeholder goes", () => {
  const ranked = rankByIntent(QA_PAGE, INTERN_SEARCH, () => 0);
  assert.equal(
    ranked.some((r) => r.title === "Various roles"),
    false,
    "the placeholder should not be on the page at all",
  );
  assert.deepEqual(
    ranked.slice(0, 2).map((r) => r.title),
    ["Software Engineer Intern, Summer 2027", "Software Engineering Intern"],
  );
  // And every seniority mismatch sinks below every row that is not one,
  // rather than five of them opening the page.
  const seniorityMismatches = [
    "Principal Enterprise Technology Architect",
    "Localization Manager",
    "Executive Assistant to the CRO",
    "Director of Litigation",
    "Revenue Accountant Lead",
  ];
  const firstMismatch = Math.min(
    ...seniorityMismatches.map((t) => ranked.findIndex((r) => r.title === t)),
  );
  assert.equal(firstMismatch, ranked.length - seniorityMismatches.length);
});

test("the caller's own comparator still decides within a tier", () => {
  const older = row("Software Engineering Intern", {
    source_url: "https://a.example.com/1",
    posted_at: "2026-01-01T00:00:00.000Z",
  });
  const newer = row("Software Engineer Intern", {
    source_url: "https://a.example.com/2",
    posted_at: "2026-08-01T00:00:00.000Z",
  });
  const ranked = rankByIntent(
    [older, newer],
    INTERN_SEARCH,
    (a, b) => Date.parse(b.posted_at!) - Date.parse(a.posted_at!),
  );
  assert.deepEqual(ranked.map((r) => r.source_url), [
    "https://a.example.com/2",
    "https://a.example.com/1",
  ]);
});

test("browsing with no keywords is left in the caller's order", () => {
  const blank = searchIntent([]);
  const rows = [row("Director of Litigation"), row("Software Engineering Intern")];
  const ranked = rankByIntent(rows, blank, () => 0);
  assert.deepEqual(
    ranked.map((r) => r.title),
    ["Director of Litigation", "Software Engineering Intern"],
  );
});

test("a non-early-career search does not penalise senior titles", () => {
  const staff = searchIntent(["staff software engineer"]);
  const verdict = relevanceOf(row("Staff Software Engineer"), staff);
  assert.ok(verdict.tier >= 2, `${verdict.tier}`);
});
