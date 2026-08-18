"use client";

import { cn } from "@/lib/utils";

/**
 * A fit score, rendered so the number reads before you read it.
 *
 * The tier colors are the point: scanning a pipeline of thirty roles, "which
 * of these actually fit me" should be answerable from the color alone, with
 * the number there to confirm rather than to be parsed. Gold is reserved for
 * the strong ones so it stays meaningful -- if every score were the accent
 * color, the accent would say nothing.
 *
 * Thresholds match the filter dropdown's own buckets (75/50/25 in
 * application-toolbar.tsx), so "75%+" in the filter and "strong" here never
 * disagree about what a good match is.
 */
export type ScoreTier = "strong" | "good" | "fair" | "weak";

export function scoreTier(score: number): ScoreTier {
  if (score >= 75) return "strong";
  if (score >= 50) return "good";
  if (score >= 25) return "fair";
  return "weak";
}

const TIER_CHIP: Record<ScoreTier, string> = {
  strong:
    "bg-[color:var(--color-accent-soft)] text-[color:var(--color-accent-ink)] ring-[color:var(--color-accent-border)]",
  good: "bg-[color:var(--color-amber)]/10 text-[color:var(--color-amber)] ring-[color:var(--color-amber)]/25",
  fair: "bg-[color:var(--color-surface-3)] text-[color:var(--color-text-muted)] ring-[color:var(--color-border-strong)]",
  weak: "bg-transparent text-[color:var(--color-text-dim)] ring-[color:var(--color-border)]",
};

const TIER_TRACK: Record<ScoreTier, string> = {
  strong: "bg-[color:var(--color-accent)]",
  good: "bg-[color:var(--color-amber)]",
  fair: "bg-[color:var(--color-text-dim)]",
  weak: "bg-[color:var(--color-border-strong)]",
};

/**
 * Compact form for a list row: a chip whose fill is the score itself.
 *
 * The bar lives *inside* the chip rather than beside it, so a dense row gains
 * a second encoding of the same value without spending a second slot of
 * horizontal space on it.
 */
export function MatchScoreChip({ score, className }: { score: number; className?: string }) {
  const tier = scoreTier(score);
  return (
    <span
      className={cn(
        "relative inline-flex items-center gap-1 overflow-hidden rounded-full px-2 py-0.5",
        "text-[11px] font-semibold tabular-nums ring-1 ring-inset",
        TIER_CHIP[tier],
        className,
      )}
    >
      <span
        aria-hidden
        className={cn("absolute inset-x-0 bottom-0 h-px opacity-70", TIER_TRACK[tier])}
        style={{ width: `${Math.max(score, 4)}%` }}
      />
      {score}%
    </span>
  );
}

/**
 * Expanded form for the inspector, where there is room to show the reasoning
 * rather than only the verdict.
 */
export function MatchScoreMeter({
  score,
  matched,
  total,
}: {
  score: number;
  matched: number;
  total: number;
}) {
  const tier = scoreTier(score);
  return (
    <div>
      <div className="flex items-baseline justify-between">
        <div className="flex items-baseline gap-1.5">
          <span className="text-2xl font-semibold tabular-nums leading-none text-[color:var(--color-text)]">
            {score}
          </span>
          <span className="text-sm text-[color:var(--color-text-dim)]">%</span>
        </div>
        <span className="text-xs tabular-nums text-[color:var(--color-text-muted)]">
          {matched} of {total} skills
        </span>
      </div>
      <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-[color:var(--color-surface-3)]">
        <div
          className={cn("h-full rounded-full transition-[width] duration-500 ease-out", TIER_TRACK[tier])}
          style={{ width: `${score}%` }}
        />
      </div>
    </div>
  );
}

/**
 * One skill the posting named, and whether the profile backs it. Rendered as
 * a chip pair rather than two prose lists: the comparison is the content, and
 * side-by-side chips make "what am I missing" answerable at a glance.
 */
export function SkillChip({ label, matched }: { label: string; matched: boolean }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] ring-1 ring-inset",
        matched
          ? "bg-[color:var(--color-accent-soft)] text-[color:var(--color-accent-ink)] ring-[color:var(--color-accent-border)]"
          : "bg-transparent text-[color:var(--color-text-dim)] ring-[color:var(--color-border)]",
      )}
    >
      {label}
    </span>
  );
}
