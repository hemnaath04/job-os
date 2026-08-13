/**
 * Decide, for every control on the form, either which verified value goes in it
 * or why it stays blank.
 *
 * Planning is separate from filling so the decision set can be tested without a
 * browser and shown to the user before anything is written. Every control ends
 * up in exactly one of the two lists: there is no third bucket of fields that
 * were quietly ignored, because a blank the user cannot see is the failure mode
 * this whole design is built around.
 */
import { isEssayQuestion, resolveFieldKey } from "./field-key.ts";
import { isBlockedByConsent, type ProfileValues } from "./mapping.ts";
import { readValue } from "./dom-guard.ts";
import type { AtsAdapter } from "../adapters/types.ts";
import type {
  EeoConsent,
  FieldKey,
  FieldOption,
  FillPlan,
  PlannedFill,
  PlannedSkip,
  RawField,
  SkipReason,
} from "./types.ts";

export interface PlanInput {
  readonly adapter: AtsAdapter;
  readonly fields: readonly RawField[];
  readonly values: ProfileValues;
  readonly consent: EeoConsent;
  /** Keys the user switched off in the panel for this run. */
  readonly disabledKeys?: ReadonlySet<FieldKey>;
  /** Overwrite values the page already had. Off by default: text the user typed
   * outranks anything we know. */
  readonly overwriteExisting?: boolean;
}

export function buildFillPlan(input: PlanInput): FillPlan {
  const { adapter, fields, values, consent } = input;
  const disabled = input.disabledKeys ?? new Set<FieldKey>();

  const fills: PlannedFill[] = [];
  const skips: PlannedSkip[] = [];

  const skip = (
    field: RawField,
    key: FieldKey | null,
    reason: SkipReason,
    detail: string,
  ): void => {
    skips.push({ field, key, reason, detail });
  };

  for (const field of fields) {
    if (field.kind === "unsupported") {
      skip(field, null, "unsupported_control", "This control is not one the extension can fill.");
      continue;
    }

    if (field.kind === "file") {
      // Attaching a resume needs a real File and a user gesture. Saying so is
      // better than filling the surrounding fields and letting the user submit
      // without the document.
      skip(field, null, "unsupported_control", "Attach this file yourself. The extension does not upload documents.");
      continue;
    }

    const hint = adapter.fieldKeyHint?.(field) ?? null;
    const { key, refusal } = resolveFieldKey(field, hint);

    if (key === null) {
      const detail =
        refusal === "essay"
          ? "Free-text question. The extension never writes an answer it cannot quote from your profile."
          : refusal === "ambiguous"
            ? "This question could mean more than one profile field, so it was left for you."
            : "No profile field matches this question.";
      skip(field, null, refusal === "essay" ? "free_text_answer" : "unrecognized_question", detail);
      continue;
    }

    // Belt and braces: a hint could name a key whose control is a prose box.
    // `resolveFieldKey` already refuses that, and this refuses it again in case
    // an adapter grows a path around it.
    if (isEssayQuestion(field)) {
      skip(field, key, "free_text_answer", "Free-text question. Left blank on purpose.");
      continue;
    }

    if (disabled.has(key)) {
      skip(field, key, "user_disabled", "You switched this field off for this run.");
      continue;
    }

    if (isBlockedByConsent(key, consent)) {
      skip(
        field,
        key,
        "eeo_not_opted_in",
        "Demographic question. Blank unless you opt this specific field in.",
      );
      continue;
    }

    const existing = readValue(field.element).trim();
    if (existing !== "" && input.overwriteExisting !== true) {
      skip(field, key, "already_filled", "Already has a value, so it was left alone.");
      continue;
    }

    const sourcedValue = values.get(key);
    if (!sourcedValue) {
      skip(
        field,
        key,
        "no_verified_fact",
        "Your profile has no verified fact for this. Add one and it will fill next time.",
      );
      continue;
    }

    // Selects and radio groups can only take a value the form already offers.
    if (field.kind === "select" || field.kind === "radiogroup" || field.kind === "checkbox") {
      const option = matchOption(field.options, sourcedValue.value);
      if (!option) {
        skip(
          field,
          key,
          "no_matching_option",
          `None of this menu's choices match "${sourcedValue.value}" exactly, so nothing was selected.`,
        );
        continue;
      }
      fills.push({ field, key, sourced: sourcedValue, option });
      continue;
    }

    fills.push({ field, key, sourced: sourcedValue, option: null });
  }

  return { ats: adapter.id, fills, skips };
}

/**
 * Find the option that means the same thing as our value.
 *
 * Exact first, then case-insensitive, then punctuation-insensitive. It stops
 * there: no prefix or fuzzy matching, because "US" prefix-matching "US Virgin
 * Islands" is how an autofiller tells an employer the wrong country. When
 * nothing matches exactly the planner leaves the control alone and says so.
 */
export function matchOption(
  options: readonly FieldOption[],
  value: string,
): FieldOption | null {
  const wanted = value.trim();
  if (wanted === "") return null;

  const byValue = options.find((o) => o.value === wanted);
  if (byValue) return byValue;

  const byLabel = options.find((o) => o.label === wanted);
  if (byLabel) return byLabel;

  const lower = wanted.toLowerCase();
  const ciMatches = options.filter(
    (o) => o.value.toLowerCase() === lower || o.label.toLowerCase() === lower,
  );
  if (ciMatches.length === 1) return ciMatches[0]!;

  const norm = normalizeOption(wanted);
  if (norm === "") return null;
  const normMatches = options.filter(
    (o) => normalizeOption(o.value) === norm || normalizeOption(o.label) === norm,
  );
  if (normMatches.length === 1) return normMatches[0]!;

  return null;
}

function normalizeOption(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}
