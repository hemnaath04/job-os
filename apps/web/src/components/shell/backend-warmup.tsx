"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { api } from "@/lib/api";

/**
 * Wake the API when the signed-in shell mounts, then fill low-priority caches
 * only after the backend is responsive and the browser is idle.
 *
 * Why this matters: Render's hobby plan sleeps the API after 15 min idle.
 * Starting every authenticated request during a cold boot creates a thundering
 * herd and makes the first screen compete with speculative work. The active
 * page owns its primary query; this component only warms likely next routes.
 */
export function BackendWarmup() {
  const qc = useQueryClient();

  useEffect(() => {
    let cancelled = false;
    let idleId: number | undefined;
    let timerId: ReturnType<typeof setTimeout> | undefined;

    const prefetch = () => {
      if (cancelled) return;
      void Promise.allSettled([
        qc.prefetchQuery({
          queryKey: ["settings"],
          queryFn: () => api.getSettings(),
        }),
        qc.prefetchQuery({
          queryKey: ["resumes"],
          queryFn: () => api.listResumes(),
        }),
      ]);
    };

    void fetch("/api/backend/health", { cache: "no-store" })
      .catch(() => undefined)
      .finally(() => {
        if (cancelled) return;
        if ("requestIdleCallback" in window) {
          idleId = window.requestIdleCallback(prefetch, { timeout: 4_000 });
        } else {
          timerId = setTimeout(prefetch, 1_500);
        }
      });

    return () => {
      cancelled = true;
      if (idleId !== undefined && "cancelIdleCallback" in window) {
        window.cancelIdleCallback(idleId);
      }
      if (timerId !== undefined) clearTimeout(timerId);
    };
  }, [qc]);

  return null;
}
