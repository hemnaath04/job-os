/**
 * Popup logic: report session state, hold the demographic opt-ins, start a run.
 *
 * All the real work happens in the service worker and the content script. This
 * exists so the fill is always the result of a user gesture, which is both the
 * safety story and the reason `activeTab` is sufficient.
 */
import { loadSettings, saveConsent } from "../background/settings.ts";
import type { FillReply, StatusReply } from "../shared/messages.ts";
import { EEO_FIELD_KEYS, type EeoConsent, type FieldKey } from "../core/types.ts";

const statusEl = document.getElementById("status")!;
const fillButton = document.getElementById("fill") as HTMLButtonElement;

void init();

async function init(): Promise<void> {
  await restoreConsentToggles();
  await refreshStatus();

  fillButton.addEventListener("click", () => {
    void doFill();
  });
}

async function restoreConsentToggles(): Promise<void> {
  const settings = await loadSettings();

  for (const input of Array.from(
    document.querySelectorAll<HTMLInputElement>("input[type=checkbox][data-key]"),
  )) {
    const key = input.dataset.key as FieldKey | undefined;
    if (!key || !EEO_FIELD_KEYS.has(key)) continue;

    input.checked = settings.eeoConsent[key] === true;
    input.addEventListener("change", () => {
      void persistConsent();
    });
  }
}

async function persistConsent(): Promise<void> {
  const consent: EeoConsent = {};
  for (const input of Array.from(
    document.querySelectorAll<HTMLInputElement>("input[type=checkbox][data-key]"),
  )) {
    const key = input.dataset.key as FieldKey | undefined;
    if (key && EEO_FIELD_KEYS.has(key) && input.checked) consent[key] = true;
  }
  await saveConsent(consent);
}

async function refreshStatus(): Promise<void> {
  const reply = (await chrome.runtime.sendMessage({ type: "status" })) as StatusReply | undefined;

  if (!reply) {
    setStatus("bad", "Could not reach the extension's background worker.");
    return;
  }

  if (reply.error) {
    setStatus("bad", reply.error);
    return;
  }

  if (!reply.signedIn) {
    setStatus("bad", `Sign in at ${prettyOrigin(reply.appOrigin)} first, then reopen this.`);
    return;
  }

  const drafts =
    reply.draftsDropped > 0
      ? ` ${reply.draftsDropped} unconfirmed draft${reply.draftsDropped === 1 ? "" : "s"} will not be used.`
      : "";

  setStatus("good", `${reply.factCount} verified facts ready.${drafts}`);
  fillButton.disabled = false;
}

async function doFill(): Promise<void> {
  fillButton.disabled = true;
  setStatus("", "Reading the form...");

  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) {
    setStatus("bad", "No active tab.");
    fillButton.disabled = false;
    return;
  }

  const reply = (await chrome.runtime.sendMessage({
    type: "fill",
    tabId: tab.id,
  })) as FillReply | undefined;

  if (!reply?.ok) {
    setStatus("bad", reply?.error ?? "The fill did not start.");
    fillButton.disabled = false;
    return;
  }

  setStatus("good", "Filled. Check the review panel on the page.");
  window.close();
}

function setStatus(tone: "good" | "bad" | "", text: string): void {
  statusEl.className = tone;
  statusEl.textContent = text;
}

function prettyOrigin(origin: string): string {
  try {
    return new URL(origin).host;
  } catch {
    return origin;
  }
}
