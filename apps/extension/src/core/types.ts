/**
 * The vocabulary the whole extension shares.
 *
 * The important thing in this file is `SourcedValue`. It is the only shape the
 * filler will accept, and it cannot be produced by an object literal because of
 * the branding symbol below. That is what makes safety invariant 1 (never
 * free-text an answer) a property of the type system rather than a promise in a
 * code review: to fill a field you must hold a value that a verified profile
 * fact produced, and `provenance.ts` is the only module that can mint one.
 */

/** Present on every SourcedValue, impossible to write from outside provenance.ts. */
declare const SOURCE_BRAND: unique symbol;

/** Canonical field identities. A form control that does not resolve to one of
 * these is never filled, no matter what it looks like. Adding a key here is a
 * deliberate act: it means "there is a verbatim profile value for this". */
export const FIELD_KEYS = [
  // Identity
  "first_name",
  "last_name",
  "full_name",
  "preferred_name",
  // Contact
  "email",
  "phone",
  // Location
  "address_line1",
  "address_line2",
  "city",
  "state",
  "postal_code",
  "country",
  // Links
  "linkedin_url",
  "github_url",
  "portfolio_url",
  "other_url",
  // Education
  "school",
  "degree",
  "field_of_study",
  "education_start",
  "education_end",
  "gpa",
  // Work
  "current_company",
  "current_title",
  "work_start",
  "work_end",
  // Authorization. Stored as verified facts because a wrong answer here is a
  // legal problem, not a formatting problem.
  "work_authorized",
  "requires_sponsorship",
  // Demographic. Opt-in per field, blank by default. See eeo.ts.
  "eeo_gender",
  "eeo_race",
  "eeo_hispanic",
  "eeo_veteran",
  "eeo_disability",
] as const;

export type FieldKey = (typeof FIELD_KEYS)[number];

const FIELD_KEY_SET: ReadonlySet<string> = new Set(FIELD_KEYS);

export function isFieldKey(value: string): value is FieldKey {
  return FIELD_KEY_SET.has(value);
}

/** Field keys that carry demographic answers. Never filled unless the user has
 * explicitly opted that specific key in. */
export const EEO_FIELD_KEYS: ReadonlySet<FieldKey> = new Set<FieldKey>([
  "eeo_gender",
  "eeo_race",
  "eeo_hispanic",
  "eeo_veteran",
  "eeo_disability",
]);

/**
 * A profile fact that the API returned with `verified === true`.
 *
 * `parseVerifiedProfile` is the only constructor, and it drops unverified rows
 * before they reach any other module, so nothing downstream has to remember to
 * check the flag. Mirrors `ProfileFact` in
 * apps/api/src/job_os/db/models/profile.py.
 */
export interface VerifiedFact {
  readonly id: string;
  readonly kind: string;
  readonly title: string;
  readonly org: string | null;
  readonly startDate: string | null;
  readonly endDate: string | null;
  readonly location: string | null;
  readonly payload: Readonly<Record<string, unknown>>;
}

/** Where a value came from, in words a person can check against their profile. */
export interface FactCitation {
  /** `ProfileFact.id`, so the review UI can deep link to the profile row. */
  readonly factId: string;
  /** `ProfileFact.kind`, for example "education" or "work". */
  readonly kind: string;
  /** Human label for the fact, for example "Northeastern University". */
  readonly factLabel: string;
  /** Which attribute of the fact produced the value, for example "payload.gpa". */
  readonly attribute: string;
}

/**
 * A value that provably came from a verified fact, verbatim.
 *
 * The brand makes `{ value: "...", citation: {...} } as SourcedValue` the only
 * way to forge one, and the codebase scanner test fails the build on that cast.
 */
export interface SourcedValue {
  readonly [SOURCE_BRAND]: true;
  readonly value: string;
  readonly citation: FactCitation;
}

/** What kind of control we are looking at. Drives both filling and skipping. */
export type ControlKind =
  | "text"
  | "textarea"
  | "select"
  /** A button that opens a listbox, rather than a native `<select>`. Workday
   * and Ashby both build their dropdowns this way, and the options do not exist
   * in the DOM until it is opened, so these are matched at fill time. */
  | "popup_select"
  | "radiogroup"
  | "checkbox"
  | "file"
  | "unsupported";

/** One option of a select or radio group, kept with its original casing so we
 * can write back exactly what the form expects. */
export interface FieldOption {
  readonly value: string;
  readonly label: string;
}

/**
 * A form control as an adapter found it, before any decision about filling.
 * `element` is the thing we would write to; for a radio group it is the
 * container, and `options` carries the individual inputs' values.
 */
export interface RawField {
  readonly element: Element;
  readonly kind: ControlKind;
  /** Best label we could resolve, already normalized for matching. */
  readonly label: string;
  /** The label exactly as shown to the user, for the review panel. */
  readonly rawLabel: string;
  readonly name: string | null;
  readonly id: string | null;
  readonly autocomplete: string | null;
  readonly placeholder: string | null;
  readonly required: boolean;
  readonly options: readonly FieldOption[];
  /** Adapter-specific stable hook, for example Workday's data-automation-id. */
  readonly automationId: string | null;
}

/** Why a field was deliberately left alone. Every one of these is shown to the
 * user, because silence is how the competitor's failure mode works. */
export type SkipReason =
  | "unrecognized_question"
  | "free_text_answer"
  | "no_verified_fact"
  | "eeo_not_opted_in"
  | "unsupported_control"
  | "no_matching_option"
  | "already_filled"
  | "user_disabled";

export interface PlannedFill {
  readonly field: RawField;
  readonly key: FieldKey;
  readonly sourced: SourcedValue;
  /** For selects and radio groups, the option we matched. */
  readonly option: FieldOption | null;
}

export interface PlannedSkip {
  readonly field: RawField;
  /** Null when the label did not resolve to any canonical key. */
  readonly key: FieldKey | null;
  readonly reason: SkipReason;
  /** One sentence the user reads in the panel. */
  readonly detail: string;
}

/** A required control that is still empty after filling. The whole point of
 * invariant 2: an application that looks complete but is not gets silently
 * rejected, and the applicant never finds out why. */
export interface RequiredGap {
  readonly field: RawField;
  readonly rawLabel: string;
  readonly kind: ControlKind;
}

export interface FillPlan {
  readonly ats: AtsId;
  readonly fills: readonly PlannedFill[];
  readonly skips: readonly PlannedSkip[];
}

export interface FillResult {
  readonly ats: AtsId;
  readonly filled: readonly PlannedFill[];
  /** Planned but the write did not take, for example a React-controlled input
   * that reverted. Reported honestly rather than counted as a success. */
  readonly failed: readonly PlannedFill[];
  readonly skipped: readonly PlannedSkip[];
  readonly requiredGaps: readonly RequiredGap[];
}

export type AtsId =
  | "greenhouse"
  | "lever"
  | "ashby"
  | "workday"
  | "smartrecruiters"
  | "generic";

/** Per-key user consent for demographic questions. Absent means blank. */
export type EeoConsent = Partial<Record<FieldKey, boolean>>;

export interface ExtensionSettings {
  readonly appOrigin: string;
  readonly eeoConsent: EeoConsent;
}
