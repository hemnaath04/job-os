// Bring-your-own-key credentials for the keyed discovery sources.
//
// Nothing here ever reaches the server's environment: the user pastes a key on
// /jobs/keys, it lands in this browser's localStorage, and the Job Finder
// replays it in the body of each /api/discover call. The route hands it
// straight to the provider and forgets it.
//
// Client-safe by construction, so a component can import it. The fetchers that
// consume these keys live in ./keyed-sources and stay server-side.

import type { DiscoverySource } from "../types";

/** The sources whose credential the user supplies rather than the server. */
export type ByoSource = "jsearch" | "adzuna";

export interface DiscoveryKeys {
  jsearch?: string;
  adzuna_app_id?: string;
  adzuna_app_key?: string;
}

const STORAGE_KEY = "job-os:discovery-keys";

/** Dispatched on save/clear so an open Job Finder re-reads without a reload. */
export const KEYS_CHANGED_EVENT = "discovery-keys-changed";

export function isByoSource(source: DiscoverySource): source is ByoSource {
  return source === "jsearch" || source === "adzuna";
}

export function loadKeys(): DiscoveryKeys {
  if (typeof window === "undefined") return {};
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as unknown;
    if (!parsed || typeof parsed !== "object") return {};
    return parsed as DiscoveryKeys;
  } catch {
    return {};
  }
}

/** Merge `patch` over what is stored and return the result. */
export function saveKeys(patch: Partial<DiscoveryKeys>): DiscoveryKeys {
  const merged: DiscoveryKeys = { ...loadKeys(), ...patch };
  persist(merged);
  return merged;
}

export function clearProviderKey(source: ByoSource): DiscoveryKeys {
  const next = loadKeys();
  if (source === "jsearch") delete next.jsearch;
  else {
    delete next.adzuna_app_id;
    delete next.adzuna_app_key;
  }
  persist(next);
  return next;
}

/**
 * Whether a source can actually run. Adzuna needs both halves of its
 * credential, so a half-filled form must not count as connected. Anything that
 * is not a BYO source is always ready: its key, if any, lives on the server.
 */
export function hasKey(source: DiscoverySource, keys?: DiscoveryKeys): boolean {
  if (!isByoSource(source)) return true;
  const stored = keys ?? loadKeys();
  if (source === "jsearch") return Boolean(stored.jsearch?.trim());
  return Boolean(stored.adzuna_app_id?.trim() && stored.adzuna_app_key?.trim());
}

function persist(keys: DiscoveryKeys) {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(keys));
  } catch {
    /* private mode or quota: the keys stay in memory for this page only */
  }
  window.dispatchEvent(new Event(KEYS_CHANGED_EVENT));
}
