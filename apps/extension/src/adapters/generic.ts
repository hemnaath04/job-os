/**
 * The fallback for every ATS without an adapter.
 *
 * It offers no hints at all, which means it fills only what the accessibility
 * tree and the `autocomplete` attribute make unambiguous: name, email, phone,
 * address, links. That is a narrow result and it is meant to be. The
 * competitor's mistake was claiming thousands of supported platforms while
 * really only working on one; a generic adapter that quietly half-fills an
 * unknown form and reports success is the same mistake in miniature.
 *
 * The review panel names this adapter explicitly so the user knows the form was
 * not specifically supported.
 */
import { collectStandard } from "./collect.ts";
import type { AtsAdapter } from "./types.ts";

export const genericAdapter: AtsAdapter = {
  id: "generic",
  label: "Generic form",

  // Never auto-detected; the registry falls back to it.
  detect(): boolean {
    return false;
  },

  formRoots(doc: Document): Element[] {
    const forms = Array.from(doc.querySelectorAll("form"));
    if (forms.length === 0) return [doc.body];

    // Pick the form that looks most like an application rather than the site's
    // search box: the one with the most fillable controls.
    const scored = forms
      .map((form) => ({
        form,
        score: form.querySelectorAll("input:not([type=hidden]), textarea, select").length,
      }))
      .sort((a, b) => b.score - a.score);

    const best = scored[0];
    return best && best.score >= 3 ? [best.form] : forms;
  },

  collectFields: collectStandard,
};
