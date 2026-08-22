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
