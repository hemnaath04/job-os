import assert from "node:assert/strict";
import { test } from "node:test";
import {
  BULLET_MAX_WORDS,
  countWords,
  openingVerb,
  vaultQuality,
  vaultQualitySummary,
} from "./bullet-quality.ts";
import type { ProfileFact } from "./types.ts";

// His real bullets, at their real lengths. Eleven of fifteen run past the cap
// and seven of fifteen open with "Built", which is what the tailored resume
// inherits and what nothing ever showed him.
const JOB_OS_TRACKS =
  "Built job.os (live at jobs.hemnaath.tech): tracks applications on a Kanban " +
  "board, tailors a master resume to any posting, and crawls Greenhouse, Lever, " +
  "Ashby, and SmartRecruiters overnight to score roles against a verified profile.";
const JOB_OS_ENGINE =
  "Built a fact-grounded tailoring engine that rewrites bullets only from " +
  "verified facts, with deterministic checks that strip unverified numbers, " +
  "reject new employers or metrics, and cap bullet growth so it cannot invent " +
  "experience or pad to a job description.";
const JOB_OS_INDEX =
  "Replaced a live per-search fan-out (8-60s, 100MB+ per search) with a " +
  "pre-built overnight index and per-token liveness tracking that ranks a full " +
  "page in under 300ms, plus a deterministic in-browser fit score with alias matching.";
const CLAIMFARM =
  "Built an AI agent that turns a farmer's crop photo into a filed insurance " +
  "claim in under a minute: a vision model grades damage, weather corroborates " +
  "it, embeddings retrieve similar claims, and an LLM drafts a localized " +
  "confirmation in 10 languages, behind a 6-signal fraud check.";
const EPAM_SHORT =
  "Drove daily root-cause analysis with developers on failing tests, raising " +
  "coverage on the pricing engine and shortening time-to-fix on regressions.";

function bullet(id: string, text: string) {
  return {
    id,
    text,
    target_role: null,
    metric_verified: true,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  };
}

function fact(id: string, title: string, texts: string[]): ProfileFact {
  return {
    id,
    kind: "project",
    title,
    org: null,
    start_date: null,
    end_date: null,
    location: null,
    payload: {},
    verified: true,
    source_url: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    bullets: texts.map((text, index) => bullet(`${id}-${index}`, text)),
  } as ProfileFact;
}

const VAULT = [
  fact("jobos", "job.os", [JOB_OS_TRACKS, JOB_OS_ENGINE, JOB_OS_INDEX]),
  fact("claimfarm", "ClaimFarm", [CLAIMFARM]),
  fact("epam", "EPAM", [EPAM_SHORT]),
];

test("counts the words the resume will count", () => {
  assert.equal(countWords(CLAIMFARM), 46);
  assert.equal(countWords("  spaced   out  "), 2);
  assert.equal(countWords(""), 0);
});

test("reads the opening verb, not the punctuation", () => {
  assert.equal(openingVerb(JOB_OS_TRACKS), "built");
  assert.equal(openingVerb("  (Re)built the thing."), "re");
  assert.equal(openingVerb("123 things"), "things");
  assert.equal(openingVerb(""), "");
});

test("names the bullet that runs long, and how long", () => {
  const { byBullet } = vaultQuality(VAULT);
  const issues = byBullet.get("claimfarm-0") ?? [];
  assert.deepEqual(issues, [
    { kind: "too_long", detail: `46 of ${BULLET_MAX_WORDS} words` },
  ]);
});

test("leaves a bullet that is already fine alone", () => {
  assert.equal(vaultQuality(VAULT).byBullet.has("epam-0"), false);
});

test("repetition is judged inside the fact, where the resume prints them together", () => {
  const { byBullet } = vaultQuality(VAULT);
  // Both job.os bullets that open "Built" are marked; the one opening
  // "Replaced" is not, though it is over the cap.
  for (const id of ["jobos-0", "jobos-1"]) {
    assert.ok((byBullet.get(id) ?? []).some((i) => i.kind === "repeated_opener"));
  }
  assert.ok(!(byBullet.get("jobos-2") ?? []).some((i) => i.kind === "repeated_opener"));
  // ClaimFarm's only bullet has nothing to repeat against inside its own fact.
  assert.ok(
    !(byBullet.get("claimfarm-0") ?? []).some((i) => i.kind === "repeated_opener"),
  );
});

test("the page-wide verb is counted across facts, not inside one", () => {
  const quality = vaultQuality(VAULT);
  // Three of five bullets open "Built", across two different projects.
  assert.deepEqual(quality.dominantOpener, { verb: "built", count: 3 });
  assert.equal(quality.overCap, 4);
  assert.equal(quality.totalBullets, 5);
});

test("a verb used twice on a varied profile is left alone", () => {
  const varied = [
    fact("a", "A", ["Built one thing.", "Wired another thing."]),
    fact("b", "B", ["Built a third thing.", "Traced a fourth.", "Cut a fifth."]),
    fact("c", "C", ["Shipped a sixth.", "Tuned a seventh."]),
  ];
  assert.equal(vaultQuality(varied).dominantOpener, null);
});

test("says it plainly, or says nothing", () => {
  assert.equal(
    vaultQualitySummary(vaultQuality(VAULT)),
    '4 of 5 bullets run past 30 words, and "Built" opens 3. Tailoring prints ' +
      "your wording as it stands, so this is where to change it.",
  );
  const clean = [fact("a", "A", [EPAM_SHORT])];
  assert.equal(vaultQualitySummary(vaultQuality(clean)), null);
});

test("an empty vault reports nothing rather than dividing by zero", () => {
  const quality = vaultQuality([]);
  assert.equal(quality.totalBullets, 0);
  assert.equal(quality.dominantOpener, null);
  assert.equal(vaultQualitySummary(quality), null);
});
