/**
 * Write to the durable store, then mirror to the display store.
 *
 * The pipeline keeps applications in two places: Postgres, reached through the
 * FastAPI, which is the record; and Appwrite, which the board reads. Only one
 * of those is allowed to fail quietly.
 *
 * This exists because `patchApplication` and `archiveApplication` used to write
 * Appwrite ALONE while `createApplication` wrote both. Creates were durable and
 * every edit after them was not, so a status dragged across the board, a note,
 * or a follow-up date lived only in the display copy. Measured on the live
 * database once an exhausted read quota locked Appwrite out: 39 of 68
 * applications had never been updated in Postgres since insert, and
 * `next_action_at` was null on all 68. The rows survived; roughly three weeks
 * of pipeline movement did not.
 *
 * Ordering is the entire content of the fix. The durable write goes first and
 * its result is what callers get back; the mirror is attempted afterwards and
 * its failure is logged, not raised. A failed mirror means the board shows a
 * stale card until reconciliation, which is a display lag. A failed durable
 * write means the edit did not happen, which the caller must see.
 *
 * As a side effect this also removes an outage coupling: before, an Appwrite
 * error made the whole edit throw, so during the quota lockout the board could
 * not be edited at all.
 */
export async function writeDurablyThenMirror<T>(
  durable: () => Promise<T>,
  mirror: (result: T) => Promise<unknown>,
  label: string,
): Promise<T> {
  const result = await durable();
  try {
    await mirror(result);
  } catch (error) {
    console.error("[pipeline-dual-write] mirror failed", { label, error });
  }
  return result;
}
