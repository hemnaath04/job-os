"use client";

import { LayoutGrid, List, Rows3, Search, X } from "lucide-react";
import { useMemo } from "react";
import { Select } from "@/components/ui/select";
import type { Application } from "@/lib/types";
import { cn } from "@/lib/utils";

export type ApplicationsView = "list" | "board" | "table";
export type ApplicationSort =
  | "updated"
  | "applied"
  | "company"
  | "match"
  | "match_asc"
  | "next_action";

const SORT_OPTIONS: { value: ApplicationSort; label: string }[] = [
  { value: "updated", label: "Recently added" },
  { value: "applied", label: "Recently applied" },
  { value: "match", label: "Highest match" },
  { value: "match_asc", label: "Lowest match" },
  { value: "company", label: "Company A–Z" },
  { value: "next_action", label: "Upcoming action" },
];

const MATCH_THRESHOLDS = [
  { value: "", label: "Any match" },
  { value: "75", label: "75%+" },
  { value: "50", label: "50%+" },
  { value: "25", label: "25%+" },
];

/** Recency windows, in days. "" means no date filter. */
export const DATE_WINDOWS = [
  { value: "", label: "Any date" },
  { value: "7", label: "Last 7 days" },
  { value: "30", label: "Last 30 days" },
  { value: "90", label: "Last 90 days" },
];

/** Distinct, non-empty values for a field, so a filter never offers an option
 * nothing in the current data actually has. */
function distinctOptions(
  applications: Application[],
  pick: (application: Application) => string | null | undefined,
  anyLabel: string,
): { value: string; label: string }[] {
  const values = new Set<string>();
  for (const application of applications) {
    const value = pick(application);
    if (value) values.add(value);
  }
  return [
    { value: "", label: anyLabel },
    ...Array.from(values)
      .sort((left, right) => left.localeCompare(right))
      .map((value) => ({ value, label: value })),
  ];
}

export function ApplicationToolbar({
  applications,
  query,
  onQueryChange,
  location,
  onLocationChange,
  workType,
  onWorkTypeChange,
  minMatch,
  onMinMatchChange,
  dateWindow,
  onDateWindowChange,
  sort,
  onSortChange,
  view,
  onViewChange,
  searchRef,
}: {
  applications: Application[];
  query: string;
  onQueryChange: (value: string) => void;
  location: string;
  onLocationChange: (value: string) => void;
  workType: string;
  onWorkTypeChange: (value: string) => void;
  minMatch: string;
  onMinMatchChange: (value: string) => void;
  dateWindow: string;
  onDateWindowChange: (value: string) => void;
  sort: ApplicationSort;
  onSortChange: (value: ApplicationSort) => void;
  view: ApplicationsView;
  onViewChange: (view: ApplicationsView) => void;
  searchRef?: React.RefObject<HTMLInputElement | null>;
}) {
  const locationOptions = useMemo(
    () => distinctOptions(applications, (a) => a.job.location, "Any location"),
    [applications],
  );
  const workTypeOptions = useMemo(
    () => distinctOptions(applications, (a) => a.job.remote, "Any work type"),
    [applications],
  );

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <div className="relative min-w-52 flex-1">
        <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-[color:var(--color-text-dim)]" />
        <input
          ref={searchRef}
          type="text"
          value={query}
          onChange={(event) => onQueryChange(event.target.value)}
          placeholder="Search applications..."
          aria-label="Search applications"
          className={cn(
            "h-8 w-full rounded-lg pl-8 pr-7 text-xs",
            "border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)]",
            "text-[color:var(--color-text)] outline-none transition-colors duration-150",
            "placeholder:text-[color:var(--color-text-dim)]",
            "hover:border-[color:var(--color-border-strong)]",
            "focus:border-[color:var(--color-accent-border)]",
          )}
        />
        {query && (
          <button
            type="button"
            onClick={() => onQueryChange("")}
            aria-label="Clear search"
            className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-0.5 text-[color:var(--color-text-dim)] transition-colors hover:text-[color:var(--color-text)]"
          >
            <X className="size-3" />
          </button>
        )}
      </div>

      {locationOptions.length > 1 && (
        <Select
          compact
          value={location}
          onChange={onLocationChange}
          options={locationOptions}
          aria-label="Filter by location"
          className="!w-32 shrink-0"
        />
      )}
      {workTypeOptions.length > 1 && (
        <Select
          compact
          value={workType}
          onChange={onWorkTypeChange}
          options={workTypeOptions}
          aria-label="Filter by work type"
          className="!w-32 shrink-0"
        />
      )}
      <Select
        compact
        value={minMatch}
        onChange={onMinMatchChange}
        options={MATCH_THRESHOLDS}
        aria-label="Filter by AI match score"
        className="!w-28 shrink-0"
      />
      <Select
        compact
        value={dateWindow}
        onChange={onDateWindowChange}
        options={DATE_WINDOWS}
        aria-label="Filter by date"
        className="!w-32 shrink-0"
      />
      <Select
        compact
        value={sort}
        onChange={(value) => onSortChange(value as ApplicationSort)}
        options={SORT_OPTIONS}
        aria-label="Sort applications"
        className="!w-40 shrink-0"
      />

      <div
        role="group"
        aria-label="Pipeline view"
        className="flex shrink-0 rounded-lg border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] p-0.5"
      >
        <ViewButton active={view === "list"} onClick={() => onViewChange("list")} icon={List} label="List" />
        <ViewButton active={view === "board"} onClick={() => onViewChange("board")} icon={LayoutGrid} label="Board" />
        <ViewButton active={view === "table"} onClick={() => onViewChange("table")} icon={Rows3} label="Table" />
      </div>
    </div>
  );
}

function ViewButton({
  active,
  onClick,
  icon: Icon,
  label,
}: {
  active: boolean;
  onClick: () => void;
  icon: typeof List;
  label: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      aria-label={label}
      title={label}
      className={cn(
        "flex items-center rounded-[6px] px-2 py-1 transition-colors duration-150",
        active
          ? "bg-[color:var(--color-surface-hover)] text-[color:var(--color-text)]"
          : "text-[color:var(--color-text-dim)] hover:text-[color:var(--color-text)]",
      )}
    >
      <Icon className="size-3.5" />
    </button>
  );
}
