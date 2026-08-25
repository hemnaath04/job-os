import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { shouldRestoreTailor } from "./tailor-restore.ts";

const AMEX = { jobPostingId: "job-amex", resumeId: "r1" };

describe("shouldRestoreTailor", () => {
  it("does not restore when the visit names a job", () => {
    // The reported bug. Clicking "Tailor a resume for this role" on Advanced
    // Space linked to /tailor?job_id=advanced-space, and the restore then
    // seeded the picker from the last run, American Express. The page showed
    // the Amex resume and the next run went to Amex too.
    assert.equal(shouldRestoreTailor("job-advanced-space", AMEX), false);
  });

  it("does not restore even when the named job matches the stored one", () => {
    // Deliberately not special-cased. A named job is a request for a fresh
    // start on that role, and matching ids would otherwise reopen a finished
    // result the person did not ask to see.
    assert.equal(shouldRestoreTailor("job-amex", AMEX), false);
  });

  it("restores on a plain visit with nothing named", () => {
    // What the restore was built for: navigating away and back must not
    // strand a run or leave a finished resume unreachable.
    assert.equal(shouldRestoreTailor("", AMEX), true);
    assert.equal(shouldRestoreTailor(null, AMEX), true);
    assert.equal(shouldRestoreTailor(undefined, AMEX), true);
  });

  it("has nothing to restore when nothing was stored", () => {
    assert.equal(shouldRestoreTailor("", null), false);
    assert.equal(shouldRestoreTailor("job-advanced-space", null), false);
  });
});
