"use client";

import { useSyncExternalStore } from "react";
import type { QueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { isAppwritePipelineEnabled } from "@/lib/appwrite/config";
import { reportFailure } from "@/lib/errors";
import type { Application } from "@/lib/types";
import {
  clearDraft,
  getVersion,
  isEnriching,
  markRunning,
  markSettled,
  readDraft,
  subscribe,
} from "@/lib/pending-enrich-store";

/**
 * Pasting a description outlives the panel it was typed into.
 *
 * The enrich runs for ten to twenty seconds, because it is a real model call,
 * and the inspector that starts it unmounts the moment the user selects another
 * application or leaves the page. The request itself was never the casualty:
 * `fetchJson` passes no AbortSignal, so the fetch completes and the row is
 * written whatever the UI does. What was lost was everything the person could
 * see. The "Saving" state vanished, the typed text went with it, and coming
 * back showed an empty box with no sign anything had happened.
 *
 * This module owns the request so that nothing about it depends on a component
 * still being mounted; `pending-enrich-store` owns the state it reports.
 */

export { clearDraft, setDraft } from "@/lib/pending-enrich-store";

/**
 * Fire the enrich and own it from here on.
 *
 * Deliberately not a `useMutation`. The toast is global, the invalidation runs
 * off the client handed in at kickoff, and the promise is held by this module,
 * so navigating away mid-flight changes nothing except what is on screen.
 * Returns immediately.
 */
/**
 * Apply a pasted description to the job, wherever that job actually lives.
 *
 * Two stores, and which one holds the job decides how this is written. The
 * Appwrite pipeline keeps applications as cards whose snapshot carries the
 * job, and a card created there has no Postgres row at all, so writing by row
 * id answers 404 for a job the person can plainly see. On that path the server
 * only plans the backfill and the browser writes the card, which is also the
 * only way the board reflects it: the applications list renders those cards,
 * so a Postgres write is invisible there however correct it is.
 */
async function applyEnrich(
  application: Application,
  text: string,
): Promise<{ filled: string[]; parseUsed: boolean }> {
  if (!isAppwritePipelineEnabled) {
    const result = await api.addJobDescription(application.job.id, text);
    return { filled: result.filled, parseUsed: result.parse_used };
  }

  const plan = await api.parseJobDescription(text, application.job);
  if (Object.keys(plan.updates).length > 0) {
    await api.patchApplication(application.id, {
      job: { ...application.job, ...plan.updates },
    });
  }
  return { filled: plan.filled, parseUsed: plan.parse_used };
}

export function startEnrich(
  application: Application,
  text: string,
  queryClient: QueryClient,
): void {
  const jobId = application.job.id;
  if (!markRunning(jobId)) return;

  void (async () => {
    try {
      const result = await applyEnrich(application, text);
      // The draft only goes once the row actually has it, so a failure leaves
      // the paste recoverable instead of making the person find it again.
      clearDraft(jobId);
      await queryClient.invalidateQueries({ queryKey: ["applications"] });
      if (result.filled.length > 0) {
        toast.success(`Read the description and filled in ${result.filled.join(", ")}`);
      } else if (result.parseUsed) {
        // Parsed fine and changed nothing, which is what a second paste on an
        // already-filled job looks like. Claiming a rescore here would be
        // claiming something that did not happen.
        toast.success("Read the description. Nothing needed updating");
      } else {
        // Not a success dressed up as more than it was: nothing could be read
        // out of it, so the match stays honestly unavailable.
        toast.success("Could not read any details from that description");
      }
    } catch (err) {
      reportFailure("save that description", err);
    } finally {
      markSettled(jobId);
    }
  })();
}

/** Subscribes a component to one job's paste state. */
export function usePendingEnrich(jobId: string): { running: boolean; draft: string } {
  // The version counter is the snapshot: useSyncExternalStore compares by
  // identity, so returning a fresh object here would re-render forever.
  useSyncExternalStore(subscribe, getVersion, () => 0);
  return { running: isEnriching(jobId), draft: readDraft(jobId) };
}
