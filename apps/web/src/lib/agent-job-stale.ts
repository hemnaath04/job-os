/**
 * When an agent run has gone quiet long enough that nothing is coming.
 *
 * The Appwrite Function's hard ceiling on one execution is 900s. A job row is
 * written exactly once at the end of a normal run: on success, on a caught
 * exception, or on a caught transport error. So "running" long past that
 * ceiling almost always means the run reached none of them, because the
 * process itself died. Observed four times in one week, with Appwrite's own
 * execution record reading `failed`, 500, "general_unknown", no logs and no
 * traceback.
 *
 * A row like that stays "running" forever. Nothing is left to update it: the
 * function-side reaper only runs opportunistically, when the same user
 * dispatches their NEXT job, so a run nobody follows up on is never marked at
 * all.
 *
 * The browser already read that state correctly. The MCP connector did not,
 * and returned the raw snapshot: an agent polling `get_resume_tailor_status`
 * on a dead run was told "running" indefinitely, which is the one answer that
 * cannot be acted on. It happened during this session, twice, and had to be
 * diagnosed by reading Appwrite's execution log by hand.
 *
 * So the reading lives here, in a leaf with no imports, and both callers use
 * it. Two surfaces disagreeing about whether a run is alive is worse than
 * either answer on its own.
 */

/**
 * The function's 900s ceiling plus a buffer for the write round trip.
 *
 * Deliberately generous. Being early here would mark a slow but living run as
 * dead and send someone to re-run work that was about to land, which is a
 * worse failure than the wait it saves.
 */
export const AGENT_JOB_STALE_MS = 16 * 60 * 1_000;

export const AGENT_JOB_STALE_ERROR =
  "This run stopped responding partway through and never reported back. Try again.";

/** The little of an agent job this reading needs. */
export type StaleCheckable = {
  status?: string | null;
  error?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  progress?: { updated_at?: string | null } | null;
};

/**
 * Whether this run has been silent past the point anything could still arrive.
 *
 * Only ever true for a run that claims to be running. A queued job has not
 * started, and a finished one has already said what happened.
 */
export function isStaleAgentJob(job: StaleCheckable, now: number = Date.now()): boolean {
  if (job.status !== "running") return false;
  // The most recent sign of life, in the order the run would have produced
  // them. `created_at` is the floor: a run that died before its first progress
  // write has nothing else to date it by, and is exactly the case this exists
  // for.
  const lastSeen = job.progress?.updated_at ?? job.updated_at ?? job.created_at;
  if (!lastSeen) return false;
  const at = Date.parse(lastSeen);
  if (Number.isNaN(at)) return false;
  return now - at > AGENT_JOB_STALE_MS;
}

/**
 * The job as it should be reported: unchanged, or failed with a reason.
 *
 * Reported rather than written back. A read is not the place to mutate state
 * on someone else's behalf, and every caller already knows what to do with
 * status "failed". The row itself is corrected by the function-side reaper on
 * the owner's next dispatch.
 *
 * An existing error is preserved. If the run managed to say why it was in
 * trouble before it died, that sentence is better than this one.
 */
export function withStaleness<T extends StaleCheckable>(job: T, now: number = Date.now()): T {
  if (!isStaleAgentJob(job, now)) return job;
  return {
    ...job,
    status: "failed",
    error: job.error ?? AGENT_JOB_STALE_ERROR,
  };
}
