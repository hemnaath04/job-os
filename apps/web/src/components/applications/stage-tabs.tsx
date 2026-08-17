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
    <div className="flex flex-col gap-1.5">
      <div className="flex flex-wrap items-center gap-1 text-xs">
        {PRIMARY_STAGES.map((stage, index) => {
          const count = applications.filter((a) => matchesStatuses(a.status, stage.statuses)).length;
          return (
            <StageTab
              key={stage.key}
              label={stage.label}
              count={count}
              selected={active === stage.key}
              onClick={() => onChange(stage.key)}
              withDivider={index > 0}
            />
          );
        })}
      </div>
      <div className="flex flex-wrap items-center gap-1 text-[11px] text-[color:var(--color-text-dim)]">
        {SECONDARY_STAGES.map((stage) => {
          const count =
            stage.key === "archived"
              ? archivedCount
              : applications.filter((a) => matchesStatuses(a.status, stage.statuses)).length;
          return (
            <StageTab
              key={stage.key}
              label={stage.label}
              count={count}
              selected={active === stage.key}
              onClick={() => onChange(stage.key)}
              muted
            />
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
  withDivider = false,
  muted = false,
}: {
  label: string;
  count: number;
  selected: boolean;
  onClick: () => void;
  withDivider?: boolean;
  muted?: boolean;
}) {
  return (
    <>
      {withDivider && <span className="text-[color:var(--color-border-strong)]">|</span>}
      <button
        type="button"
        onClick={onClick}
        aria-pressed={selected}
        className={
          "rounded-md px-1.5 py-1 font-medium transition " +
          (selected
            ? "text-[color:var(--color-text)]"
            : muted
              ? "text-[color:var(--color-text-dim)] hover:text-[color:var(--color-text-muted)]"
              : "text-[color:var(--color-text-muted)] hover:text-[color:var(--color-text)]")
        }
      >
        <span className={selected ? "underline decoration-2 decoration-[color:var(--color-accent-border)]" : ""}>
          {label}
        </span>{" "}
        <span className="tabular-nums text-[color:var(--color-text-dim)]">{count}</span>
      </button>
    </>
  );
}
