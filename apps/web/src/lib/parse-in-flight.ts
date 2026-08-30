/**
 * Whether a saved job is still being read, and therefore worth asking again.
 *
 * Saving a URL answers immediately and does the reading in a background task:
 * Heroku abandons a request at 30s and a fetch-plus-parse takes longer, so the
 * route cannot wait. The row lands with `parse_pending`, a placeholder title,
 * and fills itself in a few seconds later.
 *
 * That half works. The half that did not is that nothing ever asked again.
 * There is no `refetchInterval` anywhere in this app, so once "Still reading
 * this posting" rendered it stayed on screen until the page was reloaded,
 * however long ago the parse had actually finished. Reported exactly that way,
 * from a card whose row was already complete in the database while the screen
 * still described it as untitled and unread.
 *
 * A feature that works while the interface says it does not is worse than a
 * slow one, because there is no way to tell "still going" from "broken".
 */
import type { Application } from "@/lib/types";

/**
 * How long a parse is given before polling gives up on it.
 *
 * Not a guess at how long a parse takes: it is the point past which asking
 * again is pointless. The deferred parse runs in the web process, so a dyno
 * restart mid-parse leaves a row at `parse_pending` with nothing coming for it
 * ever, and the server-side reaper only runs when the same user dispatches
 * their next job. Polling such a row forever would spend a request every few
 * seconds, on every open tab, for a result that cannot arrive.
 *
 * Three minutes is well past a healthy parse, which is measured in seconds,
 * and well short of a nuisance. After it, the card keeps its "Read it again"
 * button, which is the honest thing to offer a row nobody is filling in.
 */
export const PARSE_POLL_CEILING_MS = 3 * 60 * 1_000;

/** How often to ask, while anything on screen is still being read. */
export const PARSE_POLL_INTERVAL_MS = 4_000;

/** The little of an application this reading needs. */
export type ParseCheckable = {
  created_at?: string | null;
  job?: { jd_parsed?: { parse_pending?: boolean } | null } | null;
};

/**
 * Whether this row is mid-parse AND recent enough that a result may still land.
 *
 * Both halves matter. Without the first, a settled board polls forever; without
 * the second, a row stranded by a restart does the same.
 */
export function isParseInFlight(
  application: ParseCheckable,
  now: number = Date.now(),
): boolean {
  if (!application.job?.jd_parsed?.parse_pending) return false;
  if (!application.created_at) return false;
  const started = Date.parse(application.created_at);
  // An unparseable timestamp is not evidence that anything is still running.
  if (Number.isNaN(started)) return false;
  return now - started < PARSE_POLL_CEILING_MS;
}

/**
 * The poll interval for a list, or `false` to stop.
 *
 * Shaped for React Query's `refetchInterval`, which takes exactly this: a
 * number to keep asking, `false` to stop. Returning `false` the moment nothing
 * is in flight is what keeps a settled board completely silent, which is the
 * normal case and should cost nothing.
 */
export function parsePollInterval(
  applications: readonly ParseCheckable[] | undefined,
  now: number = Date.now(),
): number | false {
  if (!applications?.length) return false;
  return applications.some((application) => isParseInFlight(application, now))
    ? PARSE_POLL_INTERVAL_MS
    : false;
}
