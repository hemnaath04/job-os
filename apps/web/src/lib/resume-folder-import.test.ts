import assert from "node:assert/strict";
import { test } from "node:test";
import { findMasterFilename, pickResumeFiles } from "./resume-folder-import.ts";

const file = (name: string) => ({ name });

test("takes any account's resumes, not one hard-coded person's", () => {
  const picked = pickResumeFiles([
    file("Master.pdf"),
    file("nursing-resume-2026.docx"),
    file("cv.json"),
  ]);
  assert.deepEqual(
    picked.map((f) => f.name),
    ["Master.pdf", "nursing-resume-2026.docx", "cv.json"],
  );
});

test("drops files the importer cannot read", () => {
  const picked = pickResumeFiles([
    file("resume.pdf"),
    file("cover-letter.txt"),
    file("headshot.png"),
    file("notes"),
  ]);
  assert.deepEqual(
    picked.map((f) => f.name),
    ["resume.pdf"],
  );
});

test("drops the dotfiles a picked folder reports alongside real ones", () => {
  const picked = pickResumeFiles([
    file(".DS_Store"),
    file("resumes/._Master.pdf"),
    file("resumes/Master.pdf"),
  ]);
  assert.deepEqual(
    picked.map((f) => f.name),
    ["resumes/Master.pdf"],
  );
});

test("an empty folder yields nothing rather than throwing", () => {
  assert.deepEqual(pickResumeFiles([]), []);
});

test("names the master by the word in its filename, whatever surrounds it", () => {
  assert.equal(
    findMasterFilename([file("2026-general.pdf"), file("my MASTER copy.pdf")]),
    "my MASTER copy.pdf",
  );
});

test("leaves the master unset rather than promoting an arbitrary file", () => {
  assert.equal(
    findMasterFilename([file("teaching.pdf"), file("research.pdf")]),
    undefined,
  );
});

test("takes the first when several files claim to be the master", () => {
  assert.equal(
    findMasterFilename([file("master-v2.pdf"), file("master-old.pdf")]),
    "master-v2.pdf",
  );
});
