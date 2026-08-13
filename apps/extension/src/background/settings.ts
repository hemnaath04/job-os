/**
 * Settings, and only settings.
 *
 * What goes in `chrome.storage.local`: the app origin and the per-field
 * demographic opt-ins. What never goes in: any profile value. The opt-ins are
 * booleans about whether the user wants a question answered, not the answers
 * themselves, so losing this store to a compromised machine reveals a
 * preference and nothing about the person.
 */
import { DEFAULT_APP_ORIGIN } from "./session.ts";
import { EEO_FIELD_KEYS, type EeoConsent, type ExtensionSettings, type FieldKey } from "../core/types.ts";

const KEY = "settings";

export const DEFAULT_SETTINGS: ExtensionSettings = Object.freeze({
  appOrigin: DEFAULT_APP_ORIGIN,
  // Every demographic field starts off. Opting in is a per-field act.
  eeoConsent: Object.freeze({}),
});

export async function loadSettings(): Promise<ExtensionSettings> {
  const stored = await chrome.storage.local.get(KEY);
  const raw = stored[KEY];
  if (typeof raw !== "object" || raw === null) return DEFAULT_SETTINGS;

  const record = raw as Record<string, unknown>;
  return {
    appOrigin: typeof record.appOrigin === "string" && record.appOrigin.startsWith("https://")
      ? record.appOrigin
      : DEFAULT_APP_ORIGIN,
    eeoConsent: sanitizeConsent(record.eeoConsent),
  };
}

export async function saveConsent(consent: EeoConsent): Promise<void> {
  const current = await loadSettings();
  await chrome.storage.local.set({
    [KEY]: { ...current, eeoConsent: sanitizeConsent(consent) },
  });
}

/**
 * Only demographic keys, only literal `true`.
 *
 * A stray key here would be a way to smuggle consent for something that is not
 * a demographic question, and a truthy-but-not-true value would be a way to
 * turn a bug into an opt-in. Neither survives this.
 */
function sanitizeConsent(raw: unknown): EeoConsent {
  if (typeof raw !== "object" || raw === null) return {};
  const out: EeoConsent = {};
  for (const [key, value] of Object.entries(raw as Record<string, unknown>)) {
    if (!EEO_FIELD_KEYS.has(key as FieldKey)) continue;
    if (value === true) out[key as FieldKey] = true;
  }
  return out;
}
