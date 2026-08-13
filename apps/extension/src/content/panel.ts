/**
 * The review panel: safety invariant 3.
 *
 * Every field that was filled appears here with the fact it came from, and
 * every field that was left blank appears here with the reason. The list of
 * required fields that are still empty is first, styled loudest, and never
 * collapsed, because that is the failure the user would otherwise never see:
 * an application that submits as complete with core responses missing gets
 * auto-rejected, and nobody tells the applicant why.
 *
 * Built with DOM calls rather than innerHTML. The page's own content ends up in
 * these strings (field labels come from the employer's markup), so string
 * concatenation into HTML would be an injection route straight into our own UI.
 */
import { PANEL_STYLES } from "./panel-styles.ts";
import type { FillResult, PlannedFill, PlannedSkip, RequiredGap, SkipReason } from "../core/types.ts";

const HOST_ID = "job-os-autofill-panel";

/** Reason codes to the words shown above each group. */
const REASON_HEADING: Readonly<Record<SkipReason, string>> = {
  free_text_answer: "Free-text questions, never answered",
  unrecognized_question: "No matching profile field",
  no_verified_fact: "Nothing verified to say",
  eeo_not_opted_in: "Demographic questions, off by default",
  no_matching_option: "No exact option match",
  unsupported_control: "Needs you to do it",
  already_filled: "Already had a value",
  user_disabled: "You switched these off",
};

/** The order the groups read best in: the ones that say something about our
 * behaviour first, the mundane ones last. */
const REASON_ORDER: readonly SkipReason[] = [
  "free_text_answer",
  "no_verified_fact",
  "eeo_not_opted_in",
  "no_matching_option",
  "unrecognized_question",
  "unsupported_control",
  "user_disabled",
  "already_filled",
];

export function renderPanel(doc: Document, result: FillResult, adapterLabel: string): void {
  doc.getElementById(HOST_ID)?.remove();

  const host = doc.createElement("div");
  host.id = HOST_ID;
  const shadow = host.attachShadow({ mode: "open" });

  const style = doc.createElement("style");
  style.textContent = PANEL_STYLES;
  shadow.append(style);

  const panel = el(doc, "div", "panel");
  panel.setAttribute("role", "dialog");
  panel.setAttribute("aria-label", "job.os autofill review");

  panel.append(buildHeader(doc, result, adapterLabel, host));

  const body = el(doc, "div", "body");

  if (result.requiredGaps.length > 0) body.append(buildGaps(doc, result.requiredGaps));
  if (result.filled.length > 0) body.append(buildFilled(doc, result.filled));
  if (result.failed.length > 0) body.append(buildFailed(doc, result.failed));

  const skipSections = buildSkips(doc, result.skipped);
  for (const section of skipSections) body.append(section);

  if (result.filled.length === 0 && result.skipped.length === 0) {
    const empty = el(doc, "div", "empty");
    empty.textContent = "No application fields were found on this page.";
    body.append(empty);
  }

  panel.append(body);
  panel.append(buildFooter(doc));
  shadow.append(panel);

  doc.body.append(host);
}

function buildHeader(
  doc: Document,
  result: FillResult,
  adapterLabel: string,
  host: HTMLElement,
): HTMLElement {
  const header = doc.createElement("header");
  const title = el(doc, "div", "title");

  const h1 = doc.createElement("h1");
  h1.textContent = "Filled from your verified profile";
  title.append(h1);

  const subtitle = el(doc, "p", "subtitle");
  const detected = result.ats === "generic" ? "Unrecognised form" : adapterLabel;
  subtitle.textContent = `${detected}. ${count(result.filled.length, "field")} filled, ${result.skipped.length} left blank.`;
  title.append(subtitle);

  header.append(title);

  const close = doc.createElement("button");
  close.className = "close";
  close.type = "button";
  close.setAttribute("aria-label", "Close the review panel");
  close.textContent = "×";
  close.addEventListener("click", () => host.remove());
  header.append(close);

  return header;
}

/** The loud one. */
function buildGaps(doc: Document, gaps: readonly RequiredGap[]): HTMLElement {
  const section = el(doc, "section", "alarm");
  section.append(sectionHead(doc, "Required and still empty", gaps.length));

  const note = el(doc, "p", "note");
  note.textContent =
    "Fill these in yourself before you submit. Applications that arrive with required answers missing are usually rejected automatically, and you are not told why.";
  section.append(note);

  const list = doc.createElement("ul");
  list.className = "rows";
  for (const gap of gaps) {
    const li = doc.createElement("li");
    const label = el(doc, "span", "field-label");
    label.textContent = gap.rawLabel;
    li.append(label);
    list.append(li);
  }
  section.append(list);
  return section;
}

function buildFilled(doc: Document, fills: readonly PlannedFill[]): HTMLElement {
  const section = doc.createElement("section");
  section.append(sectionHead(doc, "Filled, with the fact behind each one", fills.length));

  const list = doc.createElement("ul");
  list.className = "rows";

  for (const fill of fills) {
    const li = doc.createElement("li");

    const label = el(doc, "span", "field-label");
    label.textContent = fill.field.rawLabel || fill.key;
    li.append(label);

    const value = el(doc, "span", "value");
    value.textContent = fill.option ? fill.option.label || fill.option.value : fill.sourced.value;
    li.append(value);

    const source = el(doc, "span", "source");
    const from = doc.createElement("b");
    from.textContent = fill.sourced.citation.factLabel;
    source.append("From ", from, ` (${fill.sourced.citation.kind}, ${fill.sourced.citation.attribute})`);
    li.append(source);

    list.append(li);
  }

  section.append(list);
  return section;
}

function buildFailed(doc: Document, failed: readonly PlannedFill[]): HTMLElement {
  const section = el(doc, "section", "alarm");
  section.append(sectionHead(doc, "Tried to fill but the page rejected it", failed.length));

  const note = el(doc, "p", "note");
  note.textContent = "These are still empty. Type them in yourself.";
  section.append(note);

  const list = doc.createElement("ul");
  list.className = "rows";
  for (const fill of failed) {
    const li = doc.createElement("li");
    const label = el(doc, "span", "field-label");
    label.textContent = fill.field.rawLabel || fill.key;
    li.append(label);
    list.append(li);
  }
  section.append(list);
  return section;
}

function buildSkips(doc: Document, skips: readonly PlannedSkip[]): HTMLElement[] {
  const grouped = new Map<SkipReason, PlannedSkip[]>();
  for (const skip of skips) {
    const bucket = grouped.get(skip.reason);
    if (bucket) bucket.push(skip);
    else grouped.set(skip.reason, [skip]);
  }

  const sections: HTMLElement[] = [];

  for (const reason of REASON_ORDER) {
    const bucket = grouped.get(reason);
    if (!bucket || bucket.length === 0) continue;

    const section = doc.createElement("section");
    section.append(sectionHead(doc, REASON_HEADING[reason], bucket.length));

    const list = doc.createElement("ul");
    list.className = "rows";

    for (const skip of bucket) {
      const li = doc.createElement("li");

      const label = el(doc, "span", "field-label");
      label.textContent = skip.field.rawLabel || skip.key || "(unlabelled field)";
      li.append(label);

      const detail = el(doc, "span", "reason");
      detail.textContent = skip.detail;
      li.append(detail);

      if (reason === "free_text_answer") {
        const tag = el(doc, "span", "tag essay");
        tag.textContent = "Left for you";
        li.append(tag);
      } else if (reason === "eeo_not_opted_in") {
        const tag = el(doc, "span", "tag blocked");
        tag.textContent = "Opt in per field";
        li.append(tag);
      }

      list.append(li);
    }

    section.append(list);
    sections.push(section);
  }

  return sections;
}

function buildFooter(doc: Document): HTMLElement {
  const footer = doc.createElement("footer");
  const pledge = el(doc, "span", "pledge");
  pledge.textContent = "This extension never submits.";
  footer.append(pledge, " Read everything above, then send the application yourself.");
  return footer;
}

function sectionHead(doc: Document, text: string, n: number): HTMLElement {
  const head = el(doc, "div", "section-head");
  head.append(text);
  const badge = el(doc, "span", "count");
  badge.textContent = String(n);
  head.append(badge);
  return head;
}

function el(doc: Document, tag: string, className: string): HTMLElement {
  const node = doc.createElement(tag);
  node.className = className;
  return node;
}

function count(n: number, noun: string): string {
  return `${n} ${noun}${n === 1 ? "" : "s"}`;
}
