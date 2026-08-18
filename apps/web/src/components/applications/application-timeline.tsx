"use client";

import { format } from "date-fns";
import { Check } from "lucide-react";
import { FORWARD_PIPELINE, TERMINAL_STATUSES, forwardStepIndex } from "@/lib/application-stage";
import type { Application } from "@/lib/types";
import { STATUS_LABELS } from "@/lib/types";

/**
 * There is no stage-history log reachable from the frontend, only the
 * current status and `applied_at`, so a rejected or withdrawn application
 * cannot honestly show which of screening or interview it actually reached.
 * Rather than guess, this marks only what is directly known (saved, and
 * applied when `applied_at` is set) and reports the outcome as its own line.
 */
export function ApplicationTimeline({ application }: { application: Application }) {
  const isTerminal = TERMINAL_STATUSES.has(application.status);
  const currentIndex = isTerminal
    ? application.applied_at
      ? 1
      : 0
    : forwardStepIndex(application.status);

  // Only two dates are actually known: when it was saved, and when it was
  // applied. Showing a date beside a step we cannot date would be inventing
  // history, so the rest stay bare.
  const dateFor = (index: number): string | null => {
    if (index === 0) return format(new Date(application.created_at), "MMM d");
    if (index === 1 && application.applied_at) {
      return format(new Date(application.applied_at), "MMM d");
    }
    return null;
  };

  return (
    <div className="flex flex-col">
      {FORWARD_PIPELINE.map((step, index) => {
        const reached = index <= currentIndex;
        const current = !isTerminal && index === currentIndex;
        const date = dateFor(index);
        return (
          <div key={step.status} className="flex items-center gap-2 py-0.5 text-xs">
            <span
              className={
                "flex size-3.5 shrink-0 items-center justify-center rounded-full border " +
                (reached
                  ? "border-[color:var(--color-accent-border)] bg-[color:var(--color-accent-soft)] text-[color:var(--color-accent-ink)]"
                  : "border-[color:var(--color-border)] text-transparent")
              }
            >
              {reached && <Check className="size-2" />}
            </span>
            <span
              className={
                "flex-1 " +
                (current
                  ? "font-medium text-[color:var(--color-text)]"
                  : reached
                    ? "text-[color:var(--color-text-muted)]"
                    : "text-[color:var(--color-text-dim)]")
              }
            >
              {step.label}
            </span>
            {date && (
              <span className="tabular-nums text-[10px] text-[color:var(--color-text-dim)]">
                {date}
              </span>
            )}
          </div>
        );
      })}
      {isTerminal && (
        <>
          <div className="flex items-center gap-2.5 text-sm">
            <span className="flex size-4 shrink-0 items-center justify-center rounded-full border border-[color:var(--color-rose)]/60 bg-[color:var(--color-rose)]/15 text-[9px]">
              <Check className="size-2.5 text-[color:var(--color-rose-ink)]" />
            </span>
            <span className="font-medium text-[color:var(--color-rose-ink)]">
              {STATUS_LABELS[application.status]}
            </span>
          </div>
          <p className="pl-6.5 text-[11px] text-[color:var(--color-text-dim)]">
            The exact stage reached before this isn&apos;t tracked, only that it happened.
          </p>
        </>
      )}
    </div>
  );
}
