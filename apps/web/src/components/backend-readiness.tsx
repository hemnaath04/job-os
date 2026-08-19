"use client";

import { useEffect } from "react";
import { isAppwriteInteractiveBackendEnabled } from "@/lib/appwrite/config";

/**
 * Wake both the API and Neon compute while the user is on the
 * landing or authentication screen, before the dashboard needs data.
 */
export function BackendReadiness() {
  useEffect(() => {
    if (isAppwriteInteractiveBackendEnabled) return;
    fetch("/api/backend/health/ready", { cache: "no-store" }).catch(() => {});
  }, []);

  return null;
}
