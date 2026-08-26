/**
 * The two vault defects a tailored resume inherits, measured where they can be fixed.
 *
 * The tailor already finds these. It reports `too_long_verbatim` and
 * `repeated_opening_verb_verbatim` for a bullet it printed exactly as the vault
 * holds it, and since #46 it no longer charges the writer for them, on the
 * grounds that they are the candidate's to fix and no rule can fix them:
 * shortening a claim means deciding which part of it to drop.
 *
 * Nothing rendered those flags. They were computed on every run and read by no
 * one, so the person who could act on them never saw them, and the same four
 * bullets came back flagged on every posting.
 *
 * Measured here rather than fetched, because the profile page reads the
 * Appwrite workspace while the API reads Postgres, so an endpoint would score a
 * different copy of the vault than the one on screen. The rules below are plain
 * text measures and they mirror `apps/api/src/job_os/services/resume_writing.py`,
 * which stays the source of truth: it is what actually scores the resume.
 */
import type { FactBullet, ProfileFact } from "./types";

/** One idea per bullet, one or two rendered lines. Mirrors BULLET_MAX_WORDS. */
export const BULLET_MAX_WORDS = 30;

/**
 * Across a whole page one repeated verb is normal English, so the page-wide
 * check is a share rather than a count. Mirrors PAGE_OPENER_SHARE and
 * MIN_PAGE_OPENER_REPEATS.
 */
export const PAGE_OPENER_SHARE = 1 / 3;
export const MIN_PAGE_OPENER_REPEATS = 3;

export function countWords(text: string): number {
  return text.trim().split(/\s+/).filter(Boolean).length;
}

/** The first word, which on a resume bullet is the verb doing the work. */
export function openingVerb(text: string): string {
  const match = /[A-Za-z][A-Za-z'-]*/.exec(text);
  return match ? match[0].toLowerCase() : "";
}

export interface BulletIssue {
  kind: "too_long" | "repeated_opener";
  detail: string;
}

export interface VaultQuality {
  /** Issues by bullet id. A bullet with none is absent rather than empty. */
  byBullet: Map<string, BulletIssue[]>;
  overCap: number;
  totalBullets: number;
  /** The verb opening more of the vault than a third of it, if there is one. */
  dominantOpener: { verb: string; count: number } | null;
}

function bulletsOf(facts: ProfileFact[]): FactBullet[] {
  return facts.flatMap((fact) => fact.bullets ?? []);
}

/**
 * What the tailor will inherit from this vault, and from which bullet.
 *
 * Repetition is judged inside one fact, because that is where the resume prints
 * them together and where swapping a verb is a local edit. The page-wide count
 * is reported separately: it is a fact about the profile as a whole and it is
 * not any single bullet's fault.
 */
export function vaultQuality(facts: ProfileFact[]): VaultQuality {
  const byBullet = new Map<string, BulletIssue[]>();
  const add = (id: string, issue: BulletIssue) => {
    byBullet.set(id, [...(byBullet.get(id) ?? []), issue]);
  };

  for (const fact of facts) {
    const bullets = fact.bullets ?? [];
    const openers = bullets.map((bullet) => openingVerb(bullet.text));
    for (const [index, bullet] of bullets.entries()) {
      const words = countWords(bullet.text);
      if (words > BULLET_MAX_WORDS) {
        add(bullet.id, {
          kind: "too_long",
          detail: `${words} of ${BULLET_MAX_WORDS} words`,
        });
      }
      const verb = openers[index];
      if (verb && openers.filter((other) => other === verb).length > 1) {
        add(bullet.id, {
          kind: "repeated_opener",
          detail: `opens the same as another bullet here`,
        });
      }
    }
  }

  const all = bulletsOf(facts);
  const openers = all.map((bullet) => openingVerb(bullet.text)).filter(Boolean);
  let dominantOpener: VaultQuality["dominantOpener"] = null;
  for (const verb of new Set(openers)) {
    const count = openers.filter((other) => other === verb).length;
    if (count < MIN_PAGE_OPENER_REPEATS) continue;
    if (count <= openers.length * PAGE_OPENER_SHARE) continue;
    if (!dominantOpener || count > dominantOpener.count) {
      dominantOpener = { verb, count };
    }
  }

  return {
    byBullet,
    overCap: all.filter((bullet) => countWords(bullet.text) > BULLET_MAX_WORDS).length,
    totalBullets: all.length,
    dominantOpener,
  };
}

/**
 * The one-line version, or null when there is nothing worth saying.
 *
 * Written as a fact about the resume rather than as a scolding, because the
 * long bullet is often the right one: the point is that tailoring will print it
 * exactly as it stands, which is the part he could not have known.
 */
export function vaultQualitySummary(quality: VaultQuality): string | null {
  const parts: string[] = [];
  if (quality.overCap > 0) {
    parts.push(
      `${quality.overCap} of ${quality.totalBullets} bullets run past ${BULLET_MAX_WORDS} words`,
    );
  }
  if (quality.dominantOpener) {
    const { verb, count } = quality.dominantOpener;
    const shown = verb.charAt(0).toUpperCase() + verb.slice(1);
    parts.push(`"${shown}" opens ${count}`);
  }
  if (!parts.length) return null;
  return `${parts.join(", and ")}. Tailoring prints your wording as it stands, so this is where to change it.`;
}
