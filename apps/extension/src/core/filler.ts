/**
 * Execute a plan and report exactly what happened.
 *
 * Two things matter here beyond writing the values. First, `isSourced` is
 * checked again at the last moment: even holding a `PlannedFill`, the filler
 * refuses a value whose provenance brand is missing, so a bug that manufactures
 * a plan entry still cannot put unsourced text on the page. Second, a write
 * that does not stick is reported as a failure rather than counted as a
 * success, because "we filled 14 fields" when four of them silently reverted is
 * the same lie by a different route.
 */
import {
  openAndSelect,
  readValue,
  setCheckboxValue,
  setRadioValue,
  setSelectValue,
  setTextValue,
  SubmitRefusedError,
} from "./dom-guard.ts";
import { matchOption } from "./planner.ts";
import { isSourced } from "./provenance.ts";
import type { FillPlan, FillResult, PlannedFill, PlannedSkip, RawField, RequiredGap } from "./types.ts";

/**
 * Async because popup dropdowns have to be opened before their options exist.
 * Everything else completes synchronously.
 */
export async function applyFillPlan(
  plan: FillPlan,
  allFields: readonly RawField[],
): Promise<FillResult> {
  const filled: PlannedFill[] = [];
  const failed: PlannedFill[] = [];
  const skipped: PlannedSkip[] = [...plan.skips];

  for (const fill of plan.fills) {
    // Last gate before the page. A plan entry whose value is not branded did
    // not come from a verified fact, whatever the type declarations say.
    if (!isSourced(fill.sourced)) {
      skipped.push({
        field: fill.field,
        key: fill.key,
        reason: "no_verified_fact",
        detail: "Value failed its provenance check at write time and was discarded.",
      });
      continue;
    }

    let ok = false;
    try {
      ok = await writeOne(fill);
    } catch (error) {
      // A submit refusal is a caught bug, not a crash. Keep filling the rest.
      if (!(error instanceof SubmitRefusedError)) throw error;
      ok = false;
    }

    if (ok) {
      filled.push(fill);
    } else if (fill.field.kind === "popup_select") {
      // A dropdown whose open list held nothing matching is a deliberate blank,
      // not a broken write, so say which value went unmatched.
      skipped.push({
        field: fill.field,
        key: fill.key,
        reason: "no_matching_option",
        detail: `This menu offered no exact match for "${fill.sourced.value}", so nothing was selected.`,
      });
    } else {
      failed.push(fill);
    }
  }

  return {
    ats: plan.ats,
    filled,
    failed,
    skipped,
    requiredGaps: findRequiredGaps(allFields),
  };
}

async function writeOne(fill: PlannedFill): Promise<boolean> {
  const { field, option, sourced } = fill;

  switch (field.kind) {
    case "text":
    case "textarea":
      return setTextValue(field.element, sourced.value);
    case "select":
      return option ? setSelectValue(field.element, option.value) : false;
    case "popup_select": {
      const picked = await openAndSelect(field.element, sourced.value, matchOption);
      return picked !== null;
    }
    case "radiogroup":
      return option ? setRadioValue(field.element, option.value) : false;
    case "checkbox":
      // A checkbox only ever gets ticked from an affirmative verified value.
      return setCheckboxValue(field.element, isAffirmative(sourced.value));
    default:
      return false;
  }
}

/**
 * Required controls still empty after the fill.
 *
 * This is the warning that the competitor's users never got. The recruiter
 * quote is the specification: an application arrives marked complete with core
 * responses missing, so it is rejected, and the applicant is never told. Custom
 * questions are the usual occupants of this list precisely because the
 * extension refuses to invent answers for them, which makes surfacing them
 * loudly the other half of that promise.
 */
export function findRequiredGaps(fields: readonly RawField[]): RequiredGap[] {
  const gaps: RequiredGap[] = [];

  for (const field of fields) {
    if (!field.required) continue;
    if (field.kind === "unsupported") continue;

    if (readValue(field.element).trim() !== "") continue;

    gaps.push({
      field,
      rawLabel: field.rawLabel || "(unlabelled field)",
      kind: field.kind,
    });
  }

  return gaps;
}

function isAffirmative(value: string): boolean {
  return /^(yes|y|true|1|i agree|agree|confirmed)$/i.test(value.trim());
}
