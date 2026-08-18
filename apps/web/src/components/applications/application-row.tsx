"use client";

import { format } from "date-fns";
import { memo } from "react";
import { CompanyAvatar } from "@/components/company-avatar";
import { StatusPill } from "@/components/status-pill";
import { Badge } from "@/components/ui/badge";
import type { Application } from "@/lib/types";

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
      className={
        "flex w-full items-center gap-3 border-b border-[color:var(--color-border)] px-3 py-3 text-left transition " +
        (selected
          ? "bg-[color:var(--color-accent-soft)]"
          : "hover:bg-[color:var(--color-surface-2)]")
      }
    >
      <CompanyAvatar name={company} domain={application.job.company?.domain} size={32} />
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-medium text-[color:var(--color-text)]">
          {company}
        </div>
        <div className="truncate text-xs text-[color:var(--color-text-muted)]">
          {application.job.title}
        </div>
        <div className="mt-1.5 flex items-center gap-1.5">
          <StatusPill status={application.status} size="xs" />
          {stageDate && (
            <span className="text-[11px] text-[color:var(--color-text-dim)]">
              {formatShortDate(stageDate)}
            </span>
          )}
        </div>
      </div>
      <div className="flex shrink-0 flex-col items-end gap-1 text-right">
        {matchScore !== null ? (
          <Badge variant="accent">{matchScore}% match</Badge>
        ) : (
          // Named rather than left blank: a bare-empty corner next to a row
          // that does have a score reads as broken, not as "nothing to show
          // here". See job-fit.ts -- this is "too few recognized skills to
          // score confidently", not a bug, and the row should say so.
          <span className="text-[11px] text-[color:var(--color-text-dim)]">Not scored</span>
        )}
        {application.next_action_label && (
          <Badge variant="amber" className="max-w-28 truncate">
            {application.next_action_label}
          </Badge>
        )}
      </div>
    </button>
  );
});
