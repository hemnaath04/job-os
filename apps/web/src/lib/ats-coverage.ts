/**
 * How a Keyword Match number should be READ, separated from how it is drawn.
 *
 * The tailor page showed the bare percentage on an absolute 75/50 colour scale
 * and one flat list of missing keywords. On a real ByteDance AI Platform run
 * that painted a red 27 next to an honesty review of 98 -- two grades on one
 * line, contradicting each other, with the red one implying the tailor had
 * failed when it had in fact covered everything the profile can evidence.
 *
 * The backend already sends what reconciles them (`_compute_ats_from_document`
 * and the block after it in services/tailor.py); it was simply never read. The
 * functions here are that reading, kept out of the component so they can be
 * tested without a DOM.
 */

/**
 * Whether the score is everything these facts could reach against this posting.
 *
 * Rounded on both sides because the ring renders a rounded number, and "27
 * against a ceiling of 26.7" must not read as falling short of itself.
 * `achievable` is absent on an older version row, in which case there is no
 * ceiling to compare against and the answer is no rather than a guess.
 */
export function isAtCoverageCeiling(
  score: number,
  achievable: number | undefined,
): boolean {
  if (typeof achievable !== "number" || !Number.isFinite(achievable)) return false;
  if (!Number.isFinite(score)) return false;
  return Math.round(score) >= Math.round(achievable);
}

/**
 * Split the misses into the ones the candidate can close and the ones they cannot.
 *
 * A single "Missing" list charged every unmatched requirement to the writing,
 * which is most of why a run looked like it had underperformed. `needsNewFacts`
 * is the backend's `missing_needs_new_facts`: requirements absent from every
 * verified fact and bullet, so no further pass can reach them. What is left is
 * wording or bullet selection, which another pass genuinely can.
 *
 * Order is preserved from `missing` so the panel reads in the posting's own
 * order rather than in set order.
 */
export function partitionMissing(
  missing: string[],
  needsNewFacts: string[],
): { notInProfile: string[]; reachable: string[] } {
  const unreachable = new Set(needsNewFacts);
  return {
    notInProfile: missing.filter((term) => unreachable.has(term)),
    reachable: missing.filter((term) => !unreachable.has(term)),
  };
}
