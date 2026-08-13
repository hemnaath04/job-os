/**
 * Work out what a form control is asking for, using the accessibility tree
 * first and CSS structure last.
 *
 * Every ATS reskins itself eventually and class names are the first thing to
 * change; `aria-labelledby`, `<label for>` and `autocomplete` are contracts
 * with screen readers, so they survive redesigns. The order below is roughly
 * the ARIA accessible-name computation, with a couple of ATS-shaped fallbacks
 * at the end.
 */
import type { ControlKind, FieldOption, RawField } from "./types.ts";

/** Attributes that hold a stable, semantic hook rather than a styling hook. */
const AUTOMATION_ATTRS = [
  "data-automation-id", // Workday
  "data-qa", // Lever
  "data-testid", // Ashby and others
  "data-ui", // Lever
] as const;

/**
 * The accessible name for a control, in the order a screen reader would.
 * Returns the raw text; callers normalize.
 */
export function resolveRawLabel(el: Element): string {
  // 1. aria-labelledby wins over everything, including a wrapping <label>.
  const labelledBy = el.getAttribute("aria-labelledby");
  if (labelledBy) {
    const text = labelledBy
      .split(/\s+/)
      .map((id) => el.ownerDocument.getElementById(id)?.textContent ?? "")
      .join(" ")
      .trim();
    if (text) return text;
  }

  // 2. aria-label.
  const ariaLabel = el.getAttribute("aria-label");
  if (ariaLabel && ariaLabel.trim()) return ariaLabel.trim();

  // 3. <label for="id">. Scoped by id, so it survives any amount of reshuffling.
  const id = el.getAttribute("id");
  if (id) {
    // CSS.escape keeps ids containing "--" or ":" (Workday, Ashby) from
    // producing an invalid selector that throws.
    const forLabel = el.ownerDocument.querySelector(`label[for="${cssEscape(id)}"]`);
    const text = labelTextWithoutControls(forLabel);
    if (text) return text;
  }

  // 4. A <label> wrapping the control.
  const wrapping = el.closest("label");
  const wrappingText = labelTextWithoutControls(wrapping);
  if (wrappingText) return wrappingText;

  // 5. A <legend> in the enclosing fieldset. This is how radio groups name
  //    themselves, so it matters for authorization and demographic questions.
  const legend = el.closest("fieldset")?.querySelector("legend");
  const legendText = labelTextWithoutControls(legend);
  if (legendText) return legendText;

  // 6. A labelled group container. Ashby and Workday both build custom widgets
  //    where the name sits on an ancestor with a group role.
  const group = el.closest('[role="group"], [role="radiogroup"]');
  if (group && group !== el) {
    const groupLabel = group.getAttribute("aria-label");
    if (groupLabel && groupLabel.trim()) return groupLabel.trim();
    const groupLabelledBy = group.getAttribute("aria-labelledby");
    if (groupLabelledBy) {
      const text = groupLabelledBy
        .split(/\s+/)
        .map((gid) => el.ownerDocument.getElementById(gid)?.textContent ?? "")
        .join(" ")
        .trim();
      if (text) return text;
    }
  }

  // 7. Nearest preceding text node in the same field wrapper. Workday renders
  //    its label as a sibling <label> with no `for`, so nothing above catches it.
  const sibling = nearestPrecedingLabelText(el);
  if (sibling) return sibling;

  // 8. Placeholder, then name/id humanized. Both are weak, and the field-key
  //    resolver treats them as such.
  const placeholder = el.getAttribute("placeholder");
  if (placeholder && placeholder.trim()) return placeholder.trim();

  const name = el.getAttribute("name") ?? id;
  return name ? humanize(name) : "";
}

/**
 * Lowercase, strip the decoration ATSs hang off labels, collapse whitespace.
 * "First Name *" and "first name (required)" both become "first name", so the
 * synonym table stays small and readable.
 */
export function normalizeLabel(raw: string): string {
  return raw
    .toLowerCase()
    .replace(/\(\s*(required|optional|mandatory)\s*\)/g, " ")
    .replace(/\brequired\b/g, " ")
    .replace(/[‘’]/g, "'")
    .replace(/[^a-z0-9'#+/ ]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

/** What kind of control this is, from the DOM rather than from a class name. */
export function classifyControl(el: Element): ControlKind {
  const tag = el.tagName.toLowerCase();

  if (tag === "textarea") return "textarea";
  if (tag === "select") return "select";

  if (tag === "input") {
    const type = (el.getAttribute("type") ?? "text").toLowerCase();
    if (type === "file") return "file";
    if (type === "checkbox") return "checkbox";
    if (type === "radio") return "radiogroup";
    if (type === "hidden" || type === "submit" || type === "button" || type === "image") {
      return "unsupported";
    }
    return "text";
  }

  // Custom widgets. Ashby and Workday build comboboxes out of buttons and divs;
  // the role attribute is the only honest signal.
  const role = (el.getAttribute("role") ?? "").toLowerCase();
  const popup = (el.getAttribute("aria-haspopup") ?? "").toLowerCase();

  // A trigger whose options only exist once it is open.
  if (popup === "listbox" || popup === "menu" || popup === "true") return "popup_select";
  if (role === "radiogroup") return "radiogroup";
  if (role === "combobox" || role === "listbox") return "popup_select";
  if (role === "checkbox" || role === "switch") return "checkbox";
  if (role === "textbox") {
    return el.getAttribute("aria-multiline") === "true" ? "textarea" : "text";
  }

  if (el.getAttribute("contenteditable") === "true") return "textarea";

  return "unsupported";
}

/** True when the form marks this control as required, by any of the ways ATSs
 * do it. Adapters can override with `isRequired` when they know better. */
export function detectRequired(el: Element, rawLabel: string): boolean {
  if (el.hasAttribute("required")) return true;
  if (el.getAttribute("aria-required") === "true") return true;

  // A red asterisk in the label is the most common signal and the only one
  // some forms give. Look at the label element rather than the whole wrapper so
  // a nearby required field does not bleed into this one.
  const id = el.getAttribute("id");
  const labelEl =
    (id ? el.ownerDocument.querySelector(`label[for="${cssEscape(id)}"]`) : null) ??
    el.closest("label") ??
    el.closest("fieldset")?.querySelector("legend") ??
    null;

  if (labelEl) {
    if (labelEl.querySelector('abbr[title="required" i], .required, [aria-hidden="true"].asterisk')) {
      return true;
    }
    if (/\*\s*$/.test(labelEl.textContent ?? "")) return true;
    if (/\brequired\b/i.test(labelEl.textContent ?? "")) return true;
  }

  return /\*\s*$/.test(rawLabel) || /\brequired\b/i.test(rawLabel);
}

/** Options of a select or radio group, in document order. */
export function collectOptions(el: Element): FieldOption[] {
  if (el instanceof el.ownerDocument.defaultView!.HTMLSelectElement) {
    return Array.from(el.options).map((o) => ({
      value: o.value,
      label: (o.textContent ?? "").trim(),
    }));
  }

  const doc = el.ownerDocument;

  // A radio group container: collect the member inputs.
  const radios = el.querySelectorAll('input[type="radio"]');
  if (radios.length > 0) {
    return Array.from(radios).map((r) => ({
      value: r.getAttribute("value") ?? "",
      label: resolveRawLabel(r).trim(),
    }));
  }

  // An individual radio input: collect its siblings by name.
  if (el.getAttribute("type") === "radio") {
    const name = el.getAttribute("name");
    if (name) {
      return Array.from(doc.querySelectorAll(`input[type="radio"][name="${cssEscape(name)}"]`)).map(
        (r) => ({ value: r.getAttribute("value") ?? "", label: resolveRawLabel(r).trim() }),
      );
    }
  }

  // ARIA listbox / custom combobox.
  const ariaOptions = el.querySelectorAll('[role="option"]');
  if (ariaOptions.length > 0) {
    return Array.from(ariaOptions).map((o) => ({
      value: o.getAttribute("data-value") ?? (o.textContent ?? "").trim(),
      label: (o.textContent ?? "").trim(),
    }));
  }

  return [];
}

/** The stable semantic hook, if the ATS gave us one. */
export function automationId(el: Element): string | null {
  for (const attr of AUTOMATION_ATTRS) {
    const value = el.getAttribute(attr);
    if (value && value.trim()) return value.trim();
  }
  return null;
}

/** Assemble the full description of a control. Adapters call this so every
 * adapter reports fields the same way and the planner stays adapter-agnostic. */
export function describeField(el: Element): RawField {
  const rawLabel = resolveRawLabel(el);
  const kind = classifyControl(el);
  return {
    element: el,
    kind,
    label: normalizeLabel(rawLabel),
    rawLabel: rawLabel.replace(/\s+/g, " ").trim(),
    name: el.getAttribute("name"),
    id: el.getAttribute("id"),
    autocomplete: el.getAttribute("autocomplete"),
    placeholder: el.getAttribute("placeholder"),
    required: detectRequired(el, rawLabel),
    options: collectOptions(el),
    automationId: automationId(el),
  };
}

/** Label text minus any control nested inside it, so a wrapping label does not
 * return the option text of its own radio button. */
function labelTextWithoutControls(label: Element | null | undefined): string {
  if (!label) return "";
  const clone = label.cloneNode(true) as Element;
  clone.querySelectorAll("input, select, textarea, button").forEach((n) => n.remove());
  return (clone.textContent ?? "").replace(/\s+/g, " ").trim();
}

/**
 * Walk up to a plausible field wrapper and take the first label-ish text in it.
 * Bounded to four levels so a deeply nested control cannot pick up the section
 * heading of an unrelated block.
 */
function nearestPrecedingLabelText(el: Element): string {
  let node: Element | null = el.parentElement;
  for (let depth = 0; node && depth < 4; depth += 1, node = node.parentElement) {
    const candidate = node.querySelector("label, legend, [data-automation-id$='label']");
    const text = labelTextWithoutControls(candidate);
    if (text) return text;
  }
  return "";
}

function humanize(token: string): string {
  return token
    .replace(/[_\-.[\]]+/g, " ")
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/\s+/g, " ")
    .trim();
}

/** CSS.escape is not present in every test DOM; this covers the characters ATS
 * ids actually contain. */
function cssEscape(value: string): string {
  const globalCss = (globalThis as { CSS?: { escape?: (v: string) => string } }).CSS;
  if (globalCss?.escape) return globalCss.escape(value);
  return value.replace(/["\\\]\[:.#$&*+,/;<=>?@^`{|}~]/g, "\\$&");
}
