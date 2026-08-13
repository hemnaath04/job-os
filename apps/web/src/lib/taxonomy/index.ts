/**
 * Job title taxonomy: the lookup surface.
 *
 * Pure functions over generated data. No network, no database, no I/O, safe in
 * a server component, a route handler, a worker or a test.
 *
 * The two things callers usually want:
 *
 *   normalizeTitle("Sr. Backend Engineer II")
 *     -> { leafId: "backend-engineer", confidence: 1, seniority: "senior",
 *          level: "II", ... }
 *
 *   getAncestry("backend-engineer")
 *     -> { leaf, group: "Application Engineering", family: "Software Engineering" }
 *
 * Seniority and early-career status come back as separate fields, never folded
 * into the leaf. That is the whole design: one axis should not multiply the
 * other.
 *
 * Contains modified O*NET data. See apps/web/data/onet/NOTICE.md.
 */

import { CURATED_ALIASES, ONET_ALIASES } from "./generated/aliases.ts";
import { TAXONOMY } from "./generated/taxonomy.ts";
import { bestMatch } from "./match.ts";
import type { PatternCandidate } from "./match.ts";
import {
  candidateForms,
  extractDecoration,
  isDeniedOccupation,
} from "./normalize.ts";
import { allLeafSpecs } from "./spec.ts";
import type {
  NormalizedTitle,
  SocCode,
  Taxonomy,
  TaxonomyAncestry,
  TaxonomyFamily,
  TaxonomyGroup,
  TaxonomyLeaf,
  TitleDecoration,
} from "./types.ts";

export type {
  NormalizedTitle,
  Seniority,
  Taxonomy,
  TaxonomyAncestry,
  TaxonomyFamily,
  TaxonomyGroup,
  TaxonomyLeaf,
  TitleDecoration,
} from "./types.ts";

const LEAF_BY_ID = new Map<string, TaxonomyLeaf>(TAXONOMY.leaves.map((l) => [l.id, l]));
const GROUP_BY_ID = new Map<string, TaxonomyGroup>(TAXONOMY.groups.map((g) => [g.id, g]));
const FAMILY_BY_ID = new Map<string, TaxonomyFamily>(TAXONOMY.families.map((f) => [f.id, f]));

const LEAVES_BY_SOC = ((): Map<SocCode, TaxonomyLeaf[]> => {
  const m = new Map<SocCode, TaxonomyLeaf[]>();
  for (const leaf of TAXONOMY.leaves) {
    for (const soc of leaf.soc) m.set(soc, [...(m.get(soc) ?? []), leaf]);
  }
  return m;
})();

const PATTERN_CANDIDATES: PatternCandidate[] = allLeafSpecs().map(({ leaf }) => ({
  leafId: leaf.id,
  specificity: leaf.specificity,
  rules: leaf.match,
}));

// ---------------------------------------------------------------------------
// Reading the taxonomy
// ---------------------------------------------------------------------------

/** The whole taxonomy, families and groups in display order. */
export function listTaxonomy(): Taxonomy {
  return TAXONOMY;
}

export function listFamilies(): readonly TaxonomyFamily[] {
  return TAXONOMY.families;
}

export function listGroups(): readonly TaxonomyGroup[] {
  return TAXONOMY.groups;
}

export function listLeaves(): readonly TaxonomyLeaf[] {
  return TAXONOMY.leaves;
}

export function getLeaf(id: string): TaxonomyLeaf | null {
  return LEAF_BY_ID.get(id) ?? null;
}

export function getGroup(id: string): TaxonomyGroup | null {
  return GROUP_BY_ID.get(id) ?? null;
}

export function getFamily(id: string): TaxonomyFamily | null {
  return FAMILY_BY_ID.get(id) ?? null;
}

/** Leaf plus its group and family, or null if the id is unknown. */
export function getAncestry(leafId: string): TaxonomyAncestry | null {
  const leaf = LEAF_BY_ID.get(leafId);
  if (leaf === undefined) return null;
  const group = GROUP_BY_ID.get(leaf.groupId);
  const family = FAMILY_BY_ID.get(leaf.familyId);
  if (group === undefined || family === undefined) return null;
  return { leaf, group, family };
}

/** Every leaf crosswalked to an O*NET-SOC code. Usually more than one. */
export function leavesForSoc(soc: SocCode): readonly TaxonomyLeaf[] {
  return LEAVES_BY_SOC.get(soc) ?? [];
}

/** Leaves in one group, in declaration order. */
export function leavesInGroup(groupId: string): readonly TaxonomyLeaf[] {
  const group = GROUP_BY_ID.get(groupId);
  if (group === undefined) return [];
  return group.leaves.map((id) => LEAF_BY_ID.get(id)).filter((l): l is TaxonomyLeaf => l !== undefined);
}

/** Leaves anywhere under one family, in declaration order. */
export function leavesInFamily(familyId: string): readonly TaxonomyLeaf[] {
  const family = FAMILY_BY_ID.get(familyId);
  if (family === undefined) return [];
  return family.groups.flatMap((gid) => leavesInGroup(gid));
}

// ---------------------------------------------------------------------------
// Normalizing a raw title
// ---------------------------------------------------------------------------

/**
 * Confidence floors, chosen so that an exact alias always outranks a pattern
 * guess no matter how much decoration had to be stripped to reach it.
 */
const CURATED_BASE = 1;
const CURATED_FLOOR = 0.8;
const ONET_BASE = 0.9;
const ONET_FLOOR = 0.75;
const REDUCTION_PENALTY = 0.04;
const PATTERN_BASE = 0.65;

/**
 * How much an exact string match is worth against a pattern guess, expressed in
 * the same units as leaf specificity.
 *
 * This is the number that decides the two hard cases. "Software Engineer -
 * Backend" has the generic leaf as its head alias (specificity 10) and the
 * Backend rule firing on the whole string (32), so the pattern wins.
 * "Embedded Software Engineer, Robotics" has a specific head alias (44) against
 * the Robotics rule firing on the domain word in the tail (50), and the eight
 * points tip it back to Embedded, which is the right read: the head of a title
 * is the role and the tail is usually the team.
 */
const ALIAS_BONUS = 8;

function round(n: number): number {
  return Math.round(n * 1000) / 1000;
}

/**
 * Seniority, level and early-career status read off a raw title, with no
 * opinion about the role. Useful on its own when the leaf is already known.
 */
export function describeSeniority(raw: string): TitleDecoration {
  const forms = candidateForms(raw);
  return extractDecoration(forms.primary[0] ?? "");
}

/**
 * Map a messy real-world title onto one leaf, or return null.
 *
 * Every reading of the title is collected first, then the most specific one
 * wins, with an exact string match worth a small bonus over a pattern guess:
 *
 *  - Exact alias lookup over the views that lose nothing about the role: the
 *    whole string, the string without parentheticals, and both with seniority
 *    stripped.
 *  - The pattern rules on the whole string, and again without parentheticals,
 *    since a parenthetical can inject a word that trips a rule's exclusion list
 *    without saying anything about the role.
 *  - Exact alias lookup on the head of the title, cut at the first separator.
 *    Worth the least, because the discipline often lives in the tail.
 *
 * Two things can veto the whole thing. The occupation deny list, so that
 * "Registered Nurse" and "Golf Course Attendant" come back null rather than
 * being forced into the nearest tech-sounding leaf; and a tie between two
 * equally specific leaves, because "Data Engineer / Data Scientist" really is
 * two jobs and a coin flip is worse than an honest miss.
 *
 * Precision is the priority throughout: a wrong bucket poisons ranking,
 * tailoring and analytics at once, while a null just means fall back to the raw
 * string.
 */
export function normalizeTitle(raw: string): NormalizedTitle | null {
  if (typeof raw !== "string" || raw.trim().length === 0) return null;

  const forms = candidateForms(raw);
  const primary = forms.primary[0];
  if (primary === undefined) return null;
  const decoration = extractDecoration(primary);

  interface Reading extends Omit<NormalizedTitle, keyof TitleDecoration> {
    rank: number;
  }
  const readings: Reading[] = [];

  const considerAlias = (form: string, index: number): void => {
    const curated = CURATED_ALIASES[form];
    if (curated !== undefined) {
      const leaf = LEAF_BY_ID.get(curated);
      if (leaf !== undefined) {
        readings.push({
          leafId: leaf.id,
          confidence: round(Math.max(CURATED_BASE - REDUCTION_PENALTY * index, CURATED_FLOOR)),
          method: "curated-alias",
          matchedOn: form,
          normalizedInput: primary,
          rank: leaf.specificity + ALIAS_BONUS,
        });
      }
      return;
    }
    const onet = ONET_ALIASES[form];
    if (onet === undefined) return;
    const leaf = LEAF_BY_ID.get(onet);
    if (leaf === undefined) return;
    readings.push({
      leafId: leaf.id,
      confidence: round(Math.max(ONET_BASE - REDUCTION_PENALTY * index, ONET_FLOOR)),
      method: "onet-alias",
      matchedOn: form,
      normalizedInput: primary,
      rank: leaf.specificity + ALIAS_BONUS,
    });
  };

  forms.primary.forEach(considerAlias);

  if (!isDeniedOccupation(primary)) {
    for (const form of forms.primary.slice(0, 2)) {
      if (isDeniedOccupation(form)) continue;
      const hit = bestMatch(PATTERN_CANDIDATES, form);
      if (hit === null) continue;
      readings.push({
        leafId: hit.leafId,
        confidence: round(PATTERN_BASE + hit.specificity / 1000),
        method: "pattern",
        matchedOn: hit.leafId,
        normalizedInput: primary,
        rank: hit.specificity,
      });
    }
    forms.reduced.forEach((form, i) => considerAlias(form, forms.primary.length + i));
  }

  let best: Reading | null = null;
  for (const reading of readings) {
    if (
      best === null ||
      reading.rank > best.rank ||
      (reading.rank === best.rank && reading.confidence > best.confidence)
    ) {
      best = reading;
    }
  }
  if (best === null) return null;

  const { rank: _rank, ...chosen } = best;
  return { ...decoration, ...chosen };
}
