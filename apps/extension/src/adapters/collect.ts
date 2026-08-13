/**
 * Shared field collection. Every adapter starts from this and adds only what
 * its own widgets need, so a fix to label resolution or radio grouping lands
 * everywhere at once.
 */
import { describeField } from "../core/labels.ts";
import type { RawField } from "../core/types.ts";

/** Controls worth looking at. Hidden and button-ish inputs are excluded here
 * rather than classified and discarded later, to keep the review panel free of
 * noise the user cannot act on. */
const CONTROL_SELECTOR = [
  "input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=image]):not([type=reset])",
  "textarea",
  "select",
  '[role="radiogroup"]',
  '[role="combobox"]',
  '[contenteditable="true"]',
].join(", ");

/**
 * Collect native controls under `root`.
 *
 * Radio buttons are grouped by `name` so a five-option question is one field in
 * the plan rather than five, which is what the user expects to see and what
 * required-gap detection needs to be correct.
 */
export function collectStandard(root: Element): RawField[] {
  const seenRadioGroups = new Set<string>();
  const fields: RawField[] = [];

  for (const el of Array.from(root.querySelectorAll(CONTROL_SELECTOR))) {
    if (isInvisible(el)) continue;

    if (el.getAttribute("type") === "radio") {
      const name = el.getAttribute("name");
      if (name) {
        if (seenRadioGroups.has(name)) continue;
        seenRadioGroups.add(name);
        fields.push(describeRadioGroup(root, el, name));
        continue;
      }
    }

    // A container we already described as a radio group; do not also describe
    // its members individually.
    if (el.closest('[role="radiogroup"]') && !el.matches('[role="radiogroup"]')) continue;

    fields.push(describeField(el));
  }

  return fields;
}

/**
 * Describe a set of same-named radios as one field.
 *
 * The element we report is the nearest container holding all of them, because
 * that is what carries the question's label. Falling back to the first input
 * keeps a group with no wrapper usable.
 */
function describeRadioGroup(root: Element, first: Element, name: string): RawField {
  const container = radioContainer(root, name) ?? first;
  const described = describeField(container === first ? first : container);

  // The container's own label is the question; make sure options come from the
  // inputs rather than from whatever else the container holds.
  return {
    ...described,
    kind: "radiogroup",
    element: container,
    name,
    required: described.required || first.hasAttribute("required") || first.getAttribute("aria-required") === "true",
  };
}

/** The tightest ancestor containing every radio of this name. */
function radioContainer(root: Element, name: string): Element | null {
  const radios = Array.from(
    root.querySelectorAll(`input[type="radio"][name="${name.replace(/"/g, '\\"')}"]`),
  );
  if (radios.length === 0) return null;

  const explicit = radios[0]!.closest('fieldset, [role="radiogroup"]');
  if (explicit && radios.every((r) => explicit.contains(r))) return explicit;

  let candidate: Element | null = radios[0]!.parentElement;
  while (candidate && !radios.every((r) => candidate!.contains(r))) {
    candidate = candidate.parentElement;
  }
  return candidate;
}

/**
 * Skip controls the user cannot see.
 *
 * Multi-step wizards (Workday especially) keep later steps in the DOM with
 * `hidden` or `display: none`, and filling those writes into a page the user
 * is not looking at. Only checks attributes and inline styles: computed styles
 * are unavailable in the test DOM and expensive on a large form.
 */
function isInvisible(el: Element): boolean {
  if (el.hasAttribute("hidden")) return true;
  if (el.getAttribute("aria-hidden") === "true") return true;
  if ((el as HTMLElement).style?.display === "none") return true;
  if ((el as HTMLElement).style?.visibility === "hidden") return true;

  const closedAncestor = el.closest("[hidden], [aria-hidden='true']");
  return closedAncestor !== null;
}
