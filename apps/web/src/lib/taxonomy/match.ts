/**
 * The pattern layer: conjunctive regex rules that decide which leaf a title
 * belongs to when no exact alias matches.
 *
 * A rule is deliberately a conjunction rather than one big regex. Almost every
 * useful test is "a discipline word AND a role word", and writing that as two
 * small anchored regexes keeps each one readable and keeps a stray word from
 * silently widening the rule.
 *
 * The same evaluator runs at build time, where it decides which leaf claims an
 * O*NET alternate title, and at runtime as the fallback for unseen titles. That
 * shared path is the point: an alias baked in from O*NET and a live title reach
 * the same leaf by the same logic.
 */

export interface MatchRule {
  /** Every pattern must match for the rule to fire. */
  all: RegExp[];
  /** If any of these match, the rule does not fire. */
  none?: RegExp[];
}

export interface PatternCandidate {
  leafId: string;
  specificity: number;
  rules: MatchRule[];
}

export interface PatternHit {
  leafId: string;
  specificity: number;
}

function ruleMatches(rule: MatchRule, text: string): boolean {
  for (const p of rule.all) if (!p.test(text)) return false;
  if (rule.none) for (const p of rule.none) if (p.test(text)) return false;
  return true;
}

export function leafMatches(candidate: PatternCandidate, text: string): boolean {
  for (const rule of candidate.rules) if (ruleMatches(rule, text)) return true;
  return false;
}

/**
 * Best leaf for `text` among `candidates`, or null.
 *
 * A tie at the top specificity returns null rather than picking arbitrarily.
 * "Data Engineer / Data Scientist" really is two jobs in one posting, and a coin
 * flip between them is the wrong-answer-worse-than-none case.
 */
export function bestMatch(
  candidates: readonly PatternCandidate[],
  text: string,
): PatternHit | null {
  let best: PatternHit | null = null;
  let tied = false;
  for (const c of candidates) {
    if (!leafMatches(c, text)) continue;
    if (best === null || c.specificity > best.specificity) {
      best = { leafId: c.leafId, specificity: c.specificity };
      tied = false;
    } else if (c.specificity === best.specificity && c.leafId !== best.leafId) {
      tied = true;
    }
  }
  return tied ? null : best;
}

/** All leaves whose rules fire, for reporting build-time ambiguity. */
export function allMatches(
  candidates: readonly PatternCandidate[],
  text: string,
): PatternHit[] {
  const hits: PatternHit[] = [];
  for (const c of candidates) {
    if (leafMatches(c, text)) hits.push({ leafId: c.leafId, specificity: c.specificity });
  }
  return hits.sort((a, b) => b.specificity - a.specificity);
}
