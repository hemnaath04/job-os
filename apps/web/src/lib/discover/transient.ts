/**
 * Telling "this search is broken" from "something was briefly busy".
 *
 * A search fans out to the pre-built index and a hundred-odd live boards, so on
 * any given run something is restarting or slow. Those were rendered in the
 * same caution banner as a missing API key, which made a deploy look like a
 * failure: releasing the backend restarts the dyno the index rides on, and for
 * about a minute every search opened with a red "The index search failed: 503".
 *
 * Nothing was wrong. The search had already fallen back to live sources and
 * returned a full page of results. The only thing the banner achieved was
 * telling the user their tool was broken while it worked.
 */
import type { DiscoverySourceError } from "../types";

/** Long enough for a dyno to finish coming up, short enough not to feel hung. */
export const RETRY_DELAY_MS = 1200;

const TRANSIENT_PATTERNS = [
  // A backend that is restarting, overloaded, or behind a gateway that gave up.
  /\b50[234]\b/,
  /temporarily unavailable/i,
  /restarting/i,
  /bad gateway/i,
  /service unavailable/i,
  /gateway time-?out/i,
  // A board that did not answer inside its budget, or a connection that dropped.
  /timed out/i,
  /timeout/i,
  /econnreset/i,
  /socket hang ?up/i,
  /network(?: request)? failed/i,
  /failed to fetch/i,
];

/**
 * Would trying again in a second plausibly work?
 *
 * Deliberately narrow. A 401, a 404, a missing key and an out-of-credits
 * account are all things retrying cannot fix and the user genuinely needs to
 * see, so anything not listed above stays loud.
 */
export function isTransient(message: string): boolean {
  const text = String(message || "");
  return TRANSIENT_PATTERNS.some((pattern) => pattern.test(text));
}

/**
 * Run `attempt`, and if it fails in a way a retry could fix, run it once more.
 *
 * Once, not a backoff loop: the user is waiting on a page of results, and a
 * second failure a second later means the thing is actually down rather than
 * mid-restart. The caller still gets the error, so a real outage is still
 * reported, just not on the first flicker.
 */
export async function retryOnceIfTransient<T>(
  attempt: () => Promise<T>,
  delayMs: number = RETRY_DELAY_MS,
): Promise<T> {
  try {
    return await attempt();
  } catch (error) {
    if (!isTransient((error as Error)?.message ?? "")) throw error;
    await new Promise((resolve) => setTimeout(resolve, delayMs));
    return attempt();
  }
}

export interface PartitionedErrors {
  /** Something the user can act on: a key, a credit balance, a dead slug. */
  actionable: DiscoverySourceError[];
  /** Something that was briefly busy. Worth saying once, quietly. */
  transient: DiscoverySourceError[];
}

export function partitionErrors(
  errors: DiscoverySourceError[],
): PartitionedErrors {
  const actionable: DiscoverySourceError[] = [];
  const transient: DiscoverySourceError[] = [];
  for (const error of errors) {
    (isTransient(error.message) ? transient : actionable).push(error);
  }
  return { actionable, transient };
}

function listNames(names: string[]): string {
  if (names.length === 1) return names[0];
  if (names.length === 2) return `${names[0]} and ${names[1]}`;
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

/** Distinguishes a backend coming up from a query that ran out of time. */
const RESTARTING = /\b50[234]\b|restarting|bad gateway|service unavailable|temporarily unavailable/i;

/**
 * Why the index was not there, in its own words.
 *
 * "The saved index was restarting" was hardcoded, and for the deploy window it
 * was written for that was true. It stopped being true: the index began failing
 * most often because its own query ran out of time, and a banner that blames a
 * restart for that sends the reader to look at a deploy log where there is
 * nothing to find. Two causes, two sentences, and each says what actually
 * happened.
 *
 * The specific timeout that prompted this is gone with its store -- Appwrite
 * answered a slow fulltext search with a 408, and `job_postings` is back on
 * Postgres. The distinction is kept because the second sentence is still the
 * honest one for any timeout, whatever the query is running on, and because
 * guessing wrong about which of the two happened is the failure this exists to
 * avoid.
 */
function indexReason(message: string): string {
  if (RESTARTING.test(message)) {
    return "The saved index was restarting, so these results came from live sources only";
  }
  return "The saved index did not answer in time, so these results came from live sources only";
}

/**
 * One line for everything that was briefly busy, or null if nothing was.
 *
 * Grouped rather than one row per source, because five slow boards on one run
 * is one fact about the internet, not five problems. The index is named
 * separately since it is the one whose absence changes what the user is looking
 * at: the results came from live sources instead.
 */
export function transientNotice(errors: DiscoverySourceError[]): string | null {
  if (!errors.length) return null;
  const index = errors.find((error) => error.source === "index");
  const indexDown = Boolean(index);
  const boards = errors
    .filter((error) => error.source !== "index")
    .map((error) => error.source.replace(/^custom:/, ""))
    .sort();
  const parts: string[] = [];
  if (index) {
    parts.push(indexReason(index.message));
  }
  if (boards.length) {
    parts.push(
      `${listNames(boards)} ${boards.length === 1 ? "was" : "were"} slow to answer and ${boards.length === 1 ? "was" : "were"} skipped`,
    );
  }
  return `${parts.join(". ")}. Searching again usually picks ${
    indexDown && boards.length ? "them" : indexDown ? "it" : boards.length === 1 ? "it" : "them"
  } up.`;
}
