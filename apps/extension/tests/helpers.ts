/**
 * Shared test scaffolding: load a fixture into a real DOM, and build a profile
 * to fill from.
 */
import { JSDOM } from "jsdom";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

import { parseVerifiedProfile, type VerifiedProfile } from "../src/core/profile.ts";
import { buildProfileValues, type ProfileValues } from "../src/core/mapping.ts";
import { collectAllFields, selectAdapter } from "../src/adapters/registry.ts";
import { buildFillPlan } from "../src/core/planner.ts";
import { applyFillPlan } from "../src/core/filler.ts";
import type { AtsAdapter } from "../src/adapters/types.ts";
import type { EeoConsent, FillResult, RawField } from "../src/core/types.ts";

const FIXTURES = fileURLToPath(new URL("./fixtures/", import.meta.url));

/**
 * Assert presence and narrow the type in one step.
 *
 * The suite runs under the same strict settings as src, so a `querySelector`
 * that might return null has to be dealt with rather than asserted away with
 * `!`. This turns "the fixture changed and the element is gone" into a clear
 * failure message instead of a TypeError twenty lines later.
 */
export function must<T>(value: T | null | undefined, what = "value"): T {
  if (value === null || value === undefined) throw new Error(`expected ${what} to be present`);
  return value;
}

/** `querySelector` that fails loudly instead of returning null. */
export function qs(root: Document | Element, selector: string): Element {
  return must(root.querySelector(selector), selector);
}

/** `getElementById` that fails loudly instead of returning null. */
export function byId(doc: Document, id: string): Element {
  return must(doc.getElementById(id), `#${id}`);
}

export function loadFixture(name: string, url: string): { doc: Document; url: URL } {
  const html = readFileSync(path.join(FIXTURES, `${name}.html`), "utf8");
  const dom = new JSDOM(html, { url });
  // The core modules reach for the document's own window for instanceof checks,
  // which is exactly what makes them work in a page they did not create.
  return { doc: dom.window.document as unknown as Document, url: new URL(url) };
}

/**
 * A complete, verified profile. Values are deliberately distinctive so a test
 * can assert that what landed in a field is character for character what came
 * out of the vault.
 */
export function sampleFactRows(): unknown[] {
  return [
    {
      id: "fact-contact",
      kind: "contact",
      title: "Ada Lovelace",
      org: null,
      start_date: null,
      end_date: null,
      location: "Boston",
      verified: true,
      payload: {
        name: "Ada Lovelace",
        email: "ada@example.com",
        phone: "+1 617 555 0142",
        url: "https://ada.example.com",
        address: "12 Analytical Way",
        city: "Boston",
        region: "MA",
        postalCode: "02115",
        countryCode: "US",
        profiles: {
          linkedin: "https://linkedin.com/in/adalovelace",
          github: "https://github.com/adalovelace",
        },
      },
    },
    {
      id: "fact-education",
      kind: "education",
      title: "Master of Science Computer Science",
      org: "Northeastern University",
      start_date: "2026-01-15",
      end_date: "2028-05-10",
      location: "Boston",
      verified: true,
      payload: { studyType: "Master of Science", area: "Computer Science", score: "3.9" },
    },
    {
      id: "fact-experience",
      kind: "experience",
      title: "Test Automation Engineer",
      org: "EPAM Systems",
      start_date: "2023-06-01",
      end_date: null,
      location: "Chennai",
      verified: true,
      payload: {},
    },
    {
      id: "fact-auth",
      kind: "authorization",
      title: "Work authorization",
      org: null,
      start_date: null,
      end_date: null,
      location: null,
      verified: true,
      payload: { work_authorized: "Yes", requires_sponsorship: "Yes" },
    },
    {
      id: "fact-eeo",
      kind: "eeo",
      title: "Self identification",
      org: null,
      start_date: null,
      end_date: null,
      location: null,
      verified: true,
      payload: { gender: "Female", race: "White", veteran: "No", disability: "No" },
    },
  ];
}

export function sampleProfile(): VerifiedProfile {
  return parseVerifiedProfile(sampleFactRows());
}

export function sampleValues(consent: EeoConsent = {}): ProfileValues {
  return buildProfileValues(sampleProfile(), consent);
}

export interface RunOutcome {
  readonly adapter: AtsAdapter;
  readonly fields: readonly RawField[];
  readonly result: FillResult;
}

/** Detect, collect, plan and fill a fixture end to end. */
export async function runFixture(
  name: string,
  url: string,
  consent: EeoConsent = {},
): Promise<RunOutcome> {
  const { doc, url: parsed } = loadFixture(name, url);
  const adapter = selectAdapter({ url: parsed, document: doc });
  const fields = collectAllFields(adapter, doc);

  const plan = buildFillPlan({
    adapter,
    fields,
    values: sampleValues(consent),
    consent,
  });

  const result = await applyFillPlan(plan, fields);
  return { adapter, fields, result };
}

/** The value that actually landed in the field mapped to `key`. */
export function filledValue(result: FillResult, key: string): string | null {
  const hit = result.filled.find((f) => f.key === key);
  return hit ? hit.sourced.value : null;
}

export function skipReasonFor(result: FillResult, labelFragment: string): string | null {
  const hit = result.skipped.find((s) =>
    s.field.rawLabel.toLowerCase().includes(labelFragment.toLowerCase()),
  );
  return hit ? hit.reason : null;
}
