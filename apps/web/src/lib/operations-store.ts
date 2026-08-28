"use client";

import { useSyncExternalStore } from "react";
import { appwriteWorkspace } from "@/lib/appwrite/workspace";
import type { AgentJobKind } from "@/lib/appwrite/workspace";
import { setOperationHandler, type RegisteredOperation } from "@/lib/operations-bus";
import {
  isUnrecoverablePollError,
  MAX_AGE_MS,
  OPERATION_FAILURE,
  readAgentJob,
} from "@/lib/operation-outcome";

/**
 * A global, navigation-surviving view of every long-running agent job the app
 * runs: tailoring a resume (draft), an AI revision ("suggested edits"), a resume
 * import, a profile extract. They each run on the Appwrite agent for two to four
 * minutes, and the page that started one unmounts the moment the user navigates
 * away, taking its progress and its finished result with it.
 *
 * This store is what keeps them reachable. It is fed by a single hook in
 * createAgentJob (via operations-bus), so it tracks every job the data layer
 * creates rather than one flow at a time, polls the real agent job on a generous
 * cap, and persists to localStorage so a job still running through a full reload
 * is re-attached rather than lost. What it shows is the true server state: an
 * honest indeterminate running state until the agent reports a real stage and
 * fraction, then a done state that clicks through to the finished result, or a
 * failed state with a short reason. No fabricated progress.
 */

const STORAGE_KEY = "operations:v1";
// Mirror of the Tailor page's own restore keys. On a tailor completion this
// store writes the "last" pointer and clears the "active" one so returning to
// the Tailor page shows the finished result rather than a dead spinner. Kept in
// sync by value with app/(app)/tailor/page.tsx; rename there, rename here.
const TAILOR_ACTIVE_KEY = "tailor:active";
const TAILOR_LAST_KEY = "tailor:last";

// How long a finished pill stays reachable across a cold reload before it is
// pruned. Long enough to come back to a result later in the day.
const DONE_TTL_MS = 12 * 60 * 60 * 1_000;
const POLL_MS = 2_000;
// Cap what is kept and what is shown, so a long session cannot grow an endless
// stack. Newest wins.
const MAX_KEPT = 8;
const MAX_VISIBLE = 4;

type OpStatus = "running" | "done" | "failed";

/** The ids and hints a result href is built from, pulled off the job input. */
type OpInput = {
  resumeId?: string;
  versionId?: string;
  jobPostingId?: string;
  filename?: string;
};

export type Operation = {
  id: string;
  kind: AgentJobKind;
  status: OpStatus;
  /** The agent's current stage, e.g. "Composing". Null before it reports one. */
  stage: string | null;
  /**
   * One measured fact about the current stage, e.g. "6 of 9 already backed by
   * your profile". Null when the agent reports none.
   */
  detail: string | null;
  /** 0..1 real progress from the agent, or null when it has reported none. */
  pct: number | null;
  /** Where a click leads: the origin page while running, the result once done. */
  href: string;
  /** A short reason, on a failed op. */
  message?: string;
  input: OpInput;
  startedAt: string;
  finishedAt?: string;
};

type KindMeta = {
  running: string;
  done: string;
  doneDetail: string;
  failed: string;
  origin: (input: OpInput) => string;
};

const KIND_META: Record<string, KindMeta> = {
  resume_tailor: {
    running: "Tailoring your resume",
    done: "Tailored resume ready",
    doneDetail: "Open the finished resume",
    failed: "Tailoring did not finish",
    origin: () => "/tailor",
  },
  resume_revision: {
    running: "Preparing AI suggestions",
    done: "AI suggestions ready",
    doneDetail: "Open the suggestions",
    failed: "The AI edit did not finish",
    origin: (input) =>
      input.resumeId && input.versionId
        ? `/resumes/${input.resumeId}/${input.versionId}`
        : "/resumes",
  },
  resume_import: {
    running: "Importing your resume",
    done: "Resume imported",
    doneDetail: "Open it in your library",
    failed: "The import did not finish",
    origin: () => "/resumes",
  },
  profile_extract: {
    running: "Reading your resume",
    done: "Profile facts extracted",
    doneDetail: "Open your profile",
    failed: "Reading your resume did not finish",
    origin: () => "/profile",
  },
};

const FALLBACK_META: KindMeta = {
  running: "Working",
  done: "Ready",
  doneDetail: "Open the result",
  failed: "This did not finish",
  origin: () => "/dashboard",
};

export function metaForKind(kind: AgentJobKind): KindMeta {
  return KIND_META[kind] ?? FALLBACK_META;
}

/** The headline for an operation card, honest to its current status. */
export function operationTitle(op: Operation): string {
  const meta = metaForKind(op.kind);
  return op.status === "running" ? meta.running : op.status === "done" ? meta.done : meta.failed;
}

/** The subline for an operation card. */
export function operationDetail(op: Operation): string {
  const meta = metaForKind(op.kind);
  if (op.status === "running") {
    if (op.stage) return op.stage;
    if (op.input.filename) return op.input.filename;
    return "Working on the server. Safe to keep browsing.";
  }
  if (op.status === "done") return meta.doneDetail;
  return op.message ?? "Try again.";
}

function inputFrom(raw: Record<string, unknown>): OpInput {
  const str = (value: unknown) => (typeof value === "string" ? value : undefined);
  return {
    resumeId: str(raw.resume_id),
    versionId: str(raw.version_id),
    jobPostingId: str(raw.spawned_from_job_id),
    filename: str(raw.filename),
  };
}

/** Where a finished job's result lives, from its kind, input and output. */
function resultHref(kind: AgentJobKind, input: OpInput, output: unknown): string {
  if (kind === "resume_tailor") {
    const out = output as { resume_id?: string; id?: string } | null;
    if (out?.resume_id && out.id) return `/resumes/${out.resume_id}/${out.id}`;
  }
  if (kind === "resume_revision") {
    if (input.resumeId && input.versionId) {
      return `/resumes/${input.resumeId}/${input.versionId}`;
    }
  }
  if (kind === "resume_import") {
    const out = output as { resume?: { id?: string }; version?: { id?: string } } | null;
    if (out?.resume?.id && out.version?.id) {
      return `/resumes/${out.resume.id}/${out.version.id}`;
    }
    return "/resumes";
  }
  if (kind === "profile_extract") return "/profile";
  return metaForKind(kind).origin(input);
}

function clamp01(value: number | null | undefined): number | null {
  if (typeof value !== "number" || Number.isNaN(value)) return null;
  return Math.max(0, Math.min(1, value));
}

function nowIso(): string {
  return new Date().toISOString();
}

// ---- store internals --------------------------------------------------------

const EMPTY: Operation[] = [];

let ops: Operation[] = [];
let snapshot: Operation[] = EMPTY;
let initialized = false;
let ticking = false;
let timer: ReturnType<typeof setTimeout> | null = null;
const listeners = new Set<() => void>();

function recomputeSnapshot() {
  const now = Date.now();
  const visible = ops
    .filter((op) => {
      if (op.status === "running") return true;
      const finished = op.finishedAt ? Date.parse(op.finishedAt) : 0;
      return now - finished < DONE_TTL_MS;
    })
    .slice(0, MAX_VISIBLE);
  snapshot = visible.length > 0 ? visible : EMPTY;
}

function persist() {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(ops.slice(0, MAX_KEPT)));
  } catch {
    /* quota or private mode: the run still works, it just will not survive a reload */
  }
}

function emit() {
  recomputeSnapshot();
  persist();
  for (const listener of listeners) listener();
}

function updateOp(id: string, next: (op: Operation) => Operation) {
  let changed = false;
  ops = ops.map((op) => {
    if (op.id !== id) return op;
    const updated = next(op);
    if (updated !== op) changed = true;
    return updated;
  });
  if (changed) emit();
}

function markFailed(id: string, message: string) {
  updateOp(id, (op) => ({
    ...op,
    status: "failed",
    stage: null,
    pct: null,
    href: metaForKind(op.kind).origin(op.input),
    message,
    finishedAt: nowIso(),
  }));
}

// There is deliberately no "the user canceled this" path here. A page's own
// stop-watching control detaches that page and nothing else: it cannot abort a
// server run, the run goes on to finish, and marking the card failed both said
// something untrue and stopped the poll that would have delivered the result.
// See lib/operation-outcome.ts for the whole story. A card that is still
// running stays running until the server says otherwise or `MAX_AGE_MS`
// passes, and either way the sentence the user reads describes the run rather
// than blaming them for it.

// ---- tailor page hand-off (mirror of app/(app)/tailor/page.tsx) -------------

function writeJson(key: string, value: unknown) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* non-critical */
  }
}

function removeKey(key: string) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(key);
  } catch {
    /* non-critical */
  }
}

function handOffTailorResult(op: Operation, output: { resume_id?: string; id?: string }) {
  if (!output.resume_id || !output.id) return;
  // So Download resolves the stored PDF right away, and returning to the Tailor
  // page shows the finished result instead of re-attaching to a dead spinner.
  try {
    appwriteWorkspace.registerVersionFile(output as never);
  } catch {
    /* best effort */
  }
  if (op.input.jobPostingId) {
    writeJson(TAILOR_LAST_KEY, {
      resumeId: output.resume_id,
      versionId: output.id,
      jobPostingId: op.input.jobPostingId,
      savedAt: nowIso(),
    });
  }
  removeKey(TAILOR_ACTIVE_KEY);
}

// ---- polling ----------------------------------------------------------------

async function pollOne(op: Operation) {
  const age = Date.now() - Date.parse(op.startedAt);
  let job: Awaited<ReturnType<typeof appwriteWorkspace.getAgentJob>>;
  try {
    job = await appwriteWorkspace.getAgentJob(op.id);
  } catch (error) {
    const message = error instanceof Error ? error.message : "";
    if (isUnrecoverablePollError(message)) {
      markFailed(op.id, OPERATION_FAILURE.missing);
    }
    // Anything else is a transient blip, retried next tick up to MAX_AGE_MS.
    return;
  }
  // Every terminal verdict is made in one place, so the sentence the card
  // shows can never drift from the server state that produced it.
  const outcome = readAgentJob(job, age);
  if (outcome.kind === "failed") {
    markFailed(op.id, outcome.message);
    return;
  }
  if (outcome.kind === "done") {
    const href = resultHref(op.kind, op.input, job.output);
    if (op.kind === "resume_tailor") {
      handOffTailorResult(op, job.output as { resume_id?: string; id?: string });
    }
    updateOp(op.id, (current) => ({
      ...current,
      status: "done",
      stage: null,
      detail: null,
      pct: null,
      href,
      finishedAt: nowIso(),
    }));
    return;
  }
  const stage = job.progress?.stage ?? null;
  const detail = job.progress?.detail ?? null;
  const pct = clamp01(job.progress?.pct);
  updateOp(op.id, (current) =>
    current.stage === stage && current.detail === detail && current.pct === pct
      ? current
      : { ...current, stage, detail, pct },
  );
}

async function tick() {
  timer = null;
  const active = ops.filter((op) => op.status === "running");
  if (active.length === 0) {
    ticking = false;
    return;
  }
  await Promise.allSettled(active.map(pollOne));
  if (ops.some((op) => op.status === "running")) {
    schedule();
  } else {
    ticking = false;
  }
}

function schedule(delay = POLL_MS) {
  if (timer) clearTimeout(timer);
  timer = setTimeout(() => void tick(), delay);
}

function ensureTicking() {
  if (ticking) return;
  if (!ops.some((op) => op.status === "running")) return;
  ticking = true;
  schedule(0);
}

// ---- persistence / lifecycle ------------------------------------------------

function initFromStorage() {
  if (initialized) return;
  initialized = true;
  if (typeof window === "undefined") return;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    const parsed = raw ? (JSON.parse(raw) as Operation[]) : [];
    const now = Date.now();
    ops = parsed
      .filter((op) => op && op.id && op.kind && op.startedAt)
      .map((op) => {
        // Records written before `detail` existed have none, and the card reads
        // it directly, so it is normalised on the way in rather than guarded at
        // every use.
        const record = { ...op, detail: op.detail ?? null };
        if (record.status === "running") {
          const age = now - Date.parse(record.startedAt);
          if (!Number.isFinite(age) || age > MAX_AGE_MS) {
            return {
              ...record,
              status: "failed" as const,
              message: OPERATION_FAILURE.timedOut,
              finishedAt: nowIso(),
            };
          }
        }
        return record;
      })
      .filter((op) => {
        if (op.status === "running") return true;
        const finished = op.finishedAt ? Date.parse(op.finishedAt) : 0;
        return now - finished < DONE_TTL_MS;
      })
      .slice(0, MAX_KEPT);
    recomputeSnapshot();
  } catch {
    ops = [];
  }
}

function handleRegistered(registered: RegisteredOperation) {
  initFromStorage();
  const kind = registered.kind as AgentJobKind;
  const input = inputFrom(registered.input);
  const record: Operation = {
    id: registered.id,
    kind,
    status: "running",
    stage: null,
    detail: null,
    pct: null,
    href: metaForKind(kind).origin(input),
    input,
    startedAt: nowIso(),
  };
  ops = [record, ...ops.filter((op) => op.id !== record.id)].slice(0, MAX_KEPT);
  emit();
  ensureTicking();
}

// Attach at module load so a job queued before the indicator subscribes is still
// captured. Harmless during SSR: no agent job is created on the server.
setOperationHandler(handleRegistered);

/**
 * Stop showing a card. Says nothing about the run itself.
 *
 * Running cards are dismissible too, which is the honest version of what
 * `cancelOperation` used to provide. Removing a card from a list is not a
 * claim that anything failed or was canceled, so it can be offered for a
 * running job without writing down something untrue; the server run carries
 * on and the version it saves is still in the library. What is lost is only
 * the shortcut to it, which is the user's own choice to make.
 */
export function dismissOperation(id: string) {
  if (!ops.some((entry) => entry.id === id)) return;
  ops = ops.filter((entry) => entry.id !== id);
  emit();
}

function subscribe(listener: () => void): () => void {
  initFromStorage();
  listeners.add(listener);
  ensureTicking();
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0 && timer) {
      clearTimeout(timer);
      timer = null;
      ticking = false;
    }
  };
}

function getSnapshot(): Operation[] {
  return snapshot;
}

function getServerSnapshot(): Operation[] {
  return EMPTY;
}

/** Subscribe a component to the currently tracked operations (newest first). */
export function useOperations(): Operation[] {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}
