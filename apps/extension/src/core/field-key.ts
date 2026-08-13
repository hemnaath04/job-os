/**
 * Decide which canonical field a control is asking for, or decide that we do
 * not know.
 *
 * "We do not know" is the common case and the correct one. Every custom
 * application question ("Please provide an example of your exceptional
 * ability", "Why do you want to work here?") lands here, resolves to null, and
 * is left blank. There is no fallback path that produces text, which is what
 * makes safety invariant 1 hold in practice rather than in principle.
 *
 * Matching is deliberately conservative:
 *   - `autocomplete` first, because it is a browser standard the ATS opted into
 *   - then whole-label equality against a synonym table
 *   - then containment, but only for phrases distinctive enough to be safe
 * A label that plausibly matches two keys resolves to null rather than picking.
 */
import type { FieldKey, RawField } from "./types.ts";

/**
 * WHATWG autofill tokens to our keys. The most reliable signal on the page
 * when present, because the ATS had to mean it.
 * https://html.spec.whatwg.org/multipage/form-control-infrastructure.html#autofill
 */
const AUTOCOMPLETE_MAP: Readonly<Record<string, FieldKey>> = {
  "given-name": "first_name",
  "family-name": "last_name",
  name: "full_name",
  "nickname": "preferred_name",
  email: "email",
  tel: "phone",
  "tel-national": "phone",
  "street-address": "address_line1",
  "address-line1": "address_line1",
  "address-line2": "address_line2",
  "address-level2": "city",
  "address-level1": "state",
  "postal-code": "postal_code",
  country: "country",
  "country-name": "country",
  organization: "current_company",
  "organization-title": "current_title",
  url: "portfolio_url",
};

/**
 * Exact normalized labels. Whole-string matches, so "name" maps to full name
 * while "name of school" does not accidentally come along.
 */
const EXACT: ReadonlyArray<readonly [FieldKey, readonly string[]]> = [
  ["first_name", ["first name", "given name", "forename", "legal first name", "first"]],
  ["last_name", ["last name", "family name", "surname", "legal last name", "last"]],
  ["full_name", ["full name", "name", "your name", "legal name", "full legal name", "candidate name"]],
  ["preferred_name", ["preferred name", "nickname", "preferred first name", "goes by"]],
  ["email", ["email", "e mail", "email address", "e mail address", "your email", "work email", "personal email"]],
  ["phone", ["phone", "phone number", "mobile", "mobile number", "mobile phone", "telephone", "cell", "cell phone", "contact number", "primary phone"]],
  ["address_line1", ["address", "street address", "address line 1", "address 1", "street", "mailing address", "home address"]],
  ["address_line2", ["address line 2", "address 2", "apt suite", "apartment", "unit", "suite"]],
  ["city", ["city", "town", "city town", "city of residence"]],
  ["state", ["state", "province", "region", "state province", "state region", "county"]],
  ["postal_code", ["zip", "zip code", "postal code", "postcode", "zip postal code"]],
  ["country", ["country", "country of residence", "country region"]],
  ["linkedin_url", ["linkedin", "linkedin url", "linkedin profile", "linkedin profile url", "linkedin link"]],
  ["github_url", ["github", "github url", "github profile", "github link", "git hub"]],
  ["portfolio_url", ["portfolio", "portfolio url", "website", "personal website", "portfolio site", "personal site", "portfolio link"]],
  ["other_url", ["other website", "other url", "other link", "additional website"]],
  ["school", ["school", "university", "college", "institution", "school name", "university name"]],
  ["degree", ["degree", "degree type", "level of education", "highest degree", "education level"]],
  ["field_of_study", ["field of study", "major", "discipline", "area of study", "concentration", "course of study"]],
  ["education_start", ["education start date", "school start date"]],
  ["education_end", ["graduation date", "education end date", "expected graduation", "school end date", "graduation year"]],
  ["gpa", ["gpa", "grade point average", "cumulative gpa", "gpa score"]],
  ["current_company", ["current company", "company", "employer", "current employer", "most recent company", "current organization", "most recent employer"]],
  ["current_title", ["current title", "job title", "title", "current job title", "current role", "most recent title", "position", "current position"]],
  ["work_start", ["employment start date", "work start date", "start date"]],
  ["work_end", ["employment end date", "work end date", "end date"]],
];

/**
 * Distinctive phrases matched by containment. Only phrases that cannot
 * reasonably belong to another field are listed, because containment is where
 * a mapping table starts guessing.
 */
const CONTAINS: ReadonlyArray<readonly [FieldKey, readonly string[]]> = [
  ["linkedin_url", ["linkedin"]],
  ["github_url", ["github"]],
  ["email", ["email address"]],
  ["phone", ["phone number", "mobile number"]],
  ["postal_code", ["zip code", "postal code"]],
  ["gpa", ["grade point average"]],
  ["field_of_study", ["field of study"]],
  ["school", ["name of school", "name of university", "which university", "which school"]],
  ["work_authorized", [
    "legally authorized to work",
    "authorized to work",
    "legally eligible to work",
    "work authorization",
    "right to work",
  ]],
  ["requires_sponsorship", [
    "require sponsorship",
    "requires sponsorship",
    "need sponsorship",
    "visa sponsorship",
    "sponsorship now or in the future",
    "immigration sponsorship",
  ]],
  ["eeo_gender", ["gender"]],
  ["eeo_race", ["race", "ethnicity", "racial"]],
  ["eeo_hispanic", ["hispanic", "latino"]],
  ["eeo_veteran", ["veteran", "military service", "protected veteran"]],
  ["eeo_disability", ["disability", "disabled"]],
];

/**
 * Phrases that mean "this is an essay", regardless of anything else on the
 * control. A question shaped like this is never answered, even if some token
 * inside it also appears in the synonym table.
 */
const ESSAY_MARKERS: readonly string[] = [
  "why do you",
  "why are you",
  "why would you",
  "tell us",
  "tell me",
  "describe",
  "please provide an example",
  "provide an example",
  "give an example",
  "in your own words",
  "what makes you",
  "what interests you",
  "how did you hear",
  "how would you",
  "what are you looking for",
  "cover letter",
  "anything else",
  "additional information",
  "elaborate",
  "explain",
  "walk us through",
  "share a time",
  "exceptional ability",
];

/**
 * Field keys whose answer can legitimately live in a textarea. Everything else
 * in a textarea is treated as free text and skipped, because a multi-line box
 * is how forms ask for prose.
 */
const TEXTAREA_SAFE: ReadonlySet<FieldKey> = new Set<FieldKey>([
  "address_line1",
  "address_line2",
]);

export interface FieldKeyResolution {
  readonly key: FieldKey | null;
  /** Why we refused, when we refused. Shown verbatim in the review panel. */
  readonly refusal: "unrecognized" | "essay" | "ambiguous" | null;
}

const NO_MATCH: FieldKeyResolution = Object.freeze({ key: null, refusal: "unrecognized" });
const ESSAY: FieldKeyResolution = Object.freeze({ key: null, refusal: "essay" });
const AMBIGUOUS: FieldKeyResolution = Object.freeze({ key: null, refusal: "ambiguous" });

/**
 * Resolve a control to a canonical key.
 *
 * `hint` lets an adapter supply a key it knows from a stable attribute (a
 * Workday `data-automation-id`, say). Hints still go through the essay gate.
 */
export function resolveFieldKey(field: RawField, hint?: FieldKey | null): FieldKeyResolution {
  // An essay question is an essay question no matter what its label contains.
  // This runs before the hint so an adapter cannot accidentally open the door.
  if (isEssayQuestion(field)) return ESSAY;

  if (hint) return { key: hint, refusal: null };

  // 1. autocomplete: a standard the page opted into, so trust it first.
  const auto = (field.autocomplete ?? "").toLowerCase().trim();
  if (auto && auto !== "off" && auto !== "on") {
    // Tokens can carry a section or a billing/shipping prefix; the last token
    // is the field name itself.
    const token = auto.split(/\s+/).pop() ?? "";
    const mapped = AUTOCOMPLETE_MAP[token];
    if (mapped) return { key: mapped, refusal: null };
  }

  // An input typed as email or tel is unambiguous even with a useless label.
  const inputType = (field.element.getAttribute("type") ?? "").toLowerCase();
  if (inputType === "email") return { key: "email", refusal: null };
  if (inputType === "tel") return { key: "phone", refusal: null };

  const label = field.label;
  if (!label) return NO_MATCH;

  // 2. Whole-label match.
  const exact = matchExact(label);
  if (exact.length === 1) return { key: exact[0]!, refusal: null };
  if (exact.length > 1) return AMBIGUOUS;

  // 3. Distinctive containment.
  const contained = matchContains(label);
  if (contained.length === 1) return { key: contained[0]!, refusal: null };
  if (contained.length > 1) return AMBIGUOUS;

  return NO_MATCH;
}

/**
 * True when the control is asking for prose.
 *
 * Two independent signals, either sufficient: the wording is a question of the
 * "tell us about" family, or the control is a textarea that is not one of the
 * few keys a textarea legitimately holds. The second catches novel phrasings
 * the marker list has never seen, which is the whole risk with a keyword list.
 */
export function isEssayQuestion(field: RawField): boolean {
  const label = field.label;

  if (ESSAY_MARKERS.some((marker) => label.includes(marker))) return true;

  if (field.kind === "textarea") {
    // Give the safe keys a chance before calling it prose.
    const exact = matchExact(label);
    const only = exact.length === 1 ? exact[0]! : null;
    if (!only || !TEXTAREA_SAFE.has(only)) return true;
  }

  // A long question ending in a question mark is prose whatever it mentions.
  if (label.length > 80 && field.rawLabel.trimEnd().endsWith("?")) return true;

  return false;
}

function matchExact(label: string): FieldKey[] {
  const hits: FieldKey[] = [];
  for (const [key, phrases] of EXACT) {
    if (phrases.includes(label)) hits.push(key);
  }
  return hits;
}

function matchContains(label: string): FieldKey[] {
  const hits: FieldKey[] = [];
  for (const [key, phrases] of CONTAINS) {
    if (phrases.some((phrase) => label.includes(phrase))) hits.push(key);
  }

  // "Are you legally authorized to work, and will you require sponsorship?" is
  // one control asking two things. Refusing is right: answering half of a
  // compound legal question is worse than leaving it for the user.
  if (hits.includes("work_authorized") && hits.includes("requires_sponsorship")) {
    return ["work_authorized", "requires_sponsorship"];
  }

  // Race and Hispanic origin are asked as separate questions on the standard
  // EEO form, and "hispanic" is the more specific of the two.
  if (hits.includes("eeo_hispanic") && hits.includes("eeo_race")) {
    return ["eeo_hispanic"];
  }

  return hits;
}
