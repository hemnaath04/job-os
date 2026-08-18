"use client";

import { PRIMARY_STAGES, SECONDARY_STAGES, matchesStatuses } from "@/lib/application-stage";
import type { PrimaryStage, SecondaryStage } from "@/lib/application-stage";
import type { Application } from "@/lib/types";

export type StageFilter = PrimaryStage | SecondaryStage;

/**
 * Replaces the Kanban's columns as the primary way to narrow the list.
 * Secondary stages (rejected, withdrawn, archived) get the same tab shape but
 * a quieter row underneath, per the brief: supported, not dominant.
 */
export function StageTabs({
  applications,
  archivedCount,
  active,
  onChange,
}: {
  applications: Application[];
  archivedCount: number;
  active: StageFilter;
  onChange: (stage: StageFilter) => void;
}) {
  return (
    <div className="flex flex-col gap-2.5">
      <div
        role="tablist"
        aria-label="Filter by stage"
        className="inline-flex flex-wrap items-center gap-1 self-start rounded-full border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] p-1"
      >
        {PRIMARY_STAGES.map((stage) => {
          const count = applications.filter((a) => matchesStatuses(a.status, stage.statuses)).length;
          return (
            <StageTab
              key={stage.key}
              label={stage.label}
              count={count}
              selected={active === stage.key}
              onClick={() => onChange(stage.key)}
            />
          );
        })}
      </div>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-1 text-[11px]">
        {SECONDARY_STAGES.map((stage, index) => {
          const count =
            stage.key === "archived"
              ? archivedCount
              : applications.filter((a) => matchesStatuses(a.status, stage.statuses)).length;
          return (
            <div key={stage.key} className="flex items-center gap-3">
              {index > 0 && <span className="text-[color:var(--color-border-strong)]">·</span>}
              <SecondaryStageTab
                label={stage.label}
                count={count}
                selected={active === stage.key}
                onClick={() => onChange(stage.key)}
              />
            </div>
          );
        })}
      </div>
    </div>
  );
}

function StageTab({
  label,
  count,
  selected,
  onClick,
}: {
  label: string;
  count: number;
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      role="tab"
      onClick={onClick}
      aria-pressed={selected}
      aria-selected={selected}
      className={
        "flex items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-medium transition " +
        (selected
          ? "bg-[color:var(--color-accent-soft)] text-[color:var(--color-accent-ink)] shadow-sm"
          : "text-[color:var(--color-text-muted)] hover:bg-[color:var(--color-surface-hover)] hover:text-[color:var(--color-text)]")
      }
    >
      {label}
      <span className={"tabular-nums " + (selected ? "opacity-80" : "text-[color:var(--color-text-dim)]")}>
        {count}
      </span>
    </button>
  );
}

function SecondaryStageTab({
  label,
  count,
  selected,
  onClick,
}: {
  label: string;
  count: number;
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      role="tab"
      onClick={onClick}
      aria-pressed={selected}
      aria-selected={selected}
      className={
        "flex items-center gap-1 rounded-md py-0.5 font-medium transition " +
        (selected
          ? "text-[color:var(--color-text)]"
          : "text-[color:var(--color-text-dim)] hover:text-[color:var(--color-text-muted)]")
      }
    >
      {label}
      <span className="tabular-nums text-[color:var(--color-text-dim)]">{count}</span>
    </button>
  );
}
