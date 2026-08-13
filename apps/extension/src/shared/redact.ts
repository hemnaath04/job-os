/**
 * Logging that cannot leak the profile.
 *
 * The extension handles a home address, a phone number and an email on every
 * run. Console output in a content script is visible to the page, and anything
 * logged in the service worker survives in the extension's console for anyone
 * with the machine. So the rule is simple: log shapes and counts, never values.
 *
 * `redact` exists for the cases where some hint of the value genuinely helps
 * debugging, for example working out why an option did not match.
 */

/** Length and first character only. Enough to tell "empty" from "wrong", not
 * enough to reconstruct. */
export function redact(value: unknown): string {
  if (value === null || value === undefined) return "<none>";
  const text = String(value);
  if (text.length === 0) return "<empty>";
  if (text.length <= 2) return `<${text.length} chars>`;
  return `${text[0]}***<${text.length} chars>`;
}

/** Log a message with only non-identifying context. */
export function log(scope: string, message: string, context?: Record<string, number | string | boolean>): void {
  const safe = context ? sanitize(context) : undefined;
  if (safe) console.log(`[job-os-autofill:${scope}] ${message}`, safe);
  else console.log(`[job-os-autofill:${scope}] ${message}`);
}

export function warn(scope: string, message: string, context?: Record<string, number | string | boolean>): void {
  const safe = context ? sanitize(context) : undefined;
  if (safe) console.warn(`[job-os-autofill:${scope}] ${message}`, safe);
  else console.warn(`[job-os-autofill:${scope}] ${message}`);
}

/**
 * Strings in a log context are the risk, so they are redacted unless the key is
 * on the allowlist of things that are structurally safe: a field key, an
 * adapter id, a reason code. Numbers and booleans pass through, since a count
 * identifies nobody.
 */
const SAFE_STRING_KEYS = new Set([
  "ats",
  "adapter",
  "reason",
  "key",
  "kind",
  "status",
  "stage",
  "error",
]);

function sanitize(context: Record<string, number | string | boolean>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(context)) {
    if (typeof v === "string" && !SAFE_STRING_KEYS.has(k)) out[k] = redact(v);
    else out[k] = v;
  }
  return out;
}
