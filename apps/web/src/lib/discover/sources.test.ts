import assert from "node:assert/strict";
import { test } from "node:test";
import type { DiscoveryResult, DiscoverySearchResponse } from "../types.ts";
import { describeNarrowing, mergeDiscoveryResponses } from "./sources.ts";

function row(over: Partial<DiscoveryResult> = {}): DiscoveryResult {
  return {
    source: "greenhouse",
    source_label: null,
    source_id: "1",
    source_url: "https://boards.greenhouse.io/acme/jobs/1",
    title: "Software Engineering Intern",
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

function part(
  results: DiscoveryResult[],
  source_counts: Record<string, number> = {},
): DiscoverySearchResponse {
  return { results, source_counts, errors: [] };
}

test("the same requisition from two halves takes one slot, not two", () => {
  const merged = mergeDiscoveryResponses(
    [
      part([
        row({
          source: "greenhouse",
          source_url: "https://boards.greenhouse.io/verkada/jobs/5211595007",
          company_name: "Verkada",
          location: "San Mateo, CA",
        }),
      ]),
      part([
        row({
          source: "github",
          source_url:
            "https://job-boards.greenhouse.io/verkada/jobs/5211595007?gh_src=simplify",
          company_name: "Verkada",
          location: "San Mateo",
        }),
      ]),
    ],
    ["greenhouse", "github"],
    60,
  );
  assert.equal(merged.results.length, 1);
  assert.equal(merged.merge?.received, 2);
  assert.equal(merged.merge?.duplicates, 1);
});

test("deduping happens before the cap, so duplicates cannot eat the page", () => {
  // Two halves of five rows each, every row of the second a copy of the first.
  const originals = Array.from({ length: 5 }, (_, i) =>
    row({
      source: "greenhouse",
      source_url: `https://boards.greenhouse.io/acme/jobs/${i}`,
      title: `Intern ${i}`,
    }),
  );
  const copies = originals.map((r) =>
    row({ ...r, source: "github", source_url: `${r.source_url}?utm_source=simplify` }),
  );
  const merged = mergeDiscoveryResponses(
    [part(originals), part(copies)],
    ["greenhouse", "github"],
    5,
  );
  assert.equal(merged.results.length, 5);
  assert.deepEqual(
    merged.results.map((r) => r.title),
    ["Intern 0", "Intern 1", "Intern 2", "Intern 3", "Intern 4"],
  );
});

test("the merge reports what the sources handed over, not what survived", () => {
  const parts = [
    part(
      Array.from({ length: 40 }, (_, i) =>
        row({ source_url: `https://a.example.com/${i}`, title: `A${i}` }),
      ),
      { index: 40 },
    ),
    part(
      Array.from({ length: 69 }, (_, i) =>
        row({ source_url: `https://b.example.com/${i}`, title: `B${i}` }),
      ),
      { greenhouse: 69 },
    ),
  ];
  const merged = mergeDiscoveryResponses(parts, ["index", "greenhouse"], 60);
  assert.equal(merged.results.length, 60);
  // The exact shape of the QA report: per-source counts summing to 109 beside
  // a header that said 60, with nothing accounting for the difference.
  const summed = Object.values(merged.source_counts).reduce((a, b) => a + b, 0);
  assert.equal(summed, 109);
  assert.equal(merged.merge?.received, 109);
  assert.equal(merged.merge?.capped, true);
});

test("a search that fit on one page is not described as capped", () => {
  const merged = mergeDiscoveryResponses(
    [part([row({ source_url: "https://a.example.com/1" })])],
    ["greenhouse"],
    60,
  );
  assert.equal(merged.merge?.capped, false);
  assert.equal(merged.merge?.duplicates, 0);
});

test("a source that reports zero still appears in the counts", () => {
  const merged = mergeDiscoveryResponses([part([])], ["index", "greenhouse"], 60);
  assert.deepEqual(merged.source_counts, { index: 0, greenhouse: 0 });
});

test("the header names only the narrowing that happened", () => {
  assert.equal(
    describeNarrowing({ received: 109, duplicates: 22, capped: true }, 3, 60),
    " · 22 duplicates merged · 3 placeholders hidden · capped at 60",
  );
  assert.equal(
    describeNarrowing({ received: 109, duplicates: 1, capped: false }, 0, 60),
    " · 1 duplicate merged",
  );
  assert.equal(
    describeNarrowing({ received: 5, duplicates: 0, capped: false }, 0, 60),
    "",
  );
});
