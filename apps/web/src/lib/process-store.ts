"use client";

import { useSyncExternalStore } from "react";
import { appwriteWorkspace } from "@/lib/appwrite/workspace";
import { isAppwriteWorkspaceEnabled } from "@/lib/appwrite/config";
import type { TailorResponse } from "@/lib/types";

/**
 * A global, navigation-surviving view of the one long-running background job the
 * app runs: a resume tailor pass.
 *
 * Why a module-level store rather than component state: a tailor runs on the
 * Appwrite agent for a minute or more, and the user is meant to leave the Tailor
 * page while it works. The page that started it unmounts on navigation, so the
 * only thing that can keep watching the run, and click through to the result
 * when it lands, is something mounted once at the shell level. This store is that
 * something. It reads the same localStorage pointers the Tailor page writes, and
 * polls the real agent job, so what it shows is the true server state, never a
 * fabricated progress model.
 *
 * The keys and shapes below mirror the private contract in
 * app/(app)/tailor/page.tsx. They are duplicated by value rather than imported so
 * the shell does not pull a page into its bundle; if the Tailor page renames
 * them, rename them here too.
 */

// The in-flight tailor run: an agent job id plus the ids the result is saved
// under. Written by the Tailor page when a run starts, cleared on finish.
const ACTIVE_TAILOR_KEY = "tailor:active";
// The last finished run, written by the Tailor page (and by this store when it
// observes a completion off-page) so the result stays reachable.
const LAST_TAILOR_KEY = "tailor:last";

// Mirror of the Tailor page's aging rules, so this watcher gives up on a wedged
// run at the same point the page would rather than spinning forever.
const TAILOR_MAX_AGE_MS = 20 * 60 * 1_000;
const TAILOR_QUEUED_GRACE_MS = 2 * 60 * 1_000;
const POLL_MS = 1_500;
// A few consecutive transient read failures are a blip; past this it is a real
// loss of contact worth telling the user about.
const MAX_TRANSIENT_FAILURES = 8;

type StoredActiveTailor = {
  jobId: string;
  resumeId: string;
  jobPostingId: string;
  templateId?: string;
  startedAt: string;
};

type StoredLastTailor = {
  resumeId: string;
  versionId: string;
  jobPostingId: string;
  savedAt: string;
};

export type RunningProcess =
  | {
      status: "running";
      kind: "tailor";
      title: string;
      /** The agent's current stage, e.g. "Composing". Null before it reports one. */
      stage: string | null;
      /** 0..1 real progress from the agent, or null when it has not reported any. */
      pct: number | null;
      /** Where a click leads while the run is in flight. */
      href: string;
    }
  | {
      status: "done";
      kind: "tailor";
      title: string;
      /** The finished resume version. */
      href: string;
    }
  | {
      status: "failed";
      kind: "tailor";
      title: string;
      message: string;
      /** Back to where the run can be retried. */
      href: string;
    };

let state: RunningProcess | null = null;
// The run currently being polled. Set while a job is in flight so a terminal
// state is only ever emitted for a run this store actually watched, never
// synthesized from a stale pointer on a cold load.
let watchedRun: StoredActiveTailor | null = null;
let transientFailures = 0;
let timer: ReturnType<typeof setTimeout> | null = null;
let ticking = false;

const listeners = new Set<() => void>();

function emit(next: RunningProcess | null) {
  state = next;
  for (const listener of listeners) listener();
}

function readJson<T>(key: string): T | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : null;
  } catch {
    return null;
  }
}

function loadActive(): StoredActiveTailor | null {
  const parsed = readJson<Partial<StoredActiveTailor>>(ACTIVE_TAILOR_KEY);
  if (!parsed?.jobId || !parsed.startedAt) return null;
  return parsed as StoredActiveTailor;
}

function loadLast(): StoredLastTailor | null {
  const parsed = readJson<Partial<StoredLastTailor>>(LAST_TAILOR_KEY);
  if (!parsed?.versionId || !parsed.resumeId || !parsed.savedAt) return null;
  return parsed as StoredLastTailor;
}

function clearActive() {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(ACTIVE_TAILOR_KEY);
  } catch {
    /* non-critical */
  }
}

function saveLast(last: StoredLastTailor) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(LAST_TAILOR_KEY, JSON.stringify(last));
  } catch {
    /* quota or private mode: the version is still saved server-side */
  }
}

function ageMs(run: StoredActiveTailor): number | null {
  const started = Date.parse(run.startedAt);
  return Number.isFinite(started) ? Date.now() - started : null;
}

function clamp01(value: number | null | undefined): number | null {
  if (typeof value !== "number" || Number.isNaN(value)) return null;
  return Math.max(0, Math.min(1, value));
}

function runningState(stage: string | null, pct: number | null): RunningProcess {
  return {
    status: "running",
    kind: "tailor",
    title: "Tailoring your resume",
    stage,
    pct,
    href: "/tailor",
  };
}

function doneState(resumeId: string, versionId: string): RunningProcess {
  return {
    status: "done",
    kind: "tailor",
    title: "Tailored resume ready",
    href: `/resumes/${resumeId}/${versionId}`,
  };
}

function failedState(message: string): RunningProcess {
  return { status: "failed", kind: "tailor", title: "Tailoring did not finish", message, href: "/tailor" };
}

function schedule() {
  if (listeners.size === 0) {
    ticking = false;
    return;
  }
  timer = setTimeout(() => void tick(), POLL_MS);
}

async function tick() {
  // Legacy (non-Appwrite) tailoring awaits in memory on the Tailor page and
  // leaves no pollable job, so there is nothing for a global watcher to do.
  if (!isAppwriteWorkspaceEnabled) {
    if (state !== null) emit(null);
    ticking = false;
    return;
  }

  const active = loadActive();

  // A valid in-flight pointer: attach to it and read the real agent job.
  if (active) {
    const age = ageMs(active);
    if (age !== null && age > TAILOR_MAX_AGE_MS) {
      clearActive();
      if (watchedRun) {
        watchedRun = null;
        emit(failedState("The tailoring run timed out. Try again."));
      }
      schedule();
      return;
    }

    if (!watchedRun || watchedRun.jobId !== active.jobId) {
      watchedRun = active;
      transientFailures = 0;
      emit(runningState(null, null));
    }

    try {
      const job = await appwriteWorkspace.getAgentJob<TailorResponse>(active.jobId);
      transientFailures = 0;

      if (job.status === "succeeded") {
        clearActive();
        watchedRun = null;
        if (job.output) {
          appwriteWorkspace.registerVersionFile(job.output);
          saveLast({
            resumeId: job.output.resume_id,
            versionId: job.output.id,
            jobPostingId: active.jobPostingId,
            savedAt: new Date().toISOString(),
          });
          emit(doneState(job.output.resume_id, job.output.id));
        } else {
          emit(failedState("The run finished without a resume. Try again."));
        }
      } else if (job.status === "failed") {
        clearActive();
        watchedRun = null;
        emit(failedState(job.error || "The tailoring agent failed."));
      } else if (job.status === "queued" && age !== null && age > TAILOR_QUEUED_GRACE_MS) {
        clearActive();
        watchedRun = null;
        emit(failedState("The agent never started this run. Try again."));
      } else {
        emit(runningState(job.progress?.stage ?? null, clamp01(job.progress?.pct)));
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : "";
      if (/404|not found|could not be found/i.test(message)) {
        clearActive();
        watchedRun = null;
        emit(failedState("That run no longer exists. Start a new one."));
      } else {
        transientFailures += 1;
        if (transientFailures > MAX_TRANSIENT_FAILURES) {
          clearActive();
          watchedRun = null;
          emit(failedState("Lost contact with the run. Try again."));
        }
        // Otherwise keep the current running state and try again next tick.
      }
    }
    schedule();
    return;
  }

  // No active pointer. If we were mid-run, the pointer was cleared elsewhere:
  // the Tailor page finished the run itself (prefer its recorded result), or the
  // user cancelled (drop the pill). We do not resurrect a "done" from a stale
  // pointer on a cold load, so this only fires for a run we watched go in flight.
  if (watchedRun && state?.status === "running") {
    const last = loadLast();
    const finishedThisRun =
      last &&
      last.jobPostingId === watchedRun.jobPostingId &&
      Date.parse(last.savedAt) >= Date.parse(watchedRun.startedAt);
    if (finishedThisRun && last) {
      emit(doneState(last.resumeId, last.versionId));
    } else {
      emit(null);
    }
    watchedRun = null;
  }
  schedule();
}

/** Dismiss a finished or failed pill. Running pills are not dismissible. */
export function dismissProcess() {
  if (state && state.status !== "running") emit(null);
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  if (!ticking) {
    ticking = true;
    void tick();
  }
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0 && timer) {
      clearTimeout(timer);
      timer = null;
      ticking = false;
    }
  };
}

function getSnapshot(): RunningProcess | null {
  return state;
}

function getServerSnapshot(): RunningProcess | null {
  return null;
}

/** Subscribe a component to the current running process (or null). */
export function useRunningProcess(): RunningProcess | null {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}
