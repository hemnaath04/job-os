"use client";

import { LayoutGrid, List, Rows3, Search } from "lucide-react";
import { useMemo } from "react";
import { Select } from "@/components/ui/select";
import type { Application } from "@/lib/types";

export type ApplicationsView = "list" | "board" | "table";
export type ApplicationSort = "updated" | "applied" | "company" | "match";

const SORT_OPTIONS: { value: ApplicationSort; label: string }[] = [
  { value: "updated", label: "Recently updated" },
  { value: "applied", label: "Recently applied" },
  { value: "company", label: "Company, A to Z" },
  { value: "match", label: "AI match, high to low" },
];

const MATCH_THRESHOLDS = [
  { value: "", label: "Any match" },
  { value: "75", label: "75%+" },
  { value: "50", label: "50%+" },
  { value: "25", label: "25%+" },
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
  sort,
  onSortChange,
  view,
  onViewChange,
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
  sort: ApplicationSort;
  onSortChange: (value: ApplicationSort) => void;
  view: ApplicationsView;
  onViewChange: (view: ApplicationsView) => void;
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
    <div className="flex flex-wrap items-center gap-2">
      <div className="relative min-w-48 flex-1">
        <Search className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-[color:var(--color-text-dim)]" />
        <input
          type="search"
          value={query}
          onChange={(event) => onQueryChange(event.target.value)}
          placeholder="Search applications..."
          aria-label="Search applications"
          className="field-control w-full pl-8"
        />
      </div>
      {locationOptions.length > 1 && (
        <Select
          value={location}
          onChange={onLocationChange}
          options={locationOptions}
          aria-label="Filter by location"
          className="w-36"
        />
      )}
      {workTypeOptions.length > 1 && (
        <Select
          value={workType}
          onChange={onWorkTypeChange}
          options={workTypeOptions}
          aria-label="Filter by work type"
          className="w-32"
        />
      )}
      <Select
        value={minMatch}
        onChange={onMinMatchChange}
        options={MATCH_THRESHOLDS}
        aria-label="Filter by AI match score"
        className="w-28"
      />
      <Select
        value={sort}
        onChange={(value) => onSortChange(value as ApplicationSort)}
        options={SORT_OPTIONS}
        aria-label="Sort applications"
        className="w-44"
      />
      <div
        role="group"
        aria-label="Pipeline view"
        className="flex rounded-full border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] p-0.5"
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
      className={
        "flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium transition " +
        (active
          ? "bg-[color:var(--color-surface-hover)] text-[color:var(--color-text)] shadow-sm"
          : "text-[color:var(--color-text-muted)] hover:text-[color:var(--color-text)]")
      }
    >
      <Icon className="size-3.5" /> {label}
    </button>
  );
}
