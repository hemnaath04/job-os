/**
 * Workday.
 *
 * Workday is the one worth real effort: it is the ATS candidates complain about
 * most and the one they most want autofilled. It is also the least amenable to
 * generic scraping, because its labels are frequently detached from their
 * inputs and its class names are generated.
 *
 * What it does give us is `data-automation-id`, which Workday populates from
 * its own component model and uses for its internal test suite. Those values
 * are effectively an API: `legalNameSection_firstName` has meant the same thing
 * across every Workday tenant and skin I have seen referenced. Matching on them
 * is the opposite of a brittle CSS path, so this adapter leans on them hard and
 * falls back to the accessibility tree for anything unrecognised.
 *
 * Two Workday shapes are deliberately NOT filled:
 *   - split date spinbuttons (month / day / year as three inputs). They resolve
 *     to no canonical key and appear in the review panel as blanks, which is
 *     the honest outcome rather than a wrong graduation date.
 *   - the multi-step wizard's advance button, which is in the submit blocklist
 *     in dom-guard.ts. Advancing a Workday wizard is how an incomplete
 *     application gets marked submitted.
 */
import { collectStandard } from "./collect.ts";
import { describeField } from "../core/labels.ts";
import type { AtsAdapter, DetectContext } from "./types.ts";
import type { FieldKey, RawField } from "../core/types.ts";

/**
 * Workday automation ids to canonical keys.
 *
 * Matched by suffix as well as exactly, because Workday prefixes ids inside
 * repeating sections (`workExperience-1--jobTitle`) and inside its own
 * subcomponents.
 */
const AUTOMATION_HINTS: ReadonlyArray<readonly [string, FieldKey]> = [
  ["legalNameSection_firstName", "first_name"],
  ["legalNameSection_lastName", "last_name"],
  ["legalNameSection_preferredName", "preferred_name"],
  ["preferredNameSection_firstName", "preferred_name"],
  ["addressSection_addressLine1", "address_line1"],
  ["addressSection_addressLine2", "address_line2"],
  ["addressSection_city", "city"],
  ["addressSection_countryRegion", "state"],
  ["addressSection_regionSubdivision1", "state"],
  ["addressSection_postalCode", "postal_code"],
  ["countryDropdown", "country"],
  ["country", "country"],
  ["email", "email"],
  ["phone-number", "phone"],
  ["phoneNumber", "phone"],
  // Repeating experience and education sections. The first entry of each is
  // what an application form means by "current" and "most recent".
  ["jobTitle", "current_title"],
  ["company", "current_company"],
  ["schoolItem", "school"],
  ["school", "school"],
  ["degree", "degree"],
  ["field-of-study", "field_of_study"],
  ["fieldOfStudy", "field_of_study"],
  ["gpa", "gpa"],
  // The application's link fields.
  ["linkedinQuestion", "linkedin_url"],
  ["linkedIn", "linkedin_url"],
];

/** Automation ids that are part of a split date control. Recognised so they can
 * be reported clearly rather than silently ignored. */
const DATE_PART = /^dateSection(Month|Day|Year)-(input|display)$/;

export const workdayAdapter: AtsAdapter = {
  id: "workday",
  label: "Workday",

  detect({ url, document }: DetectContext): boolean {
    const host = url.hostname.toLowerCase();
    if (host.endsWith("myworkdayjobs.com") || host.endsWith("myworkdaysite.com")) return true;
    if (host.endsWith("workday.com")) return true;
    return document.querySelector("[data-automation-id='legalNameSection_firstName']") !== null;
  },

  formRoots(doc: Document): Element[] {
    const scoped = doc.querySelectorAll(
      "[data-automation-id='applyFlowPage'], [data-automation-id='jobApplication'], form",
    );
    return scoped.length > 0 ? Array.from(scoped) : [doc.body];
  },

  /**
   * Workday's dropdown triggers are buttons, which the standard collector's
   * selector deliberately excludes. Add them back explicitly so Country, State
   * and Degree are fillable rather than invisible.
   */
  collectFields(root: Element): RawField[] {
    const fields = collectStandard(root);
    const seen = new Set(fields.map((f) => f.element));

    const triggers = root.querySelectorAll(
      'button[aria-haspopup="listbox"], button[aria-haspopup="menu"], [role="combobox"]',
    );

    for (const trigger of Array.from(triggers)) {
      if (seen.has(trigger)) continue;
      if (trigger.closest("[hidden], [aria-hidden='true']")) continue;

      const described = describeField(trigger);
      fields.push({ ...described, kind: "popup_select" });
    }

    return fields;
  },

  fieldKeyHint(field: RawField): FieldKey | null {
    const automation = field.automationId ?? "";
    if (!automation) return null;

    // Date parts carry no canonical key on purpose; see the file header.
    if (DATE_PART.test(automation)) return null;

    for (const [suffix, key] of AUTOMATION_HINTS) {
      if (automation === suffix) return key;
      // `workExperience-1--jobTitle` and `education-2--school`.
      if (automation.endsWith(`--${suffix}`) || automation.endsWith(`_${suffix}`)) return key;
    }

    // Workday's self-identification steps are separate pages with their own
    // automation ids. Naming them does not fill them; consent still gates it.
    if (/selfIdentif|voluntaryDisclosure|veteranStatus|disability/i.test(automation)) {
      if (/gender/i.test(automation)) return "eeo_gender";
      if (/ethnic|race/i.test(automation)) return "eeo_race";
      if (/hispanic|latino/i.test(automation)) return "eeo_hispanic";
      if (/veteran/i.test(automation)) return "eeo_veteran";
      if (/disability/i.test(automation)) return "eeo_disability";
    }

    return null;
  },
};
