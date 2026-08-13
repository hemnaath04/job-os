/**
 * The only place a `SourcedValue` can be created.
 *
 * Safety invariant 1 lives here. Every value the extension is willing to type
 * into a form has to pass through `sourced()`, which requires a `VerifiedFact`
 * and the name of the attribute the text came from. There is deliberately no
 * function in this codebase that turns a question into an answer: if a value
 * did not come out of the profile vault verbatim, it does not exist.
 *
 * The competitor failure this prevents, in the user's words: an agent answered
 * "Please provide an example of your exceptional ability" with a fluent
 * paragraph about leading a team project recognised at a national conference,
 * none of which appeared anywhere in the profile.
 */
import type { FactCitation, SourcedValue, VerifiedFact } from "./types.ts";

/** Matches the symbol declared in types.ts. Values carry it at runtime too, so
 * a forged object literal fails `isSourced` even if a cast slipped past tsc. */
const SOURCE_BRAND = Symbol.for("job-os.sourced-value");

/**
 * Mint a value from a fact.
 *
 * Returns null when the fact does not actually carry usable text at that
 * attribute, which is the honest answer far more often than people expect: a
 * profile with no GitHub URL should produce a blank GitHub field, not a guess
 * assembled from the user's name.
 */
export function sourced(
  fact: VerifiedFact,
  attribute: string,
  rawValue: unknown,
): SourcedValue | null {
  const value = normalizeToText(rawValue);
  if (value === null) return null;

  const citation: FactCitation = {
    factId: fact.id,
    kind: fact.kind,
    factLabel: describeFact(fact),
    attribute,
  };
  return brand(value, citation);
}

/**
 * Mint a value whose text is a *subset* of a fact's text, not a rewrite.
 *
 * Splitting "Hemnaath Balasubramani" into a first and last name is the one
 * transformation worth allowing, because the result is still literally present
 * in the source. `assertVerbatim` enforces that: if the derived text is not a
 * substring of the original, this throws rather than filling something the user
 * never wrote.
 */
export function sourcedSubstring(
  fact: VerifiedFact,
  attribute: string,
  original: unknown,
  derived: string,
): SourcedValue | null {
  const source = normalizeToText(original);
  const value = derived.trim();
  if (source === null || value === "") return null;
  assertVerbatim(source, value);
  return brand(value, {
    factId: fact.id,
    kind: fact.kind,
    factLabel: describeFact(fact),
    attribute,
  });
}

/** The date notations application forms actually ask for. */
export type DateFormat = "iso" | "mm/yyyy" | "mm/dd/yyyy" | "yyyy";

/**
 * Mint a date in the notation a form wants.
 *
 * This is the one place a written value is allowed to differ character for
 * character from the stored fact, and it is fenced in hard: the input must be
 * an ISO date, the output is one of four fixed notations, and no other module
 * may call it with anything but a date. Rewriting 2026-01-15 as 01/2026 cannot
 * invent a claim the way prose can, and the review panel shows the stored value
 * next to the written one so the user sees both.
 */
export function sourcedDate(
  fact: VerifiedFact,
  attribute: string,
  isoDate: unknown,
  format: DateFormat,
): SourcedValue | null {
  if (typeof isoDate !== "string") return null;
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(isoDate.trim());
  if (!match) return null;

  const [, year, month, day] = match as unknown as [string, string, string, string];
  const value =
    format === "iso"
      ? isoDate.trim()
      : format === "yyyy"
        ? year
        : format === "mm/yyyy"
          ? `${month}/${year}`
          : `${month}/${day}/${year}`;

  return brand(value, {
    factId: fact.id,
    kind: fact.kind,
    factLabel: describeFact(fact),
    attribute: format === "iso" ? attribute : `${attribute} (${isoDate.trim()} as ${format})`,
  });
}

/**
 * Throw unless `derived` appears inside `source`.
 *
 * Case-insensitive so "USA" from "usa" passes, whitespace-collapsed so a value
 * split across lines passes. Nothing else is forgiven, and in particular no
 * amount of paraphrase passes.
 */
export function assertVerbatim(source: string, derived: string): void {
  const haystack = collapse(source).toLowerCase();
  const needle = collapse(derived).toLowerCase();
  if (!haystack.includes(needle)) {
    throw new Error(
      `provenance violation: "${redactForError(derived)}" is not verbatim in its source fact`,
    );
  }
}

/** Runtime check that a value really came from `sourced()`. Used by the filler
 * as a second gate behind the type system. */
export function isSourced(value: unknown): value is SourcedValue {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Record<PropertyKey, unknown>;
  if (candidate[SOURCE_BRAND] !== true) return false;
  if (typeof candidate.value !== "string" || candidate.value === "") return false;
  const citation = candidate.citation as Partial<FactCitation> | undefined;
  return (
    typeof citation === "object" &&
    citation !== null &&
    typeof citation.factId === "string" &&
    citation.factId !== "" &&
    typeof citation.attribute === "string"
  );
}

/** A short human description of the fact, used in the review panel. */
export function describeFact(fact: VerifiedFact): string {
  if (fact.org && fact.org !== fact.title) return `${fact.title} at ${fact.org}`;
  return fact.title;
}

function brand(value: string, citation: FactCitation): SourcedValue {
  const out = { value, citation };
  Object.defineProperty(out, SOURCE_BRAND, {
    value: true,
    enumerable: false,
    writable: false,
    configurable: false,
  });
  return Object.freeze(out) as unknown as SourcedValue;
}

/**
 * Coerce a payload value to fillable text, or null.
 *
 * Numbers and booleans are allowed because a GPA arrives as 3.9 and an
 * authorization flag arrives as true. Objects and arrays are not: flattening
 * them means inventing a format, and inventing a format is how you end up
 * writing something the user never said.
 */
function normalizeToText(raw: unknown): string | null {
  if (typeof raw === "string") {
    const trimmed = raw.trim();
    return trimmed === "" ? null : trimmed;
  }
  if (typeof raw === "number" && Number.isFinite(raw)) return String(raw);
  if (typeof raw === "boolean") return raw ? "Yes" : "No";
  return null;
}

function collapse(text: string): string {
  return text.replace(/\s+/g, " ").trim();
}

/** Errors get logged; profile text must not. Keep enough to debug, not enough
 * to leak. */
function redactForError(text: string): string {
  return text.length <= 3 ? "***" : `${text.slice(0, 2)}***(${text.length})`;
}
