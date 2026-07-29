import { toast } from "sonner";

/**
 * What to do about a failure, inferred from what the server said.
 *
 * Every one of these is a real recovery step rather than a restatement of the
 * problem. The fallback is deliberately "Try again", not "Something went
 * wrong", because the second one tells the reader nothing they cannot see.
 */
function recoveryFor(detail: string): string {
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
 * Report a failed action as an instruction.
 *
 * The headline names what did not happen, so the reader knows the scope without
 * reading further, and the body says what to do. The server's own message is
 * kept last rather than dropped: it is what makes a bug report useful, it is
 * just not the first thing a person should have to parse.
 *
 * `action` completes "Couldn't ...", so pass a bare verb phrase: "save your
 * preferences", "archive that application".
 */
export function reportFailure(action: string, error: unknown, recovery?: string) {
  const detail =
    error instanceof Error ? error.message : typeof error === "string" ? error : "";
  const advice = recovery ?? recoveryFor(detail);
  toast.error(`Couldn't ${action}`, {
    description: detail ? `${advice} (${detail})` : advice,
  });
}
