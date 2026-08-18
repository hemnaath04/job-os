"use client";

import { format } from "date-fns";
import { memo } from "react";
import { CompanyAvatar } from "@/components/company-avatar";
import { StatusPill } from "@/components/status-pill";
import { Badge } from "@/components/ui/badge";
import { MatchScoreChip } from "@/components/ui/match-score";
import type { Application } from "@/lib/types";
import { cn } from "@/lib/utils";

function formatShortDate(iso: string): string {
  return format(new Date(iso), "MMM d");
}

/** One row in the application list. Memoized: a virtualized list of 1,000
 * rows re-renders every visible row on every scroll frame otherwise, and this
 * component's own props (an Application, a selected flag) rarely change
 * between those frames. */
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
  const company = application.job.company?.name ?? "Unknown company";
  const stageDate = application.applied_at ?? application.updated_at;
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-current={selected}
      className={cn(
        "group relative flex w-full items-center gap-3 border-b border-[color:var(--color-border)]",
        "py-3 pl-4 pr-3 text-left",
        "transition-colors duration-150 ease-out",
        selected
          ? "bg-[color:var(--color-accent-soft)]"
          : "hover:bg-[color:var(--color-surface-2)]",
      )}
    >
      {/* Selection reads on the edge rather than as a heavier fill, so the
          selected row stays legible against the inspector it controls. 1px:
          a thick colored rail is the tell of a component that had nothing
          better to say. */}
      <span
        aria-hidden
        className={cn(
          "absolute inset-y-0 left-0 w-px transition-colors duration-150",
          selected ? "bg-[color:var(--color-accent)]" : "bg-transparent",
        )}
      />
      <CompanyAvatar name={company} domain={application.job.company?.domain} size={32} />
      <div className="min-w-0 flex-1">
        <div className="flex min-w-0 items-center gap-2">
          <span className="truncate text-sm font-medium text-[color:var(--color-text)]">
            {company}
          </span>
          {stageDate && (
            <span className="shrink-0 text-[11px] tabular-nums text-[color:var(--color-text-dim)]">
              {formatShortDate(stageDate)}
            </span>
          )}
        </div>
        <div className="truncate text-xs text-[color:var(--color-text-muted)]">
          {application.job.title}
        </div>
        <div className="mt-1.5 flex items-center gap-1.5">
          <StatusPill status={application.status} size="xs" />
          {application.next_action_label && (
            <Badge variant="amber" className="max-w-32 truncate">
              {application.next_action_label}
            </Badge>
          )}
        </div>
      </div>
      <div className="flex shrink-0 items-center">
        {matchScore !== null ? (
          <MatchScoreChip score={matchScore} />
        ) : (
          // Named rather than left blank: a bare-empty corner next to a row
          // that does have a score reads as broken, not as "nothing to show
          // here". See job-fit.ts -- this is "too few recognized skills to
          // score confidently", not a bug, and the row should say so.
          <span className="text-[11px] text-[color:var(--color-text-dim)]">Not scored</span>
        )}
      </div>
    </button>
  );
});
