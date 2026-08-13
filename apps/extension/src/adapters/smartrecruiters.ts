/**
 * SmartRecruiters.
 *
 * SmartRecruiters ships `data-test` hooks on its fields, which are meant for
 * its own end-to-end suite and so are about as stable as anything on the page.
 * Field names are camelCase and self-describing, which covers the rest.
 */
import { collectStandard } from "./collect.ts";
import type { AtsAdapter, DetectContext } from "./types.ts";
import type { FieldKey, RawField } from "../core/types.ts";

const NAME_HINTS: Readonly<Record<string, FieldKey>> = {
  firstName: "first_name",
  lastName: "last_name",
  email: "email",
  phoneNumber: "phone",
  phone: "phone",
  city: "city",
  country: "country",
  region: "state",
  postalCode: "postal_code",
  street: "address_line1",
  linkedinProfileUrl: "linkedin_url",
  web: "portfolio_url",
};

export const smartRecruitersAdapter: AtsAdapter = {
  id: "smartrecruiters",
  label: "SmartRecruiters",

  detect({ url, document }: DetectContext): boolean {
    const host = url.hostname.toLowerCase();
    if (host.endsWith("smartrecruiters.com")) return true;
    return document.querySelector("[data-test='application-form'], #sr-application-form") !== null;
  },

  formRoots(doc: Document): Element[] {
    const roots = doc.querySelectorAll(
      "[data-test='application-form'], #sr-application-form, form[action*='smartrecruiters']",
    );
    return roots.length > 0 ? Array.from(roots) : Array.from(doc.querySelectorAll("form"));
  },

  collectFields: collectStandard,

  fieldKeyHint(field: RawField): FieldKey | null {
    const name = field.name ?? "";
    if (name in NAME_HINTS) return NAME_HINTS[name] ?? null;

    const id = field.id ?? "";
    if (id in NAME_HINTS) return NAME_HINTS[id] ?? null;

    // `data-test="field-firstName"`.
    const test = field.automationId ?? "";
    const fieldMatch = /^field-(\w+)$/.exec(test);
    if (fieldMatch) {
      const inner = fieldMatch[1]!;
      if (inner in NAME_HINTS) return NAME_HINTS[inner] ?? null;
    }

    return null;
  },
};
