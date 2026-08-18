"use client";

import { useVirtualizer } from "@tanstack/react-virtual";
import { isToday, isYesterday, isThisWeek, isThisMonth, parseISO } from "date-fns";
import { SearchX } from "lucide-react";
import { useEffect, useMemo, useRef } from "react";
import { ApplicationRow, ROW_HEIGHT } from "@/components/applications/application-row";
import type { Application } from "@/lib/types";

const GROUP_HEIGHT = 28;

/** Buckets recency into the words someone would actually use for it. */
function bucketOf(iso: string): string {
  try {
    const date = parseISO(iso);
    if (isToday(date)) return "Today";
    if (isYesterday(date)) return "Yesterday";
    if (isThisWeek(date, { weekStartsOn: 1 })) return "This week";
    if (isThisMonth(date)) return "This month";
    return "Earlier";
  } catch {
    return "Earlier";
  }
}

type Item =
  | { kind: "group"; key: string; label: string }
  | { kind: "row"; key: string; application: Application };

/**
 * The scrolling half of the master-detail layout.
 *
 * Virtualized: a thousand-row pipeline mounting every row at once is the
 * scroll-and-lag problem this list exists to avoid, so only rows near the
 * viewport are in the DOM. Group headers are part of the same flat item list
 * rather than a nested map, because the virtualizer places one indexed
 * sequence -- nesting would mean measuring groups separately and giving up
 * the fixed-size fast path.
 *
 * Grouping only applies when the sort is recency-based. Grouping a list
 * sorted by match score under date headings would produce headings that
 * repeat and mean nothing.
 */
export function ApplicationList({
  applications,
  selectedId,
  matchScores,
  grouped = false,
  onSelect,
}: {
  applications: Application[];
  selectedId: string | null;
  matchScores: Map<string, number>;
  grouped?: boolean;
  onSelect: (application: Application) => void;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);

  const items = useMemo<Item[]>(() => {
    if (!grouped) {
      return applications.map((application) => ({
        kind: "row" as const,
        key: application.id,
        application,
      }));
    }
    const out: Item[] = [];
    let current: string | null = null;
    for (const application of applications) {
      const bucket = bucketOf(application.applied_at ?? application.updated_at);
      if (bucket !== current) {
        current = bucket;
        out.push({ kind: "group", key: `group:${bucket}`, label: bucket });
      }
      out.push({ kind: "row", key: application.id, application });
    }
    return out;
  }, [applications, grouped]);

  const virtualizer = useVirtualizer({
    count: items.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: (index) => (items[index].kind === "group" ? GROUP_HEIGHT : ROW_HEIGHT),
    overscan: 12,
  });

  // Keyboard navigation moves the selection without moving focus, so the
  // selected row has to be scrolled to explicitly -- the browser only does
  // that for free when focus itself moves.
  useEffect(() => {
    if (!selectedId) return;
    const index = items.findIndex(
      (item) => item.kind === "row" && item.application.id === selectedId,
    );
    if (index >= 0) virtualizer.scrollToIndex(index, { align: "auto" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId]);

  if (applications.length === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 px-6 text-center">
        <SearchX className="size-5 text-[color:var(--color-text-dim)]" />
        <p className="text-sm text-[color:var(--color-text-muted)]">No applications match.</p>
        <p className="text-xs text-[color:var(--color-text-dim)]">
          Try a different stage, or clear the search and filters.
        </p>
      </div>
    );
  }

  return (
    <div ref={scrollRef} className="h-full overflow-y-auto">
      <div style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
        {virtualizer.getVirtualItems().map((virtualItem) => {
          const item = items[virtualItem.index];
          return (
            <div
              key={item.key}
              style={{
                position: "absolute",
                top: 0,
                left: 0,
                width: "100%",
                height: virtualItem.size,
                transform: `translateY(${virtualItem.start}px)`,
              }}
            >
              {item.kind === "group" ? (
                <div className="flex h-full items-end px-4 pb-1">
                  <span className="text-[10px] font-medium uppercase tracking-wider text-[color:var(--color-text-dim)]">
                    {item.label}
                  </span>
                </div>
              ) : (
                <ApplicationRow
                  application={item.application}
                  selected={item.application.id === selectedId}
                  matchScore={matchScores.get(item.application.id) ?? null}
                  onSelect={() => onSelect(item.application)}
                />
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
