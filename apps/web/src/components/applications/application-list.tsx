"use client";

import { useVirtualizer } from "@tanstack/react-virtual";
import { SearchX } from "lucide-react";
import { useRef } from "react";
import { ApplicationRow } from "@/components/applications/application-row";
import type { Application } from "@/lib/types";

const ROW_HEIGHT = 78;

/**
 * The scrolling half of the master-detail layout. Virtualized: a 1,000-row
 * pipeline mounting every row at once is the exact scroll-and-lag problem
 * this whole redesign exists to fix, so only the rows near the viewport ever
 * exist in the DOM.
 */
export function ApplicationList({
  applications,
  selectedId,
  matchScores,
  onSelect,
}: {
  applications: Application[];
  selectedId: string | null;
  matchScores: Map<string, number>;
  onSelect: (application: Application) => void;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const virtualizer = useVirtualizer({
    count: applications.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: 12,
  });

  if (applications.length === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 px-6 text-center">
        <SearchX className="size-6 text-[color:var(--color-text-dim)]" />
        <p className="text-sm text-[color:var(--color-text-muted)]">No applications match.</p>
        <p className="text-xs text-[color:var(--color-text-dim)]">
          Try a different stage, or clear the search and filters.
        </p>
      </div>
    );
  }

  return (
    <div ref={scrollRef} className="h-full overflow-y-auto">
      <div
        style={{ height: virtualizer.getTotalSize(), position: "relative" }}
      >
        {virtualizer.getVirtualItems().map((item) => {
          const application = applications[item.index];
          return (
            <div
              key={application.id}
              style={{
                position: "absolute",
                top: 0,
                left: 0,
                width: "100%",
                height: item.size,
                transform: `translateY(${item.start}px)`,
              }}
            >
              <ApplicationRow
                application={application}
                selected={application.id === selectedId}
                matchScore={matchScores.get(application.id) ?? null}
                onSelect={() => onSelect(application)}
              />
            </div>
          );
        })}
      </div>
    </div>
  );
}
