import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { isAtCoverageCeiling, partitionMissing } from "./ats-coverage.ts";

describe("isAtCoverageCeiling", () => {
  it("calls the reported ByteDance run finished, not failed", () => {
    // The run that prompted this: Keyword Match 27 painted red next to a
    // review of 98, when 27 was everything the profile could evidence against
    // that posting. The ring's absolute 75/50 scale had no way to know that.
    assert.equal(isAtCoverageCeiling(26.7, 26.7), true);
  });

  it("compares rounded, so the ring's own number cannot fall short of itself", () => {
    // The ring renders Math.round(score). 27 against a ceiling of 26.7 is the
    // same measurement twice and must not read as a shortfall.
    assert.equal(isAtCoverageCeiling(27, 26.7), true);
    assert.equal(isAtCoverageCeiling(26.5, 26.7), true);
  });

  it("still says no when a pass genuinely left coverage on the table", () => {
    // The guardrail. "At the ceiling" has to mean something, or it is just a
    // green light bolted onto every run.
    assert.equal(isAtCoverageCeiling(40, 80), false);
    assert.equal(isAtCoverageCeiling(0, 26.7), false);
  });

  it("does not guess when the version row carries no ceiling", () => {
    // Versions written before `achievable_ats_score` was reported have none,
    // and inventing one would flatter exactly the runs nobody measured.
    assert.equal(isAtCoverageCeiling(27, undefined), false);
    assert.equal(isAtCoverageCeiling(27, Number.NaN), false);
  });
});

describe("partitionMissing", () => {
  it("separates work not done from wording another pass could reach", () => {
    const missing = ["computer graphics", "algorithms", "concurrent systems"];
    const { notInProfile, reachable } = partitionMissing(missing, [
      "computer graphics",
      "concurrent systems",
    ]);
    assert.deepEqual(notInProfile, ["computer graphics", "concurrent systems"]);
    assert.deepEqual(reachable, ["algorithms"]);
  });

  it("keeps the posting's own order rather than set order", () => {
    const { notInProfile } = partitionMissing(["Zig", "Ada", "COBOL"], [
      "COBOL",
      "Zig",
    ]);
    assert.deepEqual(notInProfile, ["Zig", "COBOL"]);
  });

  it("treats an empty unreachable list as everything still being reachable", () => {
    // What an older version row looks like: no `missing_needs_new_facts`, so
    // nothing is claimed to be out of reach and the panel stays one list.
    const { notInProfile, reachable } = partitionMissing(["Rust"], []);
    assert.deepEqual(notInProfile, []);
    assert.deepEqual(reachable, ["Rust"]);
  });
});
