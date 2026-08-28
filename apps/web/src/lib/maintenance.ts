import { get } from "@vercel/edge-config";

/** Set by visiting `/api/maintenance-bypass?token=<MAINTENANCE_BYPASS_SECRET>`. */
export const MAINTENANCE_BYPASS_COOKIE = "job-os-maintenance-bypass";

/**
 * Whether the manual full-site maintenance splash is on right now.
 *
 * Backed by Edge Config rather than an env var on purpose: an env var change
 * needs a new deployment to take effect, which defeats the point of a switch
 * meant to be flipped in the middle of an incident or a risky deploy. Edge
 * Config reads propagate globally in milliseconds with no redeploy.
 *
 * Fails open (returns `false`) on any read error -- a broken toggle should
 * never be the thing that takes the whole site down.
 */
export async function isMaintenanceModeOn(): Promise<boolean> {
  try {
    return (await get<boolean>("maintenanceMode")) ?? false;
  } catch {
    return false;
  }
}

/**
 * Whether this request carries a valid maintenance bypass.
 *
 * The secret has to be checked for existence, not just compared against. The
 * middleware previously compared the cookie to `process.env.MAINTENANCE_BYPASS_SECRET`
 * with a bare `===`, so on a deployment where that variable is unset both sides
 * are `undefined` -- and `undefined === undefined` is `true`. Every visitor read
 * as bypassed, with no cookie at all, and the splash silently never appeared for
 * anyone. That is the wrong direction to fail for a switch whose whole purpose is
 * to be flipped mid-incident: you would turn it on and see nothing happen.
 *
 * `/api/maintenance-bypass` already refuses to ISSUE the cookie when the secret
 * is missing (`if (!secret || token !== secret)`). This is that same guard on the
 * side that CHECKS it, which is where it was missing.
 */
export function hasMaintenanceBypass(
  cookieValue: string | undefined,
  secret: string | undefined,
): boolean {
  if (!secret || !cookieValue) return false;
  return cookieValue === secret;
}
