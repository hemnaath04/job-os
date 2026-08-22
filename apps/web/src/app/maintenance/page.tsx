"use client";

import { useEffect, useState } from "react";
import { BrandMark } from "@/components/brand-mark";

/** How often to quietly check whether maintenance mode has been switched
 * off, so a visitor who leaves the tab open gets back in on their own
 * rather than having to remember to refresh. */
const RECHECK_INTERVAL_MS = 15_000;

export default function MaintenancePage() {
  const [checking, setChecking] = useState(false);

  useEffect(() => {
    const interval = window.setInterval(async () => {
      setChecking(true);
      try {
        // Any same-origin request middleware sees: once maintenanceMode is
        // off, this 200s instead of redirecting back here, which is the
        // signal to reload for real.
        const response = await fetch("/maintenance", {
          method: "HEAD",
          cache: "no-store",
          redirect: "manual",
        });
        // A manual redirect() with `redirect: "manual"` reports as an
        // opaqueredirect response and status 0 -- so a real 200 here is
        // specifically "the middleware let this request through," meaning
        // it stopped rewriting to this same page.
        if (response.type !== "opaqueredirect" && response.ok) {
          window.location.href = "/";
        }
      } catch {
        // A network blip is not news; the next tick tries again.
      } finally {
        setChecking(false);
      }
    }, RECHECK_INTERVAL_MS);
    return () => window.clearInterval(interval);
  }, []);

  return (
    <main className="flex min-h-screen items-center justify-center p-6">
      <div className="w-full max-w-md rounded-[1.25rem] border border-[color:var(--color-border)] bg-[color:var(--color-surface-1)] px-8 py-12 text-center">
        <div className="flex justify-center">
          <span
            aria-hidden="true"
            className="relative flex size-7"
          >
            <span
              className="absolute inline-flex size-full rounded-full bg-[color:var(--color-accent)] opacity-60 motion-safe:animate-ping"
              style={{ animationDuration: "2.4s" }}
            />
            <BrandMark className="relative size-7" />
          </span>
        </div>

        <h1 className="mt-6 text-xl font-medium tracking-[-0.02em] text-[color:var(--color-text)]">
          We&rsquo;re deploying an update
        </h1>
        <p className="mt-3 text-sm leading-6 text-[color:var(--color-text-muted)]">
          job.os is briefly offline while a change ships. This page checks
          again on its own every few seconds, so you don&rsquo;t need to keep
          refreshing. Check back in a moment.
        </p>

        <p
          className="mt-8 text-xs text-[color:var(--color-text-dim)]"
          aria-live="polite"
        >
          {checking ? "Checking…" : "Waiting"}
        </p>
      </div>
    </main>
  );
}
