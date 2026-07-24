"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { api } from "@/lib/api";

/**
 * Wake the Render free-tier container the moment the signed-in shell mounts,
 * and pre-populate the TanStack cache for the pages the user is most likely
 * to visit next.
 *
 * Why this matters: Render's hobby plan sleeps the API after 15 min idle.
 * Without warmup, the first click on any signed-in route eats a 30-60s
 * cold-start wait. With warmup, the wake happens in the background while
 * you're reading the Dashboard, so by the time you click Applications the
 * container is awake and the data is already in cache.
 *
 * Fire-and-forget — errors are intentionally swallowed; the pages will
 * fetch themselves if this happens to miss.
 */
export function BackendWarmup() {
  const qc = useQueryClient();

  useEffect(() => {
    let cancelled = false;

    async function warmAndPrefetch() {
      // Wake the cheap, unauthenticated /health route first. This avoids
      // making three authenticated requests compete while Render is starting.
      await fetch("/api/backend/health", { cache: "no-store" }).catch(() => {});
      if (cancelled) return;

      // Prefetch the queries most pages depend on. TanStack dedupes these
      // against any matching query already mounted by the active page.
      const prefetches: Array<[string[], () => Promise<unknown>]> = [
        [["applications"], () => api.listApplications()],
        [["me", "settings"], () => api.getSettings()],
        [["resumes"], () => api.listResumes()],
      ];
      await Promise.allSettled(
        prefetches.map(([queryKey, queryFn]) =>
          qc.prefetchQuery({ queryKey, queryFn }),
        ),
      );
    }

    void warmAndPrefetch();
    return () => {
      cancelled = true;
    };
  }, [qc]);

  return null;
}
