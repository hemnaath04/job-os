"use client";

import { useVirtualizer } from "@tanstack/react-virtual";
import { SearchX } from "lucide-react";
import { useEffect, useMemo, useRef } from "react";
import { ApplicationRow, ROW_HEIGHT } from "@/components/applications/application-row";
import { buildListItems } from "@/lib/application-groups";
import type { Application } from "@/lib/types";

const GROUP_HEIGHT = 28;

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
 * Which sorts carry date headings, and what each heading says, is decided by
 * `buildListItems` rather than here: the heading and the order it sits in have
 * to be read off the same date, so one module owns both.
 */
export function ApplicationList({
  applications,
  selectedId,
  matchScores,
  sort,
  onSelect,
}: {
  applications: Application[];
  selectedId: string | null;
  matchScores: Map<string, number>;
  sort: string;
  onSelect: (application: Application) => void;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);

  const items = useMemo(() => buildListItems(applications, sort), [applications, sort]);

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
