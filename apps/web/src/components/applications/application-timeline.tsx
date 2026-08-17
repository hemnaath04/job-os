"use client";

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

  return (
    <div className="flex flex-col gap-2">
      {FORWARD_PIPELINE.map((step, index) => {
        const reached = index <= currentIndex;
        const current = !isTerminal && index === currentIndex;
        return (
          <div key={step.status} className="flex items-center gap-2.5 text-sm">
            <span
              className={
                "flex size-4 shrink-0 items-center justify-center rounded-full border text-[9px] " +
                (reached
                  ? "border-[color:var(--color-accent-border)] bg-[color:var(--color-accent-soft)] text-[color:var(--color-accent-ink)]"
                  : "border-[color:var(--color-border)] text-transparent")
              }
            >
              {reached && <Check className="size-2.5" />}
            </span>
            <span
              className={
                current
                  ? "font-medium text-[color:var(--color-text)]"
                  : reached
                    ? "text-[color:var(--color-text-muted)]"
                    : "text-[color:var(--color-text-dim)]"
              }
            >
              {step.label}
            </span>
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
