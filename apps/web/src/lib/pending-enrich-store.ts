/**
 * The state behind a description paste that has to outlive its panel.
 *
 * Split from `pending-enrich.ts` so it can be tested. That module reaches the
 * API client, the toast and the query cache through the `@/` alias, which the
 * test runner does not resolve; this one imports nothing, so the part with the
 * actual rules in it is reachable from a test.
 *
 * Everything is keyed by job id rather than held per panel, which is the whole
 * point: the answer to "is this saving" and "what did I type" belongs to the
 * job, not to whichever component happens to be on screen.
 */

const DRAFT_PREFIX = "enrich:draft:";

const running = new Set<string>();
const drafts = new Map<string, string>();
const listeners = new Set<() => void>();

/** One counter for the whole store. useSyncExternalStore reads it as the snapshot. */
let version = 0;

export function getVersion(): number {
  return version;
}

export function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function notify(): void {
  version += 1;
  for (const listener of listeners) listener();
}

export function readDraft(jobId: string): string {
  // Read-only on purpose: this runs during render, so it does not write the
  // stored value back into the map.
  const held = drafts.get(jobId);
  if (held !== undefined) return held;
  if (typeof window === "undefined") return "";
  return window.localStorage.getItem(DRAFT_PREFIX + jobId) ?? "";
}

export function setDraft(jobId: string, text: string): void {
  drafts.set(jobId, text);
  if (typeof window !== "undefined") {
    if (text) window.localStorage.setItem(DRAFT_PREFIX + jobId, text);
    else window.localStorage.removeItem(DRAFT_PREFIX + jobId);
  }
  notify();
}

export function clearDraft(jobId: string): void {
  setDraft(jobId, "");
}

export function isEnriching(jobId: string): boolean {
  return running.has(jobId);
}

/** Returns false when one is already in flight for this job, so callers can bail. */
export function markRunning(jobId: string): boolean {
  if (running.has(jobId)) return false;
  running.add(jobId);
  notify();
  return true;
}

export function markSettled(jobId: string): void {
  running.delete(jobId);
  notify();
}

/** Test seam. Nothing in the app calls this. */
export function __resetForTests(): void {
  running.clear();
  drafts.clear();
  listeners.clear();
  version = 0;
}
