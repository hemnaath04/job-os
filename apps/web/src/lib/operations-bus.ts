"use client";

/**
 * A tiny one-way channel from the Appwrite data layer to the operations store.
 *
 * createAgentJob (lib/appwrite/workspace.ts) calls registerAgentOperation the
 * moment it queues any agent job, so every long-running operation is tracked by
 * the single code path that creates them all, not one flow at a time. This
 * indirection exists so workspace.ts never imports the store (and the React tree
 * it pulls in): the store attaches a handler here, the data layer just announces.
 */

export type RegisteredOperation = {
  /** Appwrite agent job id. */
  id: string;
  /** AgentJobKind, e.g. "resume_tailor". A string here to avoid a workspace import. */
  kind: string;
  /** The job input, used to work out where the finished result lives. */
  input: Record<string, unknown>;
};

type Handler = (op: RegisteredOperation) => void;

let handler: Handler | null = null;
// Registrations that arrive before the store has attached (e.g. a job queued in
// the same tick the app first mounts) wait here rather than being dropped.
const buffered: RegisteredOperation[] = [];

/** The store calls this once to receive every future and buffered registration. */
export function setOperationHandler(next: Handler | null): void {
  handler = next;
  if (next) {
    while (buffered.length > 0) next(buffered.shift() as RegisteredOperation);
  }
}

/** Called by the data layer the moment an agent job is queued. */
export function registerAgentOperation(op: RegisteredOperation): void {
  if (handler) handler(op);
  else buffered.push(op);
}
