import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { buildResumeFilename } from "./resume-filename.ts";

describe("buildResumeFilename", () => {
  it("names the file after the person and the company", () => {
    assert.equal(
      buildResumeFilename({ person: "Hemnaath Balasubramani", company: "American Express" }),
      "Hemnaath_Balasubramani_American_Express.pdf",
    );
  });

  it("has no room for a job title, which is what broke it", () => {
    // The real filename this replaces, cut mid-word by the length cap:
    //   Hemnaath_Balasubramani_American Express_Campus_Graduate_masters_
    //   Summer_Internship_Program_-_2027_AI_Engineer_I,_Enterpri.pdf
    // The builder takes named parts now, so a caller cannot append a role.
    const name = buildResumeFilename({
      person: "Hemnaath Balasubramani",
      company: "American Express",
    });
    assert.ok(!name.includes("Engineer"), name);
    assert.ok(name.length < 45, `still ${name.length} characters`);
  });

  it("drops a trailing legal suffix, which is noise in a filename", () => {
    assert.equal(
      buildResumeFilename({ person: "Ada Lovelace", company: "Crowe Advisory LLC" }),
      "Ada_Lovelace_Crowe_Advisory.pdf",
    );
    assert.equal(
      buildResumeFilename({ person: "Ada Lovelace", company: "Acme, Inc." }),
      "Ada_Lovelace_Acme.pdf",
    );
  });

  it("drops two, so a doubled suffix does not leave one behind", () => {
    assert.equal(
      buildResumeFilename({ person: "Ada Lovelace", company: "Example Holdings Co. Ltd" }),
      "Ada_Lovelace_Example_Holdings.pdf",
    );
  });

  it("only strips a suffix at the end, never inside the name", () => {
    // A company is not ours to rewrite because it happens to contain a word.
    assert.equal(
      buildResumeFilename({ person: "Ada Lovelace", company: "Limited Run Games" }),
      "Ada_Lovelace_Limited_Run_Games.pdf",
    );
    assert.equal(
      buildResumeFilename({ person: "Ada Lovelace", company: "Incyte" }),
      "Ada_Lovelace_Incyte.pdf",
    );
  });

  it("caps a runaway company rather than the person's name", () => {
    const name = buildResumeFilename({
      person: "Ada Lovelace",
      company: "The Very Long Holding Company Of Extremely Verbose Naming Practices",
    });
    assert.ok(name.startsWith("Ada_Lovelace_"), name);
    assert.ok(name.length <= 60, `${name.length} characters: ${name}`);
  });

  it("strips characters a filesystem will not take", () => {
    // "Co" survives here, and that is correct rather than a miss: the suffix
    // strip runs on the company as given, where the string ends in "<Co>" and
    // not in a trailing legal suffix. Sanitising is what this asserts.
    assert.equal(
      buildResumeFilename({ person: "Ada Lovelace", company: 'A/B: "Test" <Co>' }),
      "Ada_Lovelace_AB_Test_Co.pdf",
    );
  });

  it("works with only one part known", () => {
    assert.equal(buildResumeFilename({ person: "Ada Lovelace" }), "Ada_Lovelace.pdf");
    assert.equal(buildResumeFilename({ company: "Datadog" }), "Datadog.pdf");
  });

  it("never produces a nameless file", () => {
    assert.equal(buildResumeFilename({}), "resume.pdf");
    assert.equal(buildResumeFilename({ person: "  ", company: null }), "resume.pdf");
  });
});
