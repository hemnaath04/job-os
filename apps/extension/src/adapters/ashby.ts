/**
 * Ashby.
 *
 * Ashby is a React app whose class names are content-hashed and change on every
 * deploy, so matching on CSS structure would break within weeks. Two things do
 * not move: the `_systemfield_*` ids on the fields Ashby defines itself, and
 * the accessibility wiring, which is unusually good here. Everything else goes
 * through `aria-labelledby`.
 *
 * Ashby is also the ATS whose HQ reportedly started flagging bulk agent
 * applications as spam, which is a decent argument for the review step being
 * the point rather than an inconvenience.
 */
import { collectStandard } from "./collect.ts";
import type { AtsAdapter, DetectContext } from "./types.ts";
import type { FieldKey, RawField } from "../core/types.ts";

/** Ashby's system field ids, minus the `_systemfield_` prefix. */
const SYSTEM_FIELDS: Readonly<Record<string, FieldKey>> = {
  name: "full_name",
  email: "email",
  phone: "phone",
  // Ashby's optional link fields.
  linkedin: "linkedin_url",
  github: "github_url",
  website: "portfolio_url",
  portfolio: "portfolio_url",
};

export const ashbyAdapter: AtsAdapter = {
  id: "ashby",
  label: "Ashby",

  detect({ url, document }: DetectContext): boolean {
    const host = url.hostname.toLowerCase();
    if (host === "jobs.ashbyhq.com" || host.endsWith(".ashbyhq.com")) return true;
    return document.querySelector("[id^='_systemfield_'], [data-highlight='ashby']") !== null;
  },

  formRoots(doc: Document): Element[] {
    const roots = doc.querySelectorAll(
      "form.ashby-application-form, [class*='ashby-application-form'], form",
    );
    return roots.length > 0 ? Array.from(roots) : [doc.body];
  },

  collectFields: collectStandard,

  fieldKeyHint(field: RawField): FieldKey | null {
    const id = field.id ?? "";
    const system = /^_systemfield_(\w+)$/.exec(id);
    if (system) {
      const key = system[1]!.toLowerCase();
      return SYSTEM_FIELDS[key] ?? null;
    }

    // Ashby's demographic survey is a separate, clearly marked section.
    if (field.element.closest("[data-testid='eeoc'], [id*='eeoc'], [class*='DemographicQuestion']")) {
      const lower = field.label;
      if (lower.includes("gender")) return "eeo_gender";
      if (lower.includes("race") || lower.includes("ethnic")) return "eeo_race";
      if (lower.includes("hispanic") || lower.includes("latino")) return "eeo_hispanic";
      if (lower.includes("veteran")) return "eeo_veteran";
      if (lower.includes("disability")) return "eeo_disability";
    }

    return null;
  },
};
