/**
 * The only module in this extension permitted to write to a page.
 *
 * Safety invariant 2 lives here: there is no submit action anywhere in this
 * codebase, and this file is the reason that claim can be checked rather than
 * asserted. `tests/no-submit.test.ts` scans every source file and fails if
 * `.click(`, `.submit(`, `requestSubmit`, or a synthetic Enter key appears
 * outside this file, and `safeClick` below refuses to click anything that looks
 * like a submit control even when a caller asks it to.
 *
 * The failure this prevents was described by a recruiter on the ATS side:
 * automated applications arrive marked complete with required responses
 * missing, get auto-rejected, and the applicant never learns why. So the
 * extension fills and stops. The human presses the button.
 */

/** Text on a control that means "this sends the application". */
const SUBMIT_TEXT =
  /\b(submit|apply now|apply for this job|send application|finish|complete application|i'?m done)\b/i;

/** Stable submit hooks per ATS. Semantic attributes, not styling classes. */
const SUBMIT_AUTOMATION_IDS = new Set([
  "bottom-navigation-next-button", // Workday wizard advance
  "wd-CommandButton_uic_okButton",
  "quickApplySubmitButton",
  "btn-submit", // Lever, confirmed against the saved fixture
]);

/** What an unselected dropdown says about itself. */
const PLACEHOLDER_TEXT = /^(select one|select\.\.\.|select|choose one|choose|please select|-+|)$/i;

export class SubmitRefusedError extends Error {
  constructor(description: string) {
    super(`refused to click a submit control: ${description}`);
    this.name = "SubmitRefusedError";
  }
}

/**
 * True when clicking this element could send the application.
 *
 * Deliberately over-broad. A false positive costs one radio button the user
 * ticks themselves; a false negative costs a real submitted application, so the
 * asymmetry decides the tuning. A bare `<button>` with no type counts, because
 * inside a form its implicit type is submit.
 */
export function isSubmitControl(el: Element): boolean {
  const tag = el.tagName.toLowerCase();
  const type = (el.getAttribute("type") ?? "").toLowerCase();

  if (tag === "input" && (type === "submit" || type === "image")) return true;
  if (tag === "button") {
    // No type attribute means type=submit per the HTML spec when inside a form.
    if (type === "" || type === "submit") return true;
  }

  const automation =
    el.getAttribute("data-automation-id") ?? el.getAttribute("data-qa") ?? "";
  if (SUBMIT_AUTOMATION_IDS.has(automation)) return true;

  const text = `${el.textContent ?? ""} ${el.getAttribute("aria-label") ?? ""} ${el.getAttribute("value") ?? ""}`;
  if (SUBMIT_TEXT.test(text)) return true;

  // A control nested inside a submit button is a submit control.
  const owner = el.closest('button, input[type="submit"], [role="button"]');
  if (owner && owner !== el) {
    const ownerText = `${owner.textContent ?? ""} ${owner.getAttribute("aria-label") ?? ""}`;
    if (SUBMIT_TEXT.test(ownerText)) return true;
  }

  return false;
}

/**
 * Click an element, unless it could submit.
 *
 * Radio buttons and custom checkbox widgets genuinely need a click, which is
 * why this exists at all rather than being banned outright.
 */
export function safeClick(el: Element): void {
  if (isSubmitControl(el)) {
    throw new SubmitRefusedError(describe(el));
  }
  (el as HTMLElement).click();
}

/**
 * Write a value into a text input or textarea and tell the framework about it.
 *
 * React tracks the last value it set on the node and swallows an `input` event
 * whose value it believes it already knows, so assigning `.value` directly gets
 * reverted on the next render. Going through the prototype's native setter
 * updates the node without touching React's tracker, and the event that follows
 * is then treated as a genuine user edit. Same trick works for Vue and Angular.
 *
 * No keyboard events are dispatched, ever. A synthetic Enter in a single-input
 * form submits it, which is precisely the outcome this extension exists to
 * prevent.
 */
export function setTextValue(el: Element, value: string): boolean {
  const input = el as HTMLInputElement | HTMLTextAreaElement;
  const proto =
    el.tagName.toLowerCase() === "textarea"
      ? el.ownerDocument.defaultView?.HTMLTextAreaElement?.prototype
      : el.ownerDocument.defaultView?.HTMLInputElement?.prototype;

  const setter = proto ? Object.getOwnPropertyDescriptor(proto, "value")?.set : undefined;

  if (setter) {
    setter.call(input, value);
  } else {
    input.value = value;
  }

  fire(el, "input");
  fire(el, "change");

  return readValue(el) === value;
}

/** Select an option by its `value`. Returns false if the option vanished
 * between planning and filling, which happens on forms that repopulate a
 * dependent dropdown. */
export function setSelectValue(el: Element, optionValue: string): boolean {
  const view = el.ownerDocument.defaultView;
  if (!view || !(el instanceof view.HTMLSelectElement)) return false;

  const match = Array.from(el.options).find((o) => o.value === optionValue);
  if (!match) return false;

  const setter = Object.getOwnPropertyDescriptor(view.HTMLSelectElement.prototype, "value")?.set;
  if (setter) setter.call(el, optionValue);
  else el.value = optionValue;

  fire(el, "input");
  fire(el, "change");

  return el.value === optionValue;
}

/**
 * Tick the radio in `group` whose value matches. Uses `safeClick`, so a form
 * that disguises its submit button as a radio still cannot be triggered.
 */
export function setRadioValue(group: Element, optionValue: string): boolean {
  const doc = group.ownerDocument;
  const view = doc.defaultView;
  if (!view) return false;

  const radios = Array.from(group.querySelectorAll('input[type="radio"]'));
  const pool = radios.length > 0 ? radios : radiosByName(group);

  const match = pool.find((r) => r.getAttribute("value") === optionValue);
  if (!match) return false;

  safeClick(match);

  // Some forms manage checked state themselves and ignore the click, so set it
  // directly as well and report what actually stuck.
  if (match instanceof view.HTMLInputElement && !match.checked) {
    match.checked = true;
    fire(match, "input");
    fire(match, "change");
  }

  return match instanceof view.HTMLInputElement ? match.checked : false;
}

/** Set a checkbox to a specific state. No-op when it is already there, so we
 * never toggle a box the user set. */
export function setCheckboxValue(el: Element, checked: boolean): boolean {
  const view = el.ownerDocument.defaultView;
  if (!view || !(el instanceof view.HTMLInputElement)) return false;
  if (el.checked === checked) return true;

  safeClick(el);
  if (el.checked !== checked) {
    el.checked = checked;
    fire(el, "input");
    fire(el, "change");
  }
  return el.checked === checked;
}

/**
 * Fill a button-plus-listbox dropdown of the kind Workday and Ashby build.
 *
 * The options do not exist until the trigger is clicked, so matching happens
 * here rather than during planning. The sequence is: open, wait for the listbox
 * to render, look for an exact match, click it or back out. Backing out matters
 * as much as picking: leaving a popup open over the form, or picking the
 * nearest-looking country, are both worse than a visible blank.
 *
 * Returns the option label on success, or null if nothing matched.
 */
export async function openAndSelect(
  trigger: Element,
  wanted: string,
  matcher: (options: readonly { value: string; label: string }[], wanted: string) =>
    | { value: string; label: string }
    | null,
): Promise<string | null> {
  if (isSubmitControl(trigger)) throw new SubmitRefusedError(describe(trigger));

  (trigger as HTMLElement).click();

  const listbox = await waitForListbox(trigger);
  if (!listbox) {
    closePopup(trigger);
    return null;
  }

  const optionEls = Array.from(listbox.querySelectorAll('[role="option"], li'));
  const options = optionEls.map((el) => ({
    value: el.getAttribute("data-value") ?? el.getAttribute("value") ?? (el.textContent ?? "").trim(),
    label: (el.textContent ?? "").trim(),
  }));

  const match = matcher(options, wanted);
  if (!match) {
    closePopup(trigger);
    return null;
  }

  const index = options.findIndex((o) => o.value === match.value && o.label === match.label);
  const target = index >= 0 ? optionEls[index] : undefined;
  if (!target) {
    closePopup(trigger);
    return null;
  }

  safeClick(target);
  return match.label;
}

/** Poll briefly for the listbox this trigger controls. Workday renders it into
 * a portal at the end of body, so `aria-controls` is more reliable than looking
 * inside the trigger's own subtree. */
async function waitForListbox(trigger: Element): Promise<Element | null> {
  const doc = trigger.ownerDocument;
  const controls = trigger.getAttribute("aria-controls");

  for (let attempt = 0; attempt < 20; attempt += 1) {
    const byControls = controls ? doc.getElementById(controls) : null;
    if (byControls && byControls.querySelector('[role="option"], li')) return byControls;

    const open = doc.querySelector('[role="listbox"]:not([hidden]), [role="menu"]:not([hidden])');
    if (open && open.querySelector('[role="option"], li')) return open;

    await delay(25);
  }
  return null;
}

/** Escape closes every popup implementation we care about, and unlike clicking
 * elsewhere it cannot land on another control. */
function closePopup(trigger: Element): void {
  const view = trigger.ownerDocument.defaultView;
  if (!view) return;
  trigger.dispatchEvent(
    new view.KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }),
  );
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** Current value of a control, for "is this already filled" and "did the write
 * stick" checks. */
export function readValue(el: Element): string {
  const view = el.ownerDocument.defaultView;
  if (!view) return "";

  if (el instanceof view.HTMLInputElement) {
    if (el.type === "checkbox" || el.type === "radio") return el.checked ? el.value : "";
    if (el.type === "file") return el.files && el.files.length > 0 ? "file" : "";
    return el.value;
  }
  if (el instanceof view.HTMLTextAreaElement) return el.value;
  if (el instanceof view.HTMLSelectElement) return el.value;

  if (el.getAttribute("contenteditable") === "true") return el.textContent ?? "";

  // A popup dropdown trigger shows its current selection as its own text. The
  // unselected state is a prompt, not a value, so treat the usual prompts as
  // empty or every Workday dropdown reads as already filled and gets skipped.
  const popup = (el.getAttribute("aria-haspopup") ?? "").toLowerCase();
  const role = (el.getAttribute("role") ?? "").toLowerCase();
  if (popup === "listbox" || popup === "menu" || role === "combobox" || role === "listbox") {
    const text = (el.textContent ?? "").replace(/\s+/g, " ").trim();
    return PLACEHOLDER_TEXT.test(text) ? "" : text;
  }

  // Custom widget: a checked radio somewhere inside is the group's value.
  const checked = el.querySelector('input[type="radio"]:checked, input[type="checkbox"]:checked');
  if (checked) return checked.getAttribute("value") ?? "on";

  return "";
}

function radiosByName(group: Element): Element[] {
  const name = group.getAttribute("name");
  if (!name) return [];
  return Array.from(
    group.ownerDocument.querySelectorAll(`input[type="radio"][name="${name.replace(/"/g, '\\"')}"]`),
  );
}

/** Bubbling, non-cancelable, exactly like a real edit. */
function fire(el: Element, type: "input" | "change"): void {
  const view = el.ownerDocument.defaultView;
  if (!view) return;
  el.dispatchEvent(new view.Event(type, { bubbles: true, cancelable: false }));
}

function describe(el: Element): string {
  const label = el.getAttribute("aria-label") ?? (el.textContent ?? "").trim().slice(0, 40);
  return `<${el.tagName.toLowerCase()}> ${label}`;
}
