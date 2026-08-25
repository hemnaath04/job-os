import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { shouldLinkResumeToApplication } from "./resume-application-link.ts";

type FakeResume = {
  id: string;
  is_master: boolean;
  spawned_from_application_id?: string | null;
};

function resume(id: string, overrides: Partial<FakeResume> = {}): FakeResume {
  return { id, is_master: false, spawned_from_application_id: null, ...overrides };
}

describe("shouldLinkResumeToApplication", () => {
  it("links a resume with no application yet", () => {
    const resumes = [resume("r1")];
    assert.equal(shouldLinkResumeToApplication(resumes, "r1", "app-1"), true);
  });

  it("is a no-op once already linked to the same application", () => {
    const resumes = [resume("r1", { spawned_from_application_id: "app-1" })];
    assert.equal(shouldLinkResumeToApplication(resumes, "r1", "app-1"), false);
  });

  it("refuses to steal a resume already linked to a different application", () => {
    const resumes = [resume("r1", { spawned_from_application_id: "app-2" })];
    assert.equal(shouldLinkResumeToApplication(resumes, "r1", "app-1"), false);
  });

  it("never links the master resume", () => {
    const resumes = [resume("r1", { is_master: true })];
    assert.equal(shouldLinkResumeToApplication(resumes, "r1", "app-1"), false);
  });

  it("refuses to link a second container to an application that already has one", () => {
    const resumes = [
      resume("r1"),
      resume("r2", { spawned_from_application_id: "app-1" }),
    ];
    assert.equal(shouldLinkResumeToApplication(resumes, "r1", "app-1"), false);
  });

  it("returns false for an unknown resume id", () => {
    const resumes = [resume("r1")];
    assert.equal(shouldLinkResumeToApplication(resumes, "unknown", "app-1"), false);
  });
});
