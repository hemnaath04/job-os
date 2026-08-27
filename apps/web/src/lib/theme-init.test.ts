/**
 * The pre-paint theme script, exercised as the string that actually ships.
 *
 * It runs inline in the document head, so it cannot import anything and no
 * component test can reach it. Evaluating the exported source against stub
 * globals is the only way to test the real artifact rather than a copy of its
 * logic that could drift from it.
 *
 * Worth guarding because getting it wrong is invisible in the obvious place.
 * When this defaulted to light and consulted nothing else, a phone in dark
 * mode loaded a light page and the browser force-darkened it, which repaints
 * plain colours but not a `background-clip: text` gradient: the landing
 * headline kept light-theme ink on a dark background and was unreadable, while
 * every other element inverted and looked correct.
 */
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { themeInit } from "./theme-init.ts";

function run({
  stored,
  prefersDark,
  storageThrows = false,
}: {
  stored: string | null;
  prefersDark: boolean;
  storageThrows?: boolean;
}): boolean {
  const classes = new Set<string>();
  const scope = {
    localStorage: {
      getItem(key: string) {
        if (storageThrows) throw new Error("storage unavailable");
        return key === "theme" ? stored : null;
      },
    },
    matchMedia: (query: string) => ({
      matches: query.includes("dark") ? prefersDark : false,
    }),
    document: { documentElement: { classList: { add: (c: string) => classes.add(c) } } },
  };
  new Function("localStorage", "matchMedia", "document", themeInit)(
    scope.localStorage,
    scope.matchMedia,
    scope.document,
  );
  return classes.has("dark");
}

describe("the pre-paint theme script", () => {
  it("follows a device set to dark when nothing is stored", () => {
    assert.equal(run({ stored: null, prefersDark: true }), true);
  });

  it("stays light on a device set to light when nothing is stored", () => {
    assert.equal(run({ stored: null, prefersDark: false }), false);
  });

  it("honours a stored dark choice on a light device", () => {
    assert.equal(run({ stored: "dark", prefersDark: false }), true);
  });

  it("honours a stored light choice on a dark device", () => {
    // The half that a bare prefers-color-scheme rule would get wrong: someone
    // who deliberately chose light must keep it on a dark phone.
    assert.equal(run({ stored: "light", prefersDark: true }), false);
  });

  it("does not throw when storage is unavailable", () => {
    // Private browsing and blocked-cookie modes throw on getItem. An
    // exception here runs before paint and takes the page with it.
    assert.doesNotThrow(() => run({ stored: null, prefersDark: true, storageThrows: true }));
  });
});
