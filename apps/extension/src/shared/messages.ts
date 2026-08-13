/**
 * The message contract between the popup, the service worker and the content
 * script.
 *
 * Kept in one file because the content script runs inside a page the extension
 * does not control, so every message crossing that boundary has to be treated
 * as untrusted input and validated. A page can post anything it likes at a
 * content script's listener.
 */
import type { EeoConsent, FieldKey } from "../core/types.ts";

export type Message =
  /** popup -> worker: is there a usable session, and does the profile have facts? */
  | { type: "status" }
  /** popup -> worker: fill the active tab. */
  | { type: "fill"; tabId: number }
  /** worker -> content: here is the verified profile, plan and fill. */
  | { type: "run"; profile: unknown; consent: EeoConsent; disabledKeys: FieldKey[] }
  /** content -> worker: this is what happened, for the badge. */
  | { type: "report"; filled: number; skipped: number; gaps: number };

export interface StatusReply {
  readonly signedIn: boolean;
  readonly appOrigin: string;
  readonly factCount: number;
  readonly draftsDropped: number;
  readonly error: string | null;
}

export interface FillReply {
  readonly ok: boolean;
  readonly error: string | null;
}

/** Narrow an unknown postMessage payload to a Message. Anything unrecognised is
 * dropped rather than coerced. */
export function parseMessage(raw: unknown): Message | null {
  if (typeof raw !== "object" || raw === null) return null;
  const msg = raw as Record<string, unknown>;

  switch (msg.type) {
    case "status":
      return { type: "status" };
    case "fill":
      return typeof msg.tabId === "number" ? { type: "fill", tabId: msg.tabId } : null;
    case "run":
      return {
        type: "run",
        profile: msg.profile,
        consent: isRecord(msg.consent) ? (msg.consent as EeoConsent) : {},
        disabledKeys: Array.isArray(msg.disabledKeys)
          ? (msg.disabledKeys.filter((k) => typeof k === "string") as FieldKey[])
          : [],
      };
    case "report":
      return {
        type: "report",
        filled: num(msg.filled),
        skipped: num(msg.skipped),
        gaps: num(msg.gaps),
      };
    default:
      return null;
  }
}

function num(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
