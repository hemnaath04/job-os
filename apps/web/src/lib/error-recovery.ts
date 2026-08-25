import { ApiError } from "./api-error.ts";

/**
 * What to do about a failure, inferred from what the server said.
 *
 * Every one of these is a real recovery step rather than a restatement of the
 * problem. The fallback is deliberately "Try again", not "Something went
 * wrong", because the second one tells the reader nothing they cannot see.
 *
 * This is a fallback of last resort: `failureDescription` below only calls it
 * when there is no real, specific detail to show instead. A guess from a
 * status code or a keyword is never as useful as the sentence a backend
 * handler actually wrote for this exact failure, so this never gets a chance
 * to override one.
 */
export function recoveryFor(detail: string): string {
  const text = detail.toLowerCase();
  if (!navigator.onLine) return "You appear to be offline. Reconnect and try again.";
  if (/failed to fetch|network|econnrefused|err_internet/.test(text)) {
    return "Check your connection and try again.";
  }
  if (/\b401\b|\b403\b|unauthor|forbidden|jwt|token/.test(text)) {
    return "Your session may have expired. Reload the page and try again.";
  }
  if (/\b404\b|not found/.test(text)) {
    return "It may have been removed already. Reload the page to see the current state.";
  }
  if (/\b409\b|already exists|duplicate/.test(text)) {
    return "It already exists, so nothing was changed.";
  }
  if (/\b429\b|rate limit|too many/.test(text)) {
    return "The service is rate limiting requests. Wait a moment and try again.";
  }
  if (/\b5\d\d\b|timeout|timed out|unavailable|gateway/.test(text)) {
    return "The service did not respond. Try again in a moment.";
  }
  return "Try again.";
}

/**
 * The backend's own words for this failure, when it wrote a real one.
 *
 * `ApiError#detail` is a plain string exactly when a FastAPI handler raised
 * `HTTPException(status, "some sentence")` (see fetchJson/detailFromErrorBody
 * in api.ts and api-error.ts): a message written for a person to read, e.g.
 * jobs.py's 504 "...use 'Paste the description' instead" or its 502 "Could
 * not fetch that job posting right now...". That sentence already IS the
 * actionable advice, so there is nothing for recoveryFor to add. A
 * non-ApiError failure (a raw fetch/network error, an Appwrite SDK error, a
 * hand-thrown message with no useful text) has no such field and returns
 * null here, which is the signal to fall back to recoveryFor's guess.
 */
export function backendDetail(error: unknown): string | null {
  if (!(error instanceof ApiError)) return null;
  const text = error.detail.trim();
  return text.length > 0 ? text : null;
}

/**
 * What a failure toast's body should say, computed the same way regardless of
 * whether the caller is `reportFailure` (which then hands it to sonner) or a
 * test (which can assert on it directly, with no toast library involved).
 *
 * Leads with the backend's own detail text when there is one (see
 * backendDetail above): that sentence was written for exactly this failure,
 * so it belongs first, not buried in a parenthetical after a generic guess
 * nobody reads past. Only a failure with no such detail (a raw network
 * error, for instance) falls back to recoveryFor's pattern-matched advice,
 * same as before this distinction existed. An explicit `recovery` from the
 * caller always wins, same as before: it is a deliberate override, not a
 * guess.
 */
export function failureDescription(error: unknown, recovery?: string): string {
  const detail =
    error instanceof Error ? error.message : typeof error === "string" ? error : "";
  const specific = backendDetail(error);
  const advice = recovery ?? (specific ? null : recoveryFor(detail));
  if (!advice) return specific as string;
  return detail ? `${advice} (${detail})` : advice;
}
