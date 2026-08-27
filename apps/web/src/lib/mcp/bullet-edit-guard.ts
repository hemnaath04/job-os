/**
 * What an agent editing the vault is allowed to change about a bullet.
 *
 * A bullet is the only prose on a tailored resume its owner actually wrote, and
 * everything downstream treats it as verified: `_sanitize_selected_bullets`
 * reverts a tailored rewrite that adds a number or a technology, precisely so
 * the page can only ever say what the vault already said.
 *
 * Handing an agent write access to that text puts the invariant back in play
 * from the other end. Trimming an over-long bullet and varying a repeated
 * opening verb are safe: they remove and rearrange. Adding a metric, or growing
 * a bullet to fit more job-description vocabulary into it, is the exact failure
 * the whole no-hallucination contract exists to prevent, and it would be worse
 * here than in the tailor, because a bad edit to the vault is permanent and
 * every future resume inherits it.
 *
 * So the same rule applies in both directions, and it is checked rather than
 * asked for.
 */

/** Mirrors BULLET_MAX_WORDS in apps/api resume_writing.py. */
export const BULLET_MAX_WORDS = 30;

/**
 * Room for a reword to land a word or two longer than what it replaced.
 *
 * Small on purpose. It exists so that shrinking a claim cannot be blocked by
 * the length rule, not to give an edit room to grow.
 */
export const REWORD_SLACK_WORDS = 2;

const NUMBER_RE = /\d+(?:[.,]\d+)*/g;

function numbersIn(text: string): Set<string> {
  return new Set((text.match(NUMBER_RE) ?? []).map((n) => n.replace(/,/g, "")));
}

function wordCount(text: string): number {
  return text.trim().split(/\s+/).filter(Boolean).length;
}

export interface BulletEditVerdict {
  ok: boolean;
  reason?: string;
}

/**
 * May this edit replace that bullet?
 *
 * `context` is everything the fact already says: its title, its payload, and
 * the bullet's own current wording. A number already in evidence may be moved
 * around freely; one that appears from nowhere may not.
 */
export function checkBulletEdit(
  current: string,
  next: string,
  context: string = "",
): BulletEditVerdict {
  if (!next.trim()) {
    return { ok: false, reason: "a bullet cannot be empty" };
  }
  const known = numbersIn(`${current} ${context}`);
  const invented = [...numbersIn(next)].filter((n) => !known.has(n));
  if (invented.length) {
    return {
      ok: false,
      reason:
        `this edit introduces ${invented.join(", ")}, which the fact does not ` +
        "already say. A metric has to be added by the person who can vouch for it",
    };
  }
  // Room to improve a short bullet, no room to pad a long one, and a couple of
  // words of slack so that rewording is never the thing that trips it.
  //
  // The slack is not a softening for its own sake. The first real use of this
  // tool was correcting "Owned and extended the Go test suite" to "Worked on
  // and extended", because he did not own it. That is a claim getting SMALLER,
  // it is the single safest edit this tool can make, and at 35 words against a
  // 35-word ceiling the length rule refused it: "Owned" is one word and
  // "Worked on" is two. The guard blocked an honesty fix and the edit had to go
  // around it, which is exactly the workaround a guard exists to prevent.
  //
  // Two words cannot carry a job description's worth of padding, and the checks
  // that stop fabrication are the number and technology rules above, not this
  // one. This rule is only here to stop a bullet growing into a paragraph.
  const ceiling = Math.max(wordCount(current), BULLET_MAX_WORDS) + REWORD_SLACK_WORDS;
  const words = wordCount(next);
  if (words > ceiling) {
    return {
      ok: false,
      reason:
        `this edit is ${words} words against a ceiling of ${ceiling}. Editing ` +
        "is for tightening a bullet and varying its wording, not for growing it",
    };
  }
  return { ok: true };
}
