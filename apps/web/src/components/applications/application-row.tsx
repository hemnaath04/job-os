"use client";

import { format } from "date-fns";
import { memo } from "react";
import { CompanyAvatar } from "@/components/company-avatar";
import { StatusPill } from "@/components/status-pill";
import { MatchScoreChip } from "@/components/ui/match-score";
import { jobDisplay } from "@/lib/job-display";
import type { Application } from "@/lib/types";
import { cn } from "@/lib/utils";

/** Row height, in px. Kept in sync with ApplicationList's virtualizer, which
 * needs a fixed measure to place rows without measuring each one. */
export const ROW_HEIGHT = 60;

function formatShortDate(iso: string): string {
  return format(new Date(iso), "MMM d");
}

/**
 * One row in the application list.
 *
 * Two lines, not three: company and role are what you scan for, and everything
 * else is a right-aligned column so the eye can run down one edge for status
 * or another for score instead of re-reading each row. Memoized because a
 * virtualized list re-renders every visible row on every scroll frame, and
 * these props rarely change between frames.
 */
export const ApplicationRow = memo(function ApplicationRow({
  application,
  selected,
  matchScore,
  onSelect,
}: {
  application: Application;
  selected: boolean;
  matchScore: number | null;
  onSelect: () => void;
}) {
  // Never the raw columns. An import that has not finished reading stores
  // "Untitled" and a company guessed off the URL host, and printing those put a
  // row that is still being fetched next to real roles as if it were finished.
  // See lib/job-display.ts.
  const display = jobDisplay(application.job);
  const company = display.company ?? "Company not read yet";
  const stageDate = application.applied_at ?? application.updated_at;
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-current={selected}
      style={{ height: ROW_HEIGHT }}
      className={cn(
        "group relative flex w-full items-center gap-2.5 pl-4 pr-3 text-left",
        // Hairline separator rather than a border: at this density a full
        // 1px border on every row reads as a table grid, which is heavier
        // than the content deserves.
        "after:absolute after:inset-x-3 after:bottom-0 after:h-px after:bg-[color:var(--color-border)]",
        "transition-colors duration-100 ease-out",
        selected
          ? "bg-[color:var(--color-surface-2)]"
          : "hover:bg-[color:var(--color-surface-2)]/60",
      )}
    >
      {/* Selection is an edge marker, not a fill. A saturated block behind the
          selected row competes with the inspector it drives, and at 60px tall
          a colored block is a lot of color for "you are here". */}
      <span
        aria-hidden
        className={cn(
          "absolute inset-y-1 left-0 w-0.5 rounded-full transition-colors duration-100",
          selected ? "bg-[color:var(--color-accent)]" : "bg-transparent",
        )}
      />
      <CompanyAvatar name={company} domain={application.job.company?.domain} size={26} />

      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <div className="flex min-w-0 items-center gap-2">
          <span
            className={cn(
              "truncate text-[13px] leading-tight",
              selected
                ? "font-semibold text-[color:var(--color-text)]"
                : "font-medium text-[color:var(--color-text)]",
            )}
          >
            {company}
          </span>
          <StatusPill status={application.status} size="xs" />
        </div>
        <span
          className={cn(
            "truncate text-xs leading-tight",
            display.incomplete
              ? "italic text-[color:var(--color-text-dim)]"
              : "text-[color:var(--color-text-muted)]",
          )}
          // The full sentence does not fit on a 60px row, so the row says the
          // state and the inspector next to it says what to do about it.
          title={display.note ?? undefined}
        >
          {display.title}
        </span>
      </div>

      <div className="flex shrink-0 flex-col items-end gap-0.5">
        {matchScore !== null ? (
          <MatchScoreChip score={matchScore} />
        ) : (
          // Named rather than left blank: an empty corner beside rows that do
          // have a score reads as broken. See job-fit.ts -- this is "too few
          // recognized skills to score confidently", not a failure.
          <span className="text-[10px] text-[color:var(--color-text-dim)]">Not scored</span>
        )}
        {stageDate && (
          <span className="text-[10px] tabular-nums text-[color:var(--color-text-dim)]">
            {formatShortDate(stageDate)}
          </span>
        )}
      </div>
    </button>
  );
});
