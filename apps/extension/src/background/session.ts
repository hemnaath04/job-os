/**
 * Getting the profile without holding a secret.
 *
 * The extension stores no API key, no refresh token and no long-lived
 * credential of any kind. It calls the app's own `/api/backend/*` proxy with
 * `credentials: "include"` and lets Chrome attach the Clerk session cookie the
 * user already has from signing in to the web app. The proxy exchanges that
 * cookie for a short-lived Clerk JWT server side and forwards it to the API, so
 * no bearer token is ever minted into a place the extension can read. If the
 * user is signed out the call returns 401 and the extension says so.
 *
 * The mechanism that makes this work is documented in Chrome's storage and
 * cookies guide: "Requests from an extension to a third-party are treated as
 * same-site if the extension has host permissions for the third-party." Clerk's
 * session cookie is SameSite=Lax and would otherwise be dropped on a
 * cross-origin request. The `https://jobs.hemnaath.tech/*` host permission in
 * the manifest is what buys that treatment, and it is the only reason that
 * entry exists.
 * https://developer.chrome.com/docs/extensions/develop/concepts/storage-and-cookies
 *
 * Note this is also why the fetch happens in the service worker rather than the
 * content script: a content script's requests are made on behalf of the page it
 * was injected into, so they carry the ATS's origin, not ours.
 * https://developer.chrome.com/docs/extensions/develop/concepts/network-requests
 */
import { EMPTY_PROFILE, parseVerifiedProfile, type VerifiedProfile } from "../core/profile.ts";
import { log, warn } from "../shared/redact.ts";

export const DEFAULT_APP_ORIGIN = "https://jobs.hemnaath.tech";

export class NotSignedInError extends Error {
  constructor() {
    super("not signed in to job.os");
    this.name = "NotSignedInError";
  }
}

/**
 * In-memory profile cache.
 *
 * Deliberately a module variable and not `chrome.storage`. An MV3 service
 * worker is torn down after roughly thirty seconds idle, which takes this with
 * it, so the profile's lifetime is bounded by the browser without any cleanup
 * code that could be forgotten. `chrome.storage.session` would survive longer
 * and `chrome.storage.local` would survive a reboot; neither is worth it for
 * data we can refetch in one request.
 */
let cache: { profile: VerifiedProfile; fetchedAt: number } | null = null;

/** Short enough that editing your profile in the app shows up on the next fill,
 * long enough that filling three forms in a row is one request. */
const CACHE_TTL_MS = 2 * 60 * 1000;

export function clearProfileCache(): void {
  cache = null;
}

/**
 * Fetch the verified profile.
 *
 * `verified=true` is passed as a query parameter so the server does the
 * filtering too, but `parseVerifiedProfile` still drops anything unverified on
 * arrival. Two gates for the same rule is the right amount when the failure
 * mode is putting an unconfirmed claim on someone's job application.
 */
export async function getVerifiedProfile(
  appOrigin: string,
  { force = false }: { force?: boolean } = {},
): Promise<VerifiedProfile> {
  const now = Date.now();
  if (!force && cache && now - cache.fetchedAt < CACHE_TTL_MS) {
    return cache.profile;
  }

  const url = `${appOrigin.replace(/\/+$/, "")}/api/backend/profile/facts?verified=true`;

  let response: Response;
  try {
    response = await fetch(url, {
      method: "GET",
      credentials: "include",
      headers: { accept: "application/json" },
      cache: "no-store",
    });
  } catch (error) {
    warn("session", "profile fetch failed", { error: (error as Error).name });
    throw new Error("Could not reach job.os. Check your connection and try again.");
  }

  if (response.status === 401 || response.status === 403) {
    clearProfileCache();
    throw new NotSignedInError();
  }

  if (!response.ok) {
    warn("session", "profile fetch returned an error status", { status: response.status });
    throw new Error(`job.os returned ${response.status} when asked for your profile.`);
  }

  const body: unknown = await response.json();
  const profile = parseVerifiedProfile(body);

  // Counts only. Never the facts themselves.
  log("session", "profile loaded", {
    facts: profile.facts.length,
    draftsDropped: profile.draftsDropped,
  });

  cache = { profile, fetchedAt: now };
  return profile;
}

/** A cheap signed-in check for the popup, reusing the same call. */
export async function probeSession(appOrigin: string): Promise<VerifiedProfile | null> {
  try {
    return await getVerifiedProfile(appOrigin);
  } catch (error) {
    if (error instanceof NotSignedInError) return null;
    throw error;
  }
}

export { EMPTY_PROFILE };
