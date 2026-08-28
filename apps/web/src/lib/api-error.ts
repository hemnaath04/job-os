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

/** The last named field in a validation error's `loc`, e.g. `["body","url"] -> "url"`. */
function fieldFromLoc(loc: unknown): string | null {
  if (!Array.isArray(loc)) return null;
  const named = loc.filter(
    (part): part is string =>
      typeof part === "string" && !["body", "query", "path", "header"].includes(part),
  );
  return named.length > 0 ? named[named.length - 1] : null;
}

/**
 * A FastAPI validation error list, as a sentence.
 *
 * Each entry carries a `msg` that Pydantic already wrote in English ("Field
 * required", "String should have at most 200 characters"), so the readable
 * version is that message against the field it is about. Capped at three,
 * because a body that fails ten ways is not ten things a person is going to
 * read off a toast.
 */
function fromValidationErrors(entries: unknown[]): string | null {
  const parts: string[] = [];
  for (const entry of entries) {
    if (typeof entry !== "object" || entry === null) continue;
    const { msg, loc } = entry as { msg?: unknown; loc?: unknown };
    if (typeof msg !== "string" || !msg.trim()) continue;
    const field = fieldFromLoc(loc);
    parts.push(field ? `${field}: ${msg}` : msg);
  }
  if (parts.length === 0) return null;
  const shown = parts.slice(0, 3).join("; ");
  const rest = parts.length - 3;
  return `The server rejected that request. ${shown}${rest > 0 ? ` (and ${rest} more)` : ""}.`;
}

/** The human sentence inside an object-shaped detail, if it carries one. */
function fromObjectDetail(value: Record<string, unknown>): string | null {
  for (const key of ["message", "detail", "error"]) {
    const candidate = value[key];
    if (typeof candidate === "string" && candidate.trim()) return candidate.trim();
  }
  return null;
}

/**
 * Turn a failed response's body into the detail text a person should read.
 *
 * FastAPI's HTTPException always shapes the body as `{"detail": ...}`. When
 * that detail is the plain string a handler wrote (e.g. "...use 'Paste the
 * description' instead."), that string IS the message: it was written for a
 * user to read, and JSON.stringify-ing the whole body around it used to bury
 * it inside a blob that nothing downstream ever unwrapped.
 *
 * The other two shapes this backend really returns are not strings, and used
 * to fall through to `JSON.stringify(parsed)` -- which meant the failure toast
 * printed raw JSON at the user. A 422 showed
 * `{"detail":[{"type":"missing","loc":["body","url"],...}]}`, and resumes.py's
 * finalize 409 (`HTTPException(409, {"message": ..., "review": {...}})`)
 * showed its whole review object, burying the one sentence written for a
 * person inside it. Both now yield that sentence: the validation entries carry
 * a Pydantic `msg` in English, and the finalize dict carries `message`.
 *
 * Anything left is a shape nobody wrote for a reader, so it gets the friendly,
 * status-specific sentence rather than its own markup or braces. That is also
 * what a non-JSON body gets: a platform's own error page (Heroku's
 * "Application Error" HTML for a crashed or asleep dyno, a CDN's 502/503/504
 * page) is not anything this app returned.
 */
export function detailFromErrorBody(status: number, bodyText: string): string {
  let parsed: unknown;
  try {
    parsed = JSON.parse(bodyText);
  } catch {
    return friendlyStatusText(status);
  }
  if (typeof parsed !== "object" || parsed === null) return friendlyStatusText(status);

  const detail = (parsed as { detail?: unknown }).detail;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    return fromValidationErrors(detail) ?? friendlyStatusText(status);
  }
  if (typeof detail === "object" && detail !== null && !Array.isArray(detail)) {
    return (
      fromObjectDetail(detail as Record<string, unknown>) ?? friendlyStatusText(status)
    );
  }
  // No `detail` at all: some proxies and route handlers answer with their own
  // `{"message": ...}` or `{"error": ...}` instead.
  return (
    fromObjectDetail(parsed as Record<string, unknown>) ?? friendlyStatusText(status)
  );
}
