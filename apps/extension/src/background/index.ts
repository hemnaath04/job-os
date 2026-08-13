/**
 * Service worker: the only part of the extension that talks to job.os.
 *
 * It holds the profile in memory, injects the content script when the user
 * asks, and hands the profile over for that one run. It does not decide what to
 * fill and it never touches a page.
 */
import { getVerifiedProfile, NotSignedInError } from "./session.ts";
import { loadSettings } from "./settings.ts";
import { parseMessage, type FillReply, type StatusReply } from "../shared/messages.ts";
import { log, warn } from "../shared/redact.ts";

chrome.runtime.onMessage.addListener((raw, _sender, sendResponse) => {
  const message = parseMessage(raw);
  if (!message) return false;

  switch (message.type) {
    case "status":
      void handleStatus().then(sendResponse);
      return true; // keep the channel open for the async reply

    case "fill":
      void handleFill(message.tabId).then(sendResponse);
      return true;

    case "report":
      // Badge the result so the user sees an outcome even if the panel is
      // scrolled off screen. Red whenever something needs them.
      void setBadge(message);
      return false;

    default:
      return false;
  }
});

async function handleStatus(): Promise<StatusReply> {
  const settings = await loadSettings();
  try {
    const profile = await getVerifiedProfile(settings.appOrigin);
    return {
      signedIn: true,
      appOrigin: settings.appOrigin,
      factCount: profile.facts.length,
      draftsDropped: profile.draftsDropped,
      error: null,
    };
  } catch (error) {
    if (error instanceof NotSignedInError) {
      return {
        signedIn: false,
        appOrigin: settings.appOrigin,
        factCount: 0,
        draftsDropped: 0,
        error: null,
      };
    }
    return {
      signedIn: false,
      appOrigin: settings.appOrigin,
      factCount: 0,
      draftsDropped: 0,
      error: (error as Error).message,
    };
  }
}

/**
 * Inject and run.
 *
 * The injection is what consumes the `activeTab` grant, which Chrome issues
 * because the user clicked the extension's own button. Before that click this
 * extension has no access to the page at all.
 */
async function handleFill(tabId: number): Promise<FillReply> {
  const settings = await loadSettings();

  let profile;
  try {
    profile = await getVerifiedProfile(settings.appOrigin, { force: true });
  } catch (error) {
    if (error instanceof NotSignedInError) {
      return { ok: false, error: `Sign in at ${settings.appOrigin} first, then try again.` };
    }
    return { ok: false, error: (error as Error).message };
  }

  if (profile.facts.length === 0) {
    return {
      ok: false,
      error:
        profile.draftsDropped > 0
          ? `Your profile has ${profile.draftsDropped} unconfirmed draft facts and no verified ones. Confirm them in job.os first.`
          : "Your job.os profile has no verified facts yet.",
    };
  }

  try {
    await chrome.scripting.executeScript({
      target: { tabId, allFrames: true },
      files: ["content.js"],
    });
  } catch (error) {
    warn("worker", "injection failed", { error: (error as Error).name });
    return {
      ok: false,
      error: "Chrome would not let the extension read this page. Reload it and click again.",
    };
  }

  // Plain objects only: the branded SourcedValue types are rebuilt on the other
  // side from the raw facts, so nothing that crosses this boundary is trusted
  // as already-sourced.
  const payload = {
    type: "run" as const,
    profile: profile.facts.map((f) => ({
      id: f.id,
      kind: f.kind,
      title: f.title,
      org: f.org,
      start_date: f.startDate,
      end_date: f.endDate,
      location: f.location,
      payload: f.payload,
      verified: true,
    })),
    consent: settings.eeoConsent,
    disabledKeys: [],
  };

  try {
    await chrome.tabs.sendMessage(tabId, payload);
  } catch (error) {
    warn("worker", "content script did not answer", { error: (error as Error).name });
    return { ok: false, error: "The page did not respond. Reload it and try again." };
  }

  log("worker", "fill dispatched", { facts: profile.facts.length });
  return { ok: true, error: null };
}

async function setBadge(report: { filled: number; gaps: number }): Promise<void> {
  const needsUser = report.gaps > 0;
  await chrome.action.setBadgeText({ text: needsUser ? String(report.gaps) : String(report.filled) });
  await chrome.action.setBadgeBackgroundColor({ color: needsUser ? "#C0555F" : "#FFE787" });
}
