/**
 * Adapter lookup. Adding an ATS is one import and one array entry.
 *
 * Order matters only where signatures overlap. Greenhouse and Lever both get
 * embedded in employer career sites, so the host-specific adapters are tried
 * before anything that sniffs the DOM, and the generic adapter is never
 * detected, only fallen back to.
 */
import { ashbyAdapter } from "./ashby.ts";
import { genericAdapter } from "./generic.ts";
import { greenhouseAdapter } from "./greenhouse.ts";
import { leverAdapter } from "./lever.ts";
import { smartRecruitersAdapter } from "./smartrecruiters.ts";
import { workdayAdapter } from "./workday.ts";
import type { AtsAdapter, DetectContext } from "./types.ts";

export const ADAPTERS: readonly AtsAdapter[] = [
  workdayAdapter,
  greenhouseAdapter,
  leverAdapter,
  ashbyAdapter,
  smartRecruitersAdapter,
];

export { genericAdapter };

/** The adapter for this page, or the generic one. Never returns null, because
 * "we could not tell" still deserves the narrow, honest fill. */
export function selectAdapter(ctx: DetectContext): AtsAdapter {
  for (const adapter of ADAPTERS) {
    try {
      if (adapter.detect(ctx)) return adapter;
    } catch {
      // A detector that throws on an odd page must not take the run down.
      continue;
    }
  }
  return genericAdapter;
}

/** All fields the adapter can see on this document, de-duplicated across roots
 * because ATSs nest forms more often than you would hope. */
export function collectAllFields(adapter: AtsAdapter, doc: Document) {
  const seen = new Set<Element>();
  const out = [];

  for (const root of adapter.formRoots(doc)) {
    for (const field of adapter.collectFields(root)) {
      if (seen.has(field.element)) continue;
      seen.add(field.element);
      out.push(field);
    }
  }

  return out;
}
