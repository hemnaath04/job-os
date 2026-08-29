/**
 * The parts of the server's fit score a card cannot show any other way.
 *
 * A leaf with no imports beyond types, so Node's own test runner can load it.
 * It was inline in jobs/page.tsx first, which made it unreachable by a test,
 * and this file is the whole reason the behaviour below is pinned rather than
 * assumed.
 */
import type { FitNote } from "@/lib/discover/fit-score";
import type { IndexMatchScore } from "@/lib/types";

/**
 * The parts of the server's score a card cannot show any other way.
 *
 * `blockers` and `top_reasons` were computed, sent over the wire, and dropped
 * here. That is most of a masters student's answer thrown away: an
 * undergraduate-only internship scored lower and nothing said why, because the
 * only line that explains it lives on an axis this adapter did not carry.
 *
 * Skills lines are excluded on purpose. The card already prints matched and
 * missing skills underneath, and repeating one of them as prose would be the
 * same fact twice in two shapes.
 *
 * Blockers first, and never dropped: the scorer keeps them out of the number
 * deliberately, on the grounds that a candidate who needs sponsorship looking
 * at a posting that refuses to sponsor wants to be told rather than marked
 * down. Nothing was telling them.
 */
export function fitNotesFrom(match: IndexMatchScore): FitNote[] {
  const blocking = match.blockers.map((line) => ({
    reason: line.reason,
    detail: line.detail,
    blocking: true,
  }));
  const deductions = match.top_reasons
    .filter((line) => line.axis !== "skills" && line.points < 0)
    .map((line) => ({ reason: line.reason, detail: line.detail, blocking: false }));
  // A card has no room for a full breakdown, and the scorer already ranked
  // these by how much they moved the number.
  return [...blocking, ...deductions].slice(0, 3);
}
