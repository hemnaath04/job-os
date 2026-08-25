/**
 * Whether tailoring a resume for an application should also link that
 * resume's CONTAINER to it, and the rules for when it should not.
 *
 * application-documents.tsx and the Resumes page both read
 * `spawned_from_application_id` off the resume container (not the version)
 * as the "this resume belongs to this one company" marker -- see
 * create_resume's MCP description and the Resume type's own docstring.
 * Nothing in the tailor flow ever set it: the version a tailor run writes
 * only carries `spawned_from_job_id`, and the container is a separate row
 * entirely. That gap is the bug this exists to fix.
 *
 * "One container per application" is a convention job.os's tools describe
 * to callers, not a database constraint (create_resume never checked it,
 * see apps/api/tests/test_resume_create_spawned_from_application.py), so
 * this only ever FILLS IN a missing link. It never reassigns one that
 * already points somewhere, for three reasons:
 *  - a master resume is the one general-purpose data identity every tailor
 *    run reads from; linking it to one company's application would make the
 *    very next run for a different company fight over the same field.
 *  - a resume already linked to a different application already belongs to
 *    that one. Taking it for this call would misattribute an existing
 *    container rather than fix a missing link.
 *  - an application some other container already claims keeps that
 *    container. Linking a second one to the same application would break
 *    the one-per-application shape every reader of this field assumes.
 *
 * Deliberately does not go looking for "some other, unlinked container that
 * looks like it belongs to this application" (by company/role name, say):
 * nothing else in this codebase resolves that relationship by name, so
 * guessing at one here would be inventing a match rule no other code
 * follows or could later rely on. The resume actually being tailored is
 * already, presumably, the per-company container in use for this
 * application -- that is the caller's own signal, not a guess.
 */
export function shouldLinkResumeToApplication(
  resumes: {
    id: string;
    is_master: boolean;
    spawned_from_application_id?: string | null;
  }[],
  resumeId: string,
  applicationId: string,
): boolean {
  const resume = resumes.find((candidate) => candidate.id === resumeId);
  if (!resume || resume.is_master) return false;
  if (resume.spawned_from_application_id) {
    // Already linked, correctly (idempotent, nothing to do) or to a
    // different application (not this call's to change) -- either way, no.
    return false;
  }
  const applicationAlreadyOwned = resumes.some(
    (candidate) =>
      candidate.id !== resumeId && candidate.spawned_from_application_id === applicationId,
  );
  return !applicationAlreadyOwned;
}
