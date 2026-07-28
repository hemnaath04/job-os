// Custom job-feed sources the user hosts themselves.
//
// A custom source is an HTTPS endpoint the user builds or controls. job.os
// POSTs the search filters to it and renders whatever JSON it returns; it never
// contacts the underlying job sites. That is the whole point of the split, so
// the legal acceptance recorded here gates the feature: no timestamp, no
// fetching.
//
// Client-safe by construction, same as ./keys: a component can import it. The
// fetcher that consumes these definitions lives in ./custom-fetch and stays
// server-side.

export interface CustomSource {
  id: string;
  name: string;
  url: string;
  /** Optional header name the endpoint expects, e.g. "x-custom-source-key". */
  authHeader?: string;
  authValue?: string;
  enabled: boolean;
}

const STORAGE_KEY = "job-os:custom-sources";
/** ISO timestamp of the acceptance, or absent when the terms were never taken. */
const ACCEPTED_KEY = "job-os:custom-sources-accepted";

/** Dispatched on every mutation so an open Job Finder re-reads without a reload. */
export const CUSTOM_SOURCES_CHANGED_EVENT = "custom-sources-changed";

export function loadCustomSources(): CustomSource[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(isCustomSource).map((s) => ({ ...s, enabled: s.enabled === true }));
  } catch {
    return [];
  }
}

/**
 * Create or update one source and return the new list. An absent `id` means a
 * create, and a create starts disabled: connecting a feed and searching with it
 * are two separate decisions.
 */
export function upsertCustomSource(
  input: Omit<CustomSource, "id" | "enabled"> & { id?: string; enabled?: boolean },
): CustomSource[] {
  const list = loadCustomSources();
  const id = input.id ?? newCustomId();
  const existing = list.find((s) => s.id === id);
  const next: CustomSource = {
    id,
    name: input.name.trim(),
    url: input.url.trim(),
    authHeader: input.authHeader?.trim() || undefined,
    authValue: input.authValue?.trim() || undefined,
    enabled: input.enabled ?? existing?.enabled ?? false,
  };
  const merged = existing
    ? list.map((s) => (s.id === id ? next : s))
    : [...list, next];
  persist(merged);
  return merged;
}

export function removeCustomSource(id: string): CustomSource[] {
  const next = loadCustomSources().filter((s) => s.id !== id);
  persist(next);
  return next;
}

export function setCustomEnabled(id: string, enabled: boolean): CustomSource[] {
  const next = loadCustomSources().map((s) =>
    s.id === id ? { ...s, enabled } : s,
  );
  persist(next);
  return next;
}

export function getAcceptedAt(): string | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = localStorage.getItem(ACCEPTED_KEY);
    return raw && raw.trim() ? raw : null;
  } catch {
    return null;
  }
}

export function hasAcceptedTerms(): boolean {
  return getAcceptedAt() !== null;
}

export function acceptTerms(): void {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(ACCEPTED_KEY, new Date().toISOString());
  } catch {
    /* private mode or quota: the acceptance holds for this page only */
  }
  window.dispatchEvent(new Event(CUSTOM_SOURCES_CHANGED_EVENT));
}

/**
 * Revoking clears the timestamp and switches every source off. Leaving them lit
 * would let a stale selection keep firing at the user's endpoints after they
 * withdrew the acceptance that allowed it.
 */
export function revokeTerms(): void {
  if (typeof window === "undefined") return;
  try {
    localStorage.removeItem(ACCEPTED_KEY);
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify(loadCustomSources().map((s) => ({ ...s, enabled: false }))),
    );
  } catch {
    /* nothing worth failing over: the gate below still reads as revoked */
  }
  window.dispatchEvent(new Event(CUSTOM_SOURCES_CHANGED_EVENT));
}

/**
 * Only https, and only a URL the browser can parse. The server re-checks this
 * and adds the internal-host rules: this one is here to catch a typo before it
 * becomes a failed search.
 */
export function isValidCustomUrl(url: string): boolean {
  try {
    return new URL(url.trim()).protocol === "https:";
  } catch {
    return false;
  }
}

function newCustomId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `cs-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

function isCustomSource(value: unknown): value is CustomSource {
  if (!value || typeof value !== "object") return false;
  const raw = value as Record<string, unknown>;
  return (
    typeof raw.id === "string" &&
    typeof raw.name === "string" &&
    typeof raw.url === "string"
  );
}

function persist(sources: CustomSource[]) {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(sources));
  } catch {
    /* private mode or quota: the list stays in memory for this page only */
  }
  window.dispatchEvent(new Event(CUSTOM_SOURCES_CHANGED_EVENT));
}
