"use client";

import { useSyncExternalStore } from "react";
import type { QueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { reportFailure } from "@/lib/errors";
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
export function startEnrich(
  jobId: string,
  text: string,
  queryClient: QueryClient,
): void {
  if (!markRunning(jobId)) return;

  void (async () => {
    try {
      const result = await api.addJobDescription(jobId, text);
      // The draft only goes once the row actually has it, so a failure leaves
      // the paste recoverable instead of making the person find it again.
      clearDraft(jobId);
      await queryClient.invalidateQueries({ queryKey: ["applications"] });
      if (result.filled.length > 0) {
        toast.success(`Description saved, and it filled in ${result.filled.join(", ")}`);
      } else if (result.parse_used) {
        toast.success("Description saved, and the match has been rescored");
      } else {
        // Not a success dressed up as more than it was: the text is stored and
        // useful to the tailor, but nothing could be read out of it, so the
        // match stays honestly unavailable.
        toast.success("Description saved, but no details could be read from it");
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
