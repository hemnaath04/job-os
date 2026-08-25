/**
 * Whether a stored tailor run may seed the Tailor page's pickers.
 *
 * The page restores two things on mount: a run still in flight, and the last
 * one that finished. Both existed so that navigating away and back did not
 * strand a run or leave the finished resume unreachable, and both seeded the
 * job picker from the stored run.
 *
 * That was wrong whenever the visit named a job. "Tailor a resume for this
 * role" on an application links to `/tailor?job_id=...`, and the restore then
 * overwrote it with whichever job was tailored last. Clicking the button on one
 * application showed another company's resume, and the next run went to that
 * other company too. A resume for the wrong employer is the kind of mistake
 * that leaves the app and reaches a real person, so an explicit request wins
 * over anything remembered.
 *
 * Skipping the restore strands nothing. A run in flight stays visible in the
 * global operations pill, which tracks every agent job app-wide, and the stored
 * pointers are left in localStorage for a later visit that arrives without a
 * job id.
 */
export function shouldRestoreTailor(
  urlJobId: string | null | undefined,
  stored: unknown,
): boolean {
  if (!stored) return false;
  // An explicit job in the URL is a deliberate request to tailor for THAT role.
  return !urlJobId;
}
