/**
 * Content script entry point.
 *
 * Runs in the page only after the user clicks the toolbar button, because the
 * manifest declares no `content_scripts` and injection happens through
 * `chrome.scripting.executeScript` under the `activeTab` grant.
 *
 * Its job is small and fixed: detect the ATS, collect fields, plan, fill, show
 * the panel. It never fetches anything (a content script's requests carry the
 * page's origin, not ours) and it never decides what a value should be.
 */
import { collectAllFields, selectAdapter } from "../adapters/registry.ts";
import { applyFillPlan } from "../core/filler.ts";
import { buildFillPlan } from "../core/planner.ts";
import { buildProfileValues } from "../core/mapping.ts";
import { parseVerifiedProfile } from "../core/profile.ts";
import { renderPanel } from "./panel.ts";
import { parseMessage } from "../shared/messages.ts";
import { log, warn } from "../shared/redact.ts";
import type { FieldKey } from "../core/types.ts";

/** Guard against a double injection: `executeScript` runs the file again on a
 * second click, and two panels racing over the same form helps nobody. */
const FLAG = "__jobOsAutofillReady";

interface FlaggedWindow extends Window {
  __jobOsAutofillReady?: boolean;
}

const w = window as FlaggedWindow;

if (!w[FLAG]) {
  w[FLAG] = true;

  chrome.runtime.onMessage.addListener((raw, sender, sendResponse) => {
    // Only the extension's own service worker may drive this. A page can post
    // to a content script listener, and this is the check that stops it.
    if (sender.id !== chrome.runtime.id) return false;

    const message = parseMessage(raw);
    if (!message || message.type !== "run") return false;

    void run(message.profile, message.consent, message.disabledKeys)
      .then(() => sendResponse({ ok: true }))
      .catch((error: Error) => {
        warn("content", "run failed", { error: error.name });
        sendResponse({ ok: false, error: error.message });
      });

    return true;
  });
}

async function run(
  rawProfile: unknown,
  consent: Record<string, boolean | undefined>,
  disabledKeys: FieldKey[],
): Promise<void> {
  // Re-parse rather than trust. The worker filtered already, but this is the
  // boundary the safety invariant is stated at, so it is enforced here too.
  const profile = parseVerifiedProfile(rawProfile);

  const adapter = selectAdapter({ url: new URL(location.href), document });
  const fields = collectAllFields(adapter, document);

  log("content", "form detected", { ats: adapter.id, fields: fields.length });

  const values = buildProfileValues(profile, consent);

  const plan = buildFillPlan({
    adapter,
    fields,
    values,
    consent,
    disabledKeys: new Set(disabledKeys),
  });

  const result = await applyFillPlan(plan, fields);

  renderPanel(document, result, adapter.label);

  log("content", "fill complete", {
    ats: adapter.id,
    filled: result.filled.length,
    skipped: result.skipped.length,
    gaps: result.requiredGaps.length,
  });

  // Only counts cross back to the worker, never a value.
  void chrome.runtime.sendMessage({
    type: "report",
    filled: result.filled.length,
    skipped: result.skipped.length,
    gaps: result.requiredGaps.length,
  });
}
