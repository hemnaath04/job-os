import assert from "node:assert/strict";
import { test } from "node:test";
import type { DiscoveryResult } from "../types.ts";
import { atsPostingId, dedupeByJob, normalizeUrl } from "./job-identity.ts";

function row(over: Partial<DiscoveryResult> = {}): DiscoveryResult {
  return {
    source: "greenhouse",
    source_label: "Greenhouse",
    source_id: "x",
    source_url: "https://boards.greenhouse.io/acme/jobs/1",
    title: "Software Engineering Intern",
    company_name: "Acme",
    company_domain: null,
    location: "Remote",
    country_code: "US",
    posted_at: "2026-08-01T00:00:00.000Z",
    description: "",
    technologies: [],
    already_imported: false,
    ...over,
  };
}

// The pair from the live QA: one requisition, two sources, two slots against
// the 60-result cap.
const VERKADA_GREENHOUSE = row({
  source: "greenhouse",
  source_id: "verkada:5211595007",
  source_url: "https://boards.greenhouse.io/verkada/jobs/5211595007",
  company_name: "Verkada",
  location: "San Mateo, CA",
});
const VERKADA_SIMPLIFY = row({
  source: "github",
  source_label: "SIMPLIFYJOBS",
  source_id: "simplify-abc",
  source_url:
    "https://job-boards.greenhouse.io/verkada/jobs/5211595007?gh_src=simplify&utm_source=Simplify",
  company_name: "Verkada",
  location: "San Mateo",
});

test("one Greenhouse requisition has one id, whichever host linked it", () => {
  assert.equal(
    atsPostingId(VERKADA_GREENHOUSE.source_url),
    "greenhouse:verkada:5211595007",
  );
  assert.equal(
    atsPostingId(VERKADA_SIMPLIFY.source_url),
    "greenhouse:verkada:5211595007",
  );
});

test("greenhouse, lever and ashby postings are all readable", () => {
  assert.equal(
    atsPostingId("https://boards.eu.greenhouse.io/stripe/jobs/4012345"),
    "greenhouse:stripe:4012345",
  );
  assert.equal(
    atsPostingId(
      "https://jobs.lever.co/matific/6c2c5f3a-1e2b-4d55-9a11-8f0f2b3c4d5e/apply",
    ),
    "lever:matific:6c2c5f3a-1e2b-4d55-9a11-8f0f2b3c4d5e",
  );
  assert.equal(
    atsPostingId(
      "https://jobs.ashbyhq.com/openai/8a7b6c5d-4e3f-2a1b-9c8d-7e6f5a4b3c2d",
    ),
    "ashby:openai:8a7b6c5d-4e3f-2a1b-9c8d-7e6f5a4b3c2d",
  );
});

test("an apply suffix does not become part of the id", () => {
  assert.equal(
    atsPostingId("https://boards.greenhouse.io/verkada/jobs/5211595007#app"),
    atsPostingId(
      "https://boards.greenhouse.io/verkada/jobs/5211595007/application",
    ),
  );
});

test("a board we cannot read reports no id rather than a wrong one", () => {
  assert.equal(atsPostingId("https://careers.jd.com/en/job/12345"), null);
  assert.equal(atsPostingId("not a url"), null);
  assert.equal(atsPostingId(null), null);
});

test("tracking parameters and host aliases do not change a URL's identity", () => {
  assert.equal(
    normalizeUrl("https://WWW.Example.com/jobs/7/?utm_source=x&gh_src=y#top"),
    normalizeUrl("http://example.com/jobs/7"),
  );
  // A parameter that is part of the address survives.
  assert.ok(normalizeUrl("https://example.com/j?id=7")?.includes("id=7"));
});

test("the same requisition from two sources is kept once", () => {
  const kept = dedupeByJob([VERKADA_GREENHOUSE, VERKADA_SIMPLIFY]);
  assert.equal(kept.length, 1);
  assert.equal(kept[0].source, "greenhouse");
});

test("the caller is told what was dropped and why", () => {
  const dropped: string[] = [];
  dedupeByJob([VERKADA_GREENHOUSE, VERKADA_SIMPLIFY], (_row, key) =>
    dropped.push(key),
  );
  assert.deepEqual(dropped, ["ats:greenhouse:verkada:5211595007"]);
});

test("the same role listed under two spellings of one city is one job", () => {
  const kept = dedupeByJob([
    row({ source_url: "https://a.example.com/1", location: "San Mateo, CA" }),
    row({ source_url: "https://b.example.com/2", location: "San Mateo" }),
  ]);
  assert.equal(kept.length, 1);
});

test("two different roles at one company both survive", () => {
  const kept = dedupeByJob([
    row({ source_url: "https://a.example.com/1", title: "Backend Intern" }),
    row({ source_url: "https://a.example.com/2", title: "Frontend Intern" }),
  ]);
  assert.equal(kept.length, 2);
});

test("rows with no company do not all collapse into each other", () => {
  const kept = dedupeByJob([
    row({ source_url: "https://a.example.com/1", company_name: null, title: "Intern" }),
    row({ source_url: "https://a.example.com/2", company_name: null, title: "Intern" }),
  ]);
  assert.equal(kept.length, 2);
});
