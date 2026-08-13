/**
 * Lever.
 *
 * Lever's useful signal is the `name` attribute, which is a documented part of
 * how it posts applications and therefore does not churn with redesigns. Links
 * arrive as `urls[LinkedIn]`, which is the only place in this codebase where a
 * bracketed name is parsed for meaning.
 *
 * Lever's own labels are terse ("Full name", "Resume"), so the generic resolver
 * handles the rest. Custom questions live under `cards[...]` names and are left
 * to label matching, which for a bespoke question means left blank.
 */
import { collectStandard } from "./collect.ts";
import type { AtsAdapter, DetectContext } from "./types.ts";
import type { FieldKey, RawField } from "../core/types.ts";

const NAME_HINTS: Readonly<Record<string, FieldKey>> = {
  name: "full_name",
  email: "email",
  phone: "phone",
  org: "current_company",
  // Lever labels these "Current company" and "Current title" respectively.
  company: "current_company",
  location: "city",
};

/** `urls[LinkedIn]` and friends. Lever's network names are capitalised. */
const URL_HINTS: Readonly<Record<string, FieldKey>> = {
  linkedin: "linkedin_url",
  github: "github_url",
  portfolio: "portfolio_url",
  twitter: "other_url",
  other: "other_url",
};

export const leverAdapter: AtsAdapter = {
  id: "lever",
  label: "Lever",

  detect({ url, document }: DetectContext): boolean {
    const host = url.hostname.toLowerCase();
    if (host === "jobs.lever.co" || host.endsWith(".lever.co")) return true;
    return document.querySelector("form[action*='lever.co'], .application-form[data-qa]") !== null;
  },

  formRoots(doc: Document): Element[] {
    const roots = doc.querySelectorAll(
      "form.application-form, form[action*='lever.co'], div.application-form",
    );
    return roots.length > 0 ? Array.from(roots) : Array.from(doc.querySelectorAll("form"));
  },

  collectFields: collectStandard,

  fieldKeyHint(field: RawField): FieldKey | null {
    const name = field.name ?? "";
    if (name in NAME_HINTS) return NAME_HINTS[name] ?? null;

    const urlMatch = /^urls\[([^\]]+)\]$/.exec(name);
    if (urlMatch) {
      const network = urlMatch[1]!.toLowerCase();
      return URL_HINTS[network] ?? "other_url";
    }

    // Lever names its demographic fields `eeo[gender]`, `eeo[race]`,
    // `eeo[veteran]` and `eeo[disability]`, inside a `data-qa="eeo-section"`
    // container. Confirmed against the saved fixture. Naming them here does not
    // fill them: consent gates every one of these keys separately.
    const eeoMatch = /^eeo\[([^\]]+)\]$/.exec(name);
    if (eeoMatch) {
      const question = eeoMatch[1]!.toLowerCase();
      // The disability signature fields are a legal attestation, not a
      // demographic answer, and are never filled by anything here.
      if (question.includes("signature")) return null;
      if (question.includes("gender")) return "eeo_gender";
      if (question.includes("race") || question.includes("ethnic")) return "eeo_race";
      if (question.includes("veteran")) return "eeo_veteran";
      if (question.includes("disability")) return "eeo_disability";
      return null;
    }

    return null;
  },
};
