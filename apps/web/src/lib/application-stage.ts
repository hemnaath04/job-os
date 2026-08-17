/**
 * How the ten raw `AppStatus` values collapse into the stages shown on the
 * Applications page. The status tabs need this grouping; so does the
 * inspector's timeline (which stage is "current" for a given application) and
 * the header summary stats. One table, so the three never drift apart.
 */
import type { AppStatus } from "@/lib/types";

export type PrimaryStage = "all" | "saved" | "applied" | "screening" | "interview" | "offer";
export type SecondaryStage = "rejected" | "withdrawn" | "archived";

interface StageDef<K extends string> {
  key: K;
  label: string;
  /** null means "every status", used only by the all-inclusive All tab. */
  statuses: AppStatus[] | null;
}

export const PRIMARY_STAGES: StageDef<PrimaryStage>[] = [
  { key: "all", label: "All", statuses: null },
  { key: "saved", label: "Saved", statuses: ["wishlist", "ready_to_apply"] },
  { key: "applied", label: "Applied", statuses: ["applied"] },
  { key: "screening", label: "Screening", statuses: ["oa_received"] },
  { key: "interview", label: "Interview", statuses: ["interview_scheduled"] },
  { key: "offer", label: "Offer", statuses: ["offer", "accepted"] },
];

// Real statuses the pipeline can be in, but a job hunt is not defined by how
// many of these you have, so they sit apart from the primary stages rather
// than competing for the same visual weight.
export const SECONDARY_STAGES: StageDef<SecondaryStage>[] = [
  { key: "rejected", label: "Rejected", statuses: ["rejected"] },
  { key: "withdrawn", label: "Withdrawn", statuses: ["withdrawn", "ghosted"] },
  { key: "archived", label: "Archived", statuses: null },
];

export function matchesStatuses(status: AppStatus, statuses: AppStatus[] | null): boolean {
  return statuses === null || statuses.includes(status);
}

// The forward path a role travels when nothing goes wrong. Terminal exits
// (rejected, withdrawn, ghosted) are not on this path; the inspector timeline
// renders those as where the path stopped, not as a step on it.
export const FORWARD_PIPELINE: { status: AppStatus; label: string }[] = [
  { status: "wishlist", label: "Saved" },
  { status: "applied", label: "Applied" },
  { status: "oa_received", label: "Screening" },
  { status: "interview_scheduled", label: "Interview" },
  { status: "offer", label: "Offer" },
];

const FORWARD_INDEX: Record<AppStatus, number> = {
  wishlist: 0,
  ready_to_apply: 0,
  applied: 1,
  oa_received: 2,
  interview_scheduled: 3,
  offer: 4,
  accepted: 4,
  rejected: -1,
  withdrawn: -1,
  ghosted: -1,
};

export const TERMINAL_STATUSES: ReadonlySet<AppStatus> = new Set(["rejected", "withdrawn", "ghosted"]);

/** How far along the forward pipeline a status has reached, for the
 * timeline's filled-step count. -1 for a status with no forward position. */
export function forwardStepIndex(status: AppStatus): number {
  return FORWARD_INDEX[status];
}

/** "Still moving": not yet decided one way or the other. Used for the header
 * summary's "active" count, which the raw status list has no single field for. */
export function isActiveStatus(status: AppStatus): boolean {
  return !TERMINAL_STATUSES.has(status) && status !== "offer" && status !== "accepted";
}
