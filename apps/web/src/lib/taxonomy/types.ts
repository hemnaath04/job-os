/**
 * Job title taxonomy: shared types.
 *
 * Three levels, family -> group -> leaf. A leaf is the unit a job seeker
 * actually filters on ("Backend Engineer"), which is deliberately finer than an
 * O*NET occupation: 15-1252.00 Software Developers alone covers Backend,
 * Frontend, Full Stack, Mobile, DevOps, SRE, Embedded and Architect work, and a
 * CS/AI job platform that cannot tell those apart cannot rank anything.
 *
 * Crosswalk direction matters. A leaf points at one or more SOC codes; a SOC
 * code fans out to many leaves. The SOC side is there for provenance and for
 * pulling O*NET's alternate titles in as free alias data, not for retrieval.
 *
 * Contains modified O*NET data. See apps/web/data/onet/NOTICE.md for the full
 * attribution required by CC BY 4.0.
 */

/** O*NET-SOC code, for example `15-1252.00`. */
export type SocCode = string;

export interface TaxonomyFamily {
  id: string;
  name: string;
  /** Ordered ids of this family's groups. */
  groups: string[];
}

export interface TaxonomyGroup {
  id: string;
  name: string;
  familyId: string;
  /** Ordered ids of this group's leaves. */
  leaves: string[];
}

export interface TaxonomyLeaf {
  id: string;
  name: string;
  groupId: string;
  familyId: string;
  /**
   * O*NET-SOC codes this leaf maps onto, most representative first. Several
   * leaves legitimately share a code.
   */
  soc: SocCode[];
  /**
   * One line on where this leaf's boundary sits, kept in the artifact because
   * every boundary here is a judgment call somebody will want to argue with.
   */
  note: string;
  /**
   * Tie-break weight for the pattern matcher. Higher wins. Roughly: generic
   * roles 10, specialised individual-contributor roles 30 to 50, management and
   * product 70 to 90, because "Engineering Manager, Machine Learning" is a
   * management job before it is an ML job.
   */
  specificity: number;
}

export interface Taxonomy {
  /** Bumped by hand when the leaf layer changes shape. */
  version: string;
  /** O*NET release the aliases were derived from. */
  onetVersion: string;
  generatedAt: string;
  families: TaxonomyFamily[];
  groups: TaxonomyGroup[];
  leaves: TaxonomyLeaf[];
}

/** Where an alias came from, which is what its confidence is based on. */
export type AliasSource = "curated" | "onet";

export interface TaxonomyAncestry {
  leaf: TaxonomyLeaf;
  group: TaxonomyGroup;
  family: TaxonomyFamily;
}

/**
 * Seniority is deliberately not a taxonomy node. "Senior Backend Engineer" is
 * the Backend Engineer leaf plus a seniority of `senior`, so that one filter
 * does not multiply the other.
 */
export type Seniority =
  | "intern"
  | "new_grad"
  | "entry"
  | "junior"
  | "mid"
  | "senior"
  | "staff"
  | "principal"
  | "distinguished"
  | "lead"
  | "manager"
  | "director"
  | "vp"
  | "executive";

export interface TitleDecoration {
  /** Seniority band read off the raw title, null when the title says nothing. */
  seniority: Seniority | null;
  /** The literal marker that produced `seniority`, for example `sr.` or `ii`. */
  seniorityMarker: string | null;
  /** Explicit numeric or roman level, for example `II`, `L5`, `E4`. */
  level: string | null;
  /** True for intern, co-op, apprentice and new-grad postings. */
  isEarlyCareer: boolean;
}

export interface NormalizedTitle extends TitleDecoration {
  leafId: string;
  /**
   * 0 to 1, and monotone by method: an exact alias never scores below 0.75, a
   * pattern guess never above 0.74. 1.0 is a hand-curated alias matching the
   * title verbatim, dropping 0.04 for each layer of decoration that had to come
   * off first; O*NET-derived aliases start at 0.9. Callers that cannot tolerate
   * a wrong bucket should require >= 0.75, which means "an exact alias matched".
   */
  confidence: number;
  /** How the leaf was reached, useful when auditing a bad normalization. */
  method: "curated-alias" | "onet-alias" | "pattern";
  /** The alias key or pattern-carrying leaf id that matched. */
  matchedOn: string;
  /** The cleaned string the match was made against. */
  normalizedInput: string;
}
