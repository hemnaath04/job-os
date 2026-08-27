/**
 * Both shells have to be able to reach every destination.
 *
 * This file exists because the same failure has shipped twice, and neither
 * time did anything break: a list lost an entry and the pages behind it simply
 * stopped existing on a phone. First `NAV.slice(0, 5)` dropped the sixth item
 * and took Resumes, Cover Letters, Profile and Calendar with it. Then the
 * footer (Docs, Settings, Sign out, account) stayed sidebar-only, and since
 * the sidebar is `lg:flex`, a signed-in phone user had no way to sign out at
 * all. Both are invariants over these lists, so they are checked here.
 */
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { FOOTER_NAV, NAV, OVERFLOW, PRIMARY, SECTIONS } from "./nav.ts";

describe("shell navigation", () => {
  it("the phone's two lists cover every page in NAV, in order", () => {
  assert.deepEqual(
    [...PRIMARY, ...OVERFLOW].map((item) => item.href),
    NAV.map((item) => item.href),
  );
});

  it("nothing in NAV is in both the bar and the sheet", () => {
  const bar = new Set(PRIMARY.map((item) => item.href));
  assert.equal(
    OVERFLOW.filter((item) => bar.has(item.href)).length,
    0,
  );
});

  it("the bar stays within a comfortable tap count", () => {
  // Five targets: four links plus the "More" button that opens the sheet.
  assert.ok(PRIMARY.length <= 4, `bar has ${PRIMARY.length} links, max 4`);
});

  it("the footer's pages are not in NAV, so no slice can swallow them", () => {
  const navHrefs = new Set(NAV.map((item) => item.href));
  for (const item of FOOTER_NAV) {
    assert.ok(!navHrefs.has(item.href), `${item.href} is in both NAV and FOOTER_NAV`);
  }
});

  it("the footer still has pages to render", () => {
  // Emptying this would silently remove Docs and Settings from both shells,
  // which is how the mobile gap looked from the outside: no error, no page.
  assert.ok(FOOTER_NAV.length > 0);
});

  it("every destination is listed once and has a label", () => {
  const all = [...NAV, ...FOOTER_NAV];
  assert.equal(new Set(all.map((item) => item.href)).size, all.length);
  for (const item of all) assert.ok(item.label.trim().length > 0);
});

  it("every NAV section is one the sidebar renders", () => {
  // The sidebar draws its groups by filtering NAV per SECTIONS, so a typo in a
  // section name drops that page from the desktop nav without any error.
  for (const item of NAV) {
    assert.ok(
      item.section && (SECTIONS as readonly string[]).includes(item.section),
      `${item.href} has section ${String(item.section)}, which SECTIONS does not list`,
    );
  }
});
});
