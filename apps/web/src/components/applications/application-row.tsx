"use client";

import { format } from "date-fns";
import { memo } from "react";
import { CompanyAvatar } from "@/components/company-avatar";
import type { Application } from "@/lib/types";
import { STATUS_LABELS } from "@/lib/types";

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
        "flex w-full items-center gap-3 border-b border-[color:var(--color-border)] px-3 py-2.5 text-left transition " +
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
        <div className="mt-0.5 truncate text-[11px] text-[color:var(--color-text-dim)]">
          {STATUS_LABELS[application.status]}
          {stageDate ? ` · ${formatShortDate(stageDate)}` : ""}
        </div>
      </div>
      <div className="flex shrink-0 flex-col items-end gap-0.5 text-right">
        {matchScore !== null && (
          <span className="text-[11px] font-semibold tabular-nums text-[color:var(--color-accent-ink)]">
            {matchScore}% match
          </span>
        )}
        {application.next_action_label && (
          <span className="max-w-28 truncate text-[10px] text-[color:var(--color-amber)]">
            {application.next_action_label}
          </span>
        )}
      </div>
    </button>
  );
});
