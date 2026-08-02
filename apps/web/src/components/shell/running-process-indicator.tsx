"use client";

import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { AlertCircle, ArrowUpRight, CheckCircle2, Loader2, X } from "lucide-react";
import Link from "next/link";
import {
  dismissOperation,
  operationDetail,
  operationTitle,
  useOperations,
  type Operation,
} from "@/lib/operations-store";

/**
 * A small floating stack that reports every long-running agent job, a tailor
 * (draft), an AI revision, a resume import, a profile extract, on every
 * workspace page. It survives navigation because it is mounted once in the shell
 * and reads a global store, so a job started on one page keeps reporting here
 * after the user leaves, and a click lands on the finished result once it is
 * ready. More than one job at a time stacks, newest nearest the corner.
 *
 * Placement is bottom-left: clear of the bottom-right toaster, clear of the
 * desktop sidebar (never wider than 232px), and lifted above the mobile bottom
 * nav and the home-indicator safe area.
 */
export function RunningProcessIndicator() {
  const operations = useOperations();
  const reduceMotion = useReducedMotion();

  return (
    <div className="pointer-events-none fixed bottom-[calc(env(safe-area-inset-bottom)+4.75rem)] left-4 z-40 flex flex-col-reverse items-start gap-2 lg:bottom-6 lg:left-[248px]">
      <AnimatePresence initial={false}>
        {operations.map((op) => (
          <motion.div
            key={op.id}
            layout={!reduceMotion}
            initial={reduceMotion ? { opacity: 0 } : { opacity: 0, y: 10, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={reduceMotion ? { opacity: 0 } : { opacity: 0, y: 8, scale: 0.98 }}
            transition={
              reduceMotion
                ? { duration: 0.12 }
                : { type: "spring", stiffness: 320, damping: 30 }
            }
            className="pointer-events-auto w-[min(20rem,calc(100vw-2rem))]"
          >
            <OperationCard op={op} />
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  );
}

function OperationCard({ op }: { op: Operation }) {
  const running = op.status === "running";
  const title = operationTitle(op);
  const detail = operationDetail(op);

  const ariaLabel = running
    ? `${title}. In progress, open the page it runs on.`
    : op.status === "done"
      ? `${title}. ${detail}.`
      : `${title}. ${detail}`;

  return (
    <div className="relative">
      <Link
        href={op.href}
        aria-label={ariaLabel}
        className="workspace-panel workspace-panel-interactive block rounded-[var(--radius-card)] p-3.5 transition active:scale-[.96] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-accent-ink)]"
      >
        {/* aria-live so a screen reader announces the change from running to
            ready without the user having to watch the corner. */}
        <div role="status" aria-live="polite" className="flex items-start gap-2.5">
          <StatusIcon status={op.status} />
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1.5 text-sm font-semibold text-[color:var(--color-text)]">
              <span className="truncate">{title}</span>
              {!running && (
                <ArrowUpRight className="size-3.5 shrink-0 text-[color:var(--color-text-dim)]" />
              )}
            </div>
            <p className="mt-0.5 truncate text-xs text-[color:var(--color-text-muted)]">
              {detail}
            </p>
            {/* What the agent found, under what it is doing. This is the line
                that proves the run is alive during a model call, because it
                changes with the work rather than with a timer. */}
            {running && op.detail && (
              <p className="mt-0.5 truncate text-xs text-[color:var(--color-text-dim)]">
                {op.detail}
              </p>
            )}
            {running && <ProgressTrack pct={op.pct} />}
          </div>
        </div>
      </Link>

      {!running && (
        <button
          type="button"
          onClick={() => dismissOperation(op.id)}
          aria-label="Dismiss"
          className="absolute -right-1.5 -top-1.5 grid size-5 place-items-center rounded-full border border-[color:var(--color-border)] bg-[color:var(--color-surface-1)] text-[color:var(--color-text-dim)] shadow-[var(--shadow-xs)] transition hover:text-[color:var(--color-text)] active:scale-[.96] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-accent-ink)]"
        >
          <X className="size-3" />
        </button>
      )}
    </div>
  );
}

function StatusIcon({ status }: { status: Operation["status"] }) {
  if (status === "running") {
    return (
      <Loader2
        className="mt-0.5 size-4 shrink-0 animate-spin text-[color:var(--color-violet)]"
        aria-hidden="true"
      />
    );
  }
  if (status === "done") {
    return (
      <CheckCircle2
        className="mt-0.5 size-4 shrink-0 text-[color:var(--color-mint-ink)]"
        aria-hidden="true"
      />
    );
  }
  return (
    <AlertCircle
      className="mt-0.5 size-4 shrink-0 text-[color:var(--color-rose-ink)]"
      aria-hidden="true"
    />
  );
}

/**
 * Only drawn when the agent has reported a real fraction. Before that the copy
 * says the work is running and no bar is shown, rather than inventing a
 * percentage the server never sent.
 */
function ProgressTrack({ pct }: { pct: number | null }) {
  if (pct === null) return null;
  const percent = Math.round(pct * 100);
  return (
    <div className="mt-2 flex items-center gap-2">
      <div
        className="h-1.5 flex-1 overflow-hidden rounded-full bg-[color:var(--color-surface-3)]"
        role="progressbar"
        aria-label="Progress"
        aria-valuenow={percent}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div
          className="h-full rounded-full bg-gradient-brand transition-[width] duration-500 ease-out"
          style={{ width: `${percent}%` }}
        />
      </div>
      <span className="shrink-0 text-[10px] tabular-nums text-[color:var(--color-text-dim)]">
        {percent}%
      </span>
    </div>
  );
}
