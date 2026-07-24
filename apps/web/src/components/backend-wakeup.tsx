"use client";

import { useEffect } from "react";

/**
 * Start the API while a visitor is reading the landing or auth page so the
 * signed-in dashboard does not pay Render's cold-start cost.
 */
export function BackendWakeup() {
  useEffect(() => {
    fetch("/api/backend/health", { cache: "no-store" }).catch(() => {});
  }, []);

  return null;
}
