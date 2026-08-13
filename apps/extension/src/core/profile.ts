/**
 * Turn the API's `/profile/facts` payload into the verified-only view the rest
 * of the extension is allowed to see.
 *
 * `verified === false` means the user has not confirmed the fact yet. The API
 * model calls those drafts and forbids them in generated resumes; the same rule
 * applies here, and it is applied once, at the boundary, so no downstream
 * module has to remember it. Anything dropped is counted and surfaced, because
 * "we had a draft answer and said nothing" is exactly the behaviour this
 * extension exists to avoid.
 */
import type { VerifiedFact } from "./types.ts";

/** Shape of `ProfileFactRead` from apps/api/src/job_os/schemas/profile.py. */
interface RawFact {
  id?: unknown;
  kind?: unknown;
  title?: unknown;
  org?: unknown;
  start_date?: unknown;
  end_date?: unknown;
  location?: unknown;
  payload?: unknown;
  verified?: unknown;
}

export interface VerifiedProfile {
  readonly facts: readonly VerifiedFact[];
  /** How many rows we refused because they were still drafts. Shown in the
   * panel so an empty field has a visible explanation. */
  readonly draftsDropped: number;
}

export const EMPTY_PROFILE: VerifiedProfile = Object.freeze({
  facts: Object.freeze([]) as readonly VerifiedFact[],
  draftsDropped: 0,
});

/**
 * Parse and filter. Strict on purpose: a row missing an id or a kind is
 * dropped rather than patched up, because a fact we cannot cite is a fact we
 * cannot fill from.
 */
export function parseVerifiedProfile(raw: unknown): VerifiedProfile {
  if (!Array.isArray(raw)) return EMPTY_PROFILE;

  const facts: VerifiedFact[] = [];
  let draftsDropped = 0;

  for (const item of raw as RawFact[]) {
    if (typeof item !== "object" || item === null) continue;

    // The gate. Anything other than a literal `true` is a draft.
    if (item.verified !== true) {
      draftsDropped += 1;
      continue;
    }

    const id = str(item.id);
    const kind = str(item.kind);
    const title = str(item.title);
    if (id === null || kind === null || title === null) continue;

    facts.push(
      Object.freeze({
        id,
        kind,
        title,
        org: str(item.org),
        startDate: str(item.start_date),
        endDate: str(item.end_date),
        location: str(item.location),
        payload: Object.freeze(
          typeof item.payload === "object" && item.payload !== null && !Array.isArray(item.payload)
            ? ({ ...item.payload } as Record<string, unknown>)
            : {},
        ),
      }),
    );
  }

  return Object.freeze({ facts: Object.freeze(facts), draftsDropped });
}

/** Facts of one kind, newest first where dates exist. The first `experience`
 * fact is the one a form means by "current employer". */
export function factsOfKind(profile: VerifiedProfile, kind: string): readonly VerifiedFact[] {
  return profile.facts
    .filter((f) => f.kind === kind)
    .slice()
    .sort(byRecencyDesc);
}

export function firstFactOfKind(profile: VerifiedProfile, kind: string): VerifiedFact | null {
  return factsOfKind(profile, kind)[0] ?? null;
}

/**
 * Ongoing first, then latest start date. A null `endDate` means the user has
 * not left, which is what "current" means on an application form.
 */
function byRecencyDesc(a: VerifiedFact, b: VerifiedFact): number {
  const aOngoing = a.endDate === null;
  const bOngoing = b.endDate === null;
  if (aOngoing !== bOngoing) return aOngoing ? -1 : 1;

  const aEnd = a.endDate ?? "";
  const bEnd = b.endDate ?? "";
  if (aEnd !== bEnd) return bEnd.localeCompare(aEnd);

  return (b.startDate ?? "").localeCompare(a.startDate ?? "");
}

function str(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed === "" ? null : trimmed;
}
