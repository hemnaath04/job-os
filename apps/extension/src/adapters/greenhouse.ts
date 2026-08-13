/**
 * Greenhouse.
 *
 * Greenhouse renders its application form server side with real `<label for>`
 * pairs, so the generic label resolver already handles most of it. The hints
 * below cover the two places it cannot: the stable `id` values Greenhouse has
 * used for core fields across both board generations, and its custom questions,
 * which are named `question_12345` and carry no reusable meaning at all. Those
 * resolve by label or not at all, which is the correct outcome for a question
 * the employer wrote themselves.
 */
import { collectStandard } from "./collect.ts";
import type { AtsAdapter, DetectContext } from "./types.ts";
import type { FieldKey, RawField } from "../core/types.ts";

/** Greenhouse's long-lived input ids. Stable across the boards.greenhouse.io
 * and job-boards.greenhouse.io generations. */
const ID_HINTS: Readonly<Record<string, FieldKey>> = {
  first_name: "first_name",
  last_name: "last_name",
  email: "email",
  phone: "phone",
  // Greenhouse's own link fields.
  job_application_answers_attributes_0_text_value: "linkedin_url",
};

const HOSTS = ["boards.greenhouse.io", "job-boards.greenhouse.io", "my.greenhouse.io"];

export const greenhouseAdapter: AtsAdapter = {
  id: "greenhouse",
  label: "Greenhouse",

  detect({ url, document }: DetectContext): boolean {
    const host = url.hostname.toLowerCase();
    if (HOSTS.some((h) => host === h || host.endsWith(`.${h}`))) return true;

    // Greenhouse is very often embedded in the employer's own careers page via
    // an iframe or the `#grnhse_app` mount, so a host check alone misses a
    // large share of real applications.
    return (
      document.querySelector("#grnhse_app, #application_form, form#application-form") !== null &&
      document.querySelector('input[id="first_name"], input[name="job_application[first_name]"]') !==
        null
    );
  },

  formRoots(doc: Document): Element[] {
    const roots = doc.querySelectorAll(
      "#application_form, form#application-form, #grnhse_app form, form[action*='greenhouse']",
    );
    return roots.length > 0 ? Array.from(roots) : Array.from(doc.querySelectorAll("form"));
  },

  collectFields: collectStandard,

  fieldKeyHint(field: RawField): FieldKey | null {
    const id = field.id ?? "";
    if (id in ID_HINTS) return ID_HINTS[id] ?? null;

    // `job_application[first_name]`-style names on the older board.
    const name = field.name ?? "";
    const bracket = /^job_application\[(\w+)\]$/.exec(name);
    if (bracket) {
      const inner = bracket[1]!;
      if (inner in ID_HINTS) return ID_HINTS[inner] ?? null;
    }

    // Greenhouse's demographic block lives under its own container and uses
    // plain ids. Naming them here does not fill them: consent still gates it.
    if (field.element.closest("#demographic_questions, [data-testid='eeoc-section']")) {
      if (id.includes("gender")) return "eeo_gender";
      if (id.includes("race") || id.includes("ethnic")) return "eeo_race";
      if (id.includes("hispanic")) return "eeo_hispanic";
      if (id.includes("veteran")) return "eeo_veteran";
      if (id.includes("disability")) return "eeo_disability";
    }

    return null;
  },
};
