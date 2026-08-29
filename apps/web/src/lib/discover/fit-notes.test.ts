import assert from "node:assert/strict";
import test from "node:test";

import { fitNotesFrom } from "./fit-notes.ts";
import type { IndexMatchScore } from "../types.ts";

const line = (over: Partial<IndexMatchScore["top_reasons"][number]>) => ({
  axis: "education",
  points: -7,
  reason: "undergraduate_only_posting",
  detail:
    "the posting is open to students pursuing a bachelors and the profile is enrolled in a masters",
  subject: null,
  evidence: null,
  ...over,
});

const match = (over: Partial<IndexMatchScore> = {}): IndexMatchScore => ({
  overall: 44,
  raw_overall: 44,
  axes: [],
  top_reasons: [],
  confidence: "high",
  confidence_reasons: [],
  blockers: [],
  matched_skills: [],
  missing_skills: [],
  ...over,
});

test("the education mismatch reaches the card", () => {
  // The reason this exists. An undergraduate-only internship scored lower for
  // a masters student and nothing said why: the only line that explains it
  // lives on an axis the adapter used to drop.
  const notes = fitNotesFrom(match({ top_reasons: [line({})] }));

  assert.equal(notes.length, 1);
  assert.equal(notes[0].reason, "undergraduate_only_posting");
  assert.match(notes[0].detail, /bachelors/);
  assert.equal(notes[0].blocking, false);
});

test("a blocker is marked as one rather than as another point lost", () => {
  // The scorer keeps blockers out of the number deliberately: a candidate who
  // needs sponsorship wants to be told, not marked down. So the only way they
  // learn it is if a surface prints it.
  const notes = fitNotesFrom(
    match({
      blockers: [
        line({ axis: "bonus", points: 0, reason: "no_visa_sponsorship", detail: "the posting states it does not sponsor visas" }),
      ],
    }),
  );

  assert.equal(notes[0].blocking, true);
  assert.equal(notes[0].reason, "no_visa_sponsorship");
});

test("skills lines are left out, because the card already prints them", () => {
  const notes = fitNotesFrom(
    match({
      top_reasons: [
        line({ axis: "skills", reason: "missing_required_skill", detail: "Kubernetes" }),
        line({}),
      ],
    }),
  );

  assert.deepEqual(
    notes.map((n) => n.reason),
    ["undergraduate_only_posting"],
  );
});

test("a line that gained points is not reported as a reason the score is low", () => {
  const notes = fitNotesFrom(
    match({ top_reasons: [line({ points: 8, reason: "title_exact_match" })] }),
  );

  assert.deepEqual(notes, []);
});

test("blockers come first and are never dropped by the cap", () => {
  // A card has no room for a breakdown, so the list is capped. A blocker
  // losing its place to a deduction would hide the one fact that rules the
  // application out.
  const notes = fitNotesFrom(
    match({
      blockers: [line({ reason: "citizenship_required", detail: "requires citizenship" })],
      top_reasons: [
        line({ axis: "experience", reason: "years_short", detail: "a" }),
        line({ axis: "industry", reason: "industry_miss", detail: "b" }),
        line({ axis: "education", reason: "degree_short", detail: "c" }),
      ],
    }),
  );

  assert.equal(notes.length, 3);
  assert.equal(notes[0].reason, "citizenship_required");
  assert.equal(notes[0].blocking, true);
});

test("a score with nothing to explain says nothing", () => {
  assert.deepEqual(fitNotesFrom(match()), []);
});
