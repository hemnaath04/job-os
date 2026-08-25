/**
 * A failed backend/proxy call. `status` lets a caller branch on the numeric
 * code directly (`error.status === 409`) instead of parsing it back out of
 * a string, which is how the old convention could quietly break just by a
 * message's wording changing. `message` still starts with "<status>: " for
 * any caller that has not moved to `.status`, so nothing that pattern-matched
 * on the old shape breaks by this class existing.
 *
 * A leaf module with no imports of its own, deliberately: api.ts's other
 * relative imports have no file extension (bundler-resolved), which Node's
 * native ESM loader cannot follow, so api.ts itself cannot run under
 * `node --test`. This file can, and its test lives right next to it, the
 * same way apps/web/src/lib/taxonomy keeps its Node-testable pieces apart
 * from anything that needs the bundler.
 */
export class ApiError extends Error {
  readonly status: number;
  /** The clean text after the "<status>: " prefix, e.g. an HTTPException's own detail. */
  readonly detail: string;

  constructor(status: number, detail: string) {
    super(`${status}: ${detail}`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

/** What a non-JSON error response almost always means, in a sentence a
 * person can act on rather than the HTML page that said it. */
export function friendlyStatusText(status: number): string {
  switch (status) {
    case 502:
    case 504:
      return "The server is temporarily unreachable. Try again in a moment.";
    case 503:
      return "The service is temporarily unavailable, possibly restarting after a deploy. Try again shortly.";
    default:
      return "Something went wrong on the server. Try again in a moment.";
  }
}

/**
 * Turn a failed response's body into the detail text a person should read.
 *
 * FastAPI's HTTPException always shapes the body as `{"detail": ...}`. When
 * that detail is the plain string a handler wrote (e.g. "...use 'Paste the
 * description' instead."), that string IS the message: it was written for a
 * user to read, and JSON.stringify-ing the whole body around it used to bury
 * it inside a blob that nothing downstream ever unwrapped. A validation
 * error's `detail` is a list of field objects, not a string (and
 * resumes.py's finalize endpoint raises `HTTPException(409, {"message":
 * ..., "review": {...}})`, a dict, for the same reason), so that case (and
 * any other shape) falls back to stringifying the whole parsed body rather
 * than crashing or showing "[object Object]". A non-JSON body is a
 * platform's own error page (Heroku's "Application Error" HTML for a
 * crashed/asleep dyno, a CDN's 502/503/504 page), not anything this app
 * returned, so it gets a friendly, status-specific sentence instead of raw
 * markup.
 */
export function detailFromErrorBody(status: number, bodyText: string): string {
  let parsed: unknown;
  try {
    parsed = JSON.parse(bodyText);
  } catch {
    return friendlyStatusText(status);
  }
  if (
    typeof parsed === "object" &&
    parsed !== null &&
    "detail" in parsed &&
    typeof (parsed as { detail?: unknown }).detail === "string"
  ) {
    return (parsed as { detail: string }).detail;
  }
  return JSON.stringify(parsed);
}
