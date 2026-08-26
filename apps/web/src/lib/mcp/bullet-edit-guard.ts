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
  // Room to improve a short bullet, no room to pad a long one. A bullet may end
  // up no longer than it already was, or no longer than the cap, whichever is
  // the more generous, so trimming and rewording are unrestricted and growth
  // stops where the resume stops being able to print it.
  const ceiling = Math.max(wordCount(current), BULLET_MAX_WORDS);
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
