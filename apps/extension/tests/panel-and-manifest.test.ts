/**
 * Safety invariant 3 (every filled field shows its source, every blank shows
 * its reason) and the manifest's permission shape.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

import { renderPanel } from "../src/content/panel.ts";
import { must, runFixture } from "./helpers.ts";

const SRC = fileURLToPath(new URL("../src/", import.meta.url));
const manifest = JSON.parse(readFileSync(path.join(SRC, "manifest.json"), "utf8"));

function panelText(html: string, url: string, result: Parameters<typeof renderPanel>[1]): string {
  const dom = new JSDOM(html, { url });
  const doc = dom.window.document as unknown as Document;
  renderPanel(doc, result, "Test ATS");
  const host = must(doc.getElementById("job-os-autofill-panel"), "panel host");
  const shadow = must(host.shadowRoot, "panel shadow root");
  return shadow.textContent ?? "";
}

test("the panel names every filled field and the fact behind it", async () => {
  const { result } = await runFixture("greenhouse", "https://job-boards.greenhouse.io/gitlab/jobs/1");
  assert.ok(result.filled.length > 0);

  const text = panelText("<body></body>", "https://example.com/", result);

  for (const fill of result.filled) {
    assert.ok(text.includes(fill.sourced.value), `the written value for ${fill.key} is shown`);
    assert.ok(
      text.includes(fill.sourced.citation.factLabel),
      `the source fact for ${fill.key} is shown`,
    );
  }
});

test("the panel names every deliberate blank and why", async () => {
  const { result } = await runFixture("ashby", "https://jobs.ashbyhq.com/ashby/apply");
  assert.ok(result.skipped.length > 0);

  const text = panelText("<body></body>", "https://example.com/", result);

  for (const skip of result.skipped) {
    assert.ok(text.includes(skip.detail), `the reason for skipping ${skip.field.rawLabel} is shown`);
  }
});

test("required gaps are stated in the panel", async () => {
  const { result } = await runFixture("workday", "https://acme.myworkdayjobs.com/en-US/careers/job/apply");
  assert.ok(result.requiredGaps.length > 0);

  const text = panelText("<body></body>", "https://example.com/", result);

  assert.ok(text.includes("Required and still empty"));
  for (const gap of result.requiredGaps) {
    assert.ok(text.includes(gap.rawLabel), `the gap "${gap.rawLabel}" is listed`);
  }
});

test("the panel states that the extension never submits", async () => {
  const { result } = await runFixture("lever", "https://jobs.lever.co/leverdemo/abc/apply");
  const text = panelText("<body></body>", "https://example.com/", result);
  assert.ok(text.includes("never submits"));
});

test("a hostile field label cannot inject markup into the panel", async () => {
  const { result } = await runFixture("lever", "https://jobs.lever.co/leverdemo/abc/apply");

  // Rewrite a label to something an employer could put in their own form.
  const hostile = "<img src=x onerror=alert(1)>Name";
  const poisoned = {
    ...result,
    filled: result.filled.map((f, i) => (i === 0 ? { ...f, field: { ...f.field, rawLabel: hostile } } : f)),
  };

  const dom = new JSDOM("<body></body>", { url: "https://example.com/" });
  const doc = dom.window.document as unknown as Document;
  renderPanel(doc, poisoned, "Test ATS");

  const shadow = must(
    must(doc.getElementById("job-os-autofill-panel"), "panel host").shadowRoot,
    "panel shadow root",
  );
  assert.equal(shadow.querySelector("img"), null, "the label was not parsed as HTML");
  assert.ok((shadow.textContent ?? "").includes(hostile), "it is shown as literal text");
});

test("the manifest is V3 with the narrowest permissions that work", () => {
  assert.equal(manifest.manifest_version, 3);

  // activeTab rather than standing host access to every ATS.
  assert.deepEqual(manifest.permissions.sort(), ["activeTab", "scripting", "storage"]);

  // Exactly one host permission: the app's own origin, needed so Chrome treats
  // the profile fetch as same-site and attaches the existing session cookie.
  assert.deepEqual(manifest.host_permissions, ["https://jobs.hemnaath.tech/*"]);

  for (const host of manifest.host_permissions) {
    assert.ok(!host.includes("<all_urls>"), "no all_urls");
    assert.ok(host.startsWith("https://"), "https only");
  }

  // No passively injected content scripts: nothing runs until the user clicks.
  assert.equal(manifest.content_scripts, undefined);

  // Permissions we deliberately do not take.
  for (const forbidden of ["tabs", "cookies", "webRequest", "declarativeNetRequest", "<all_urls>"]) {
    assert.ok(!manifest.permissions.includes(forbidden), `${forbidden} is not requested`);
  }
});

test("no remotely hosted code, which MV3 review rejects outright", () => {
  const files: string[] = [];
  const walk = (dir: string): void => {
    for (const entry of readdirSync(dir)) {
      const full = path.join(dir, entry);
      if (statSync(full).isDirectory()) walk(full);
      else if (/\.(ts|html)$/.test(entry)) files.push(full);
    }
  };
  walk(SRC);

  for (const file of files) {
    const text = readFileSync(file, "utf8");
    assert.ok(!/\beval\s*\(/.test(text), `${path.relative(SRC, file)} does not eval`);
    assert.ok(!/new\s+Function\s*\(/.test(text), `${path.relative(SRC, file)} does not build functions`);
    // A script tag pointing off-origin is the specific thing the policy names.
    const remoteScript = /<script[^>]+src=["']https?:\/\//i;
    assert.ok(!remoteScript.test(text), `${path.relative(SRC, file)} loads no remote script`);
  }
});

test("no em dashes anywhere in the extension source", () => {
  const files: string[] = [];
  const walk = (dir: string): void => {
    for (const entry of readdirSync(dir)) {
      const full = path.join(dir, entry);
      if (statSync(full).isDirectory()) walk(full);
      else if (/\.(ts|html|json)$/.test(entry)) files.push(full);
    }
  };
  walk(SRC);

  const offenders = files.filter((f) => readFileSync(f, "utf8").includes("—"));
  assert.deepEqual(offenders.map((f) => path.relative(SRC, f)), []);
});
