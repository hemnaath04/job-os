/**
 * What a polled agent job means, in the words the user reads.
 *
 * Split out of operations-store.ts so the decision can be tested. That module
 * imports the Appwrite client and React, so Node's own test runner cannot load
 * it; this one imports nothing, the same reason api-error.ts is a leaf.
 *
 * The rule this file exists to hold: a run that is slow, that timed out, or
 * that the agent failed is NEVER described as something the user did. A real
 * tailoring run sat on "Finding the real gaps" at around 20% (a single model
 * call that can genuinely take minutes), the person pressed the only control
 * they had, and the activity card then read "You canceled this run." Two
 * things were wrong with that at once:
 *
 *  * It was not true. Nothing here can abort a server run: the agent keeps
 *    going, and that one went on to finish. Leaving the page is not canceling.
 *  * It threw the result away. Marking the card failed stopped the poll, so
 *    the finished resume the agent saved was never surfaced.
 *
 * So there is no "canceled" outcome in this file at all. Leaving a page detaches
 * that page and nothing else; the poll below is what decides an outcome, and it
 * only ever reports what the server actually said.
 */

/** The agent job's own status vocabulary, as the workspace client returns it. */
export type AgentJobStatusLike = "queued" | "running" | "succeeded" | "failed";

/** What the poll decided to do with a tracked operation this tick. */
export type OperationOutcome =
  | { kind: "running" }
  | { kind: "done" }
  | { kind: "failed"; message: string };

/**
 * How long a run is tracked before the browser stops believing in it.
 *
 * Generous on purpose: these run up to about five minutes, and the agent
 * function itself is killed at 900s, after which the agent's own reaper marks
 * the row failed. Giving up sooner than the server does is how a slow run
 * became a "failed" one in the UI while it was still working.
 */
export const MAX_AGE_MS = 25 * 60 * 1_000;

/**
 * A job that never leaves "queued" past this never reached the runtime (a bad
 * dispatch, or a crash on boot). Long enough to cover a cold agent, short of
 * the full max age so a dead run is not shown as running for 25 minutes.
 */
export const QUEUED_GRACE_MS = 5 * 60 * 1_000;

/** Every failure sentence this module can produce, named so tests can pin them. */
export const OPERATION_FAILURE = {
  /** The browser waited longer than any real run takes. */
  timedOut: "This run took longer than expected and stopped being tracked. Try again.",
  /** The agent reported success but returned nothing usable. */
  emptyResult: "It finished without a result. Try again.",
  /** The agent reported failure and gave no reason of its own. */
  agentFailed: "The agent failed. Try again.",
  /** Dispatched, never picked up. */
  neverStarted: "The agent never started this run. Try again.",
  /** The job row is gone, so there is nothing left to wait for. */
  missing: "That run no longer exists. Start a new one.",
} as const;

/**
 * Read one poll of an agent job.
 *
 * `ageMs` is how long the browser has been tracking this run, which is the only
 * input not on the job row itself. A non-finite age means the stored start time
 * was unreadable, which is treated as too old rather than as zero: a record we
 * cannot date is a record we cannot keep claiming is running.
 */
export function readAgentJob(
  job: { status: AgentJobStatusLike; output?: unknown; error?: string | null },
  ageMs: number,
): OperationOutcome {
  if (!Number.isFinite(ageMs) || ageMs > MAX_AGE_MS) {
    return { kind: "failed", message: OPERATION_FAILURE.timedOut };
  }
  if (job.status === "succeeded") {
    return job.output
      ? { kind: "done" }
      : { kind: "failed", message: OPERATION_FAILURE.emptyResult };
  }
  if (job.status === "failed") {
    // The agent writes its own reason onto the row (`_error_text` in the agent
    // function guarantees a non-empty one), and that reason is more use than
    // anything this file could invent. It is still the agent's failure, never
    // the user's.
    return {
      kind: "failed",
      message: job.error?.trim() || OPERATION_FAILURE.agentFailed,
    };
  }
  if (job.status === "queued" && ageMs > QUEUED_GRACE_MS) {
    return { kind: "failed", message: OPERATION_FAILURE.neverStarted };
  }
  return { kind: "running" };
}

/**
 * Whether a polling error is worth giving up over.
 *
 * A missing job row cannot be recovered. Anything else is a transient blip and
 * is retried on the next tick, up to `MAX_AGE_MS`, because a dropped request
 * during a five minute run is normal and dropping the run for it is not.
 */
export function isUnrecoverablePollError(message: string): boolean {
  return /404|not found|could not be found/i.test(message);
}
