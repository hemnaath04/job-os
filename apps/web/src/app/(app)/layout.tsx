"use client";

import { MotionConfig, motion, useReducedMotion } from "framer-motion";
import { usePathname } from "next/navigation";
import { Toaster } from "sonner";
import { BackendWarmup } from "@/components/shell/backend-warmup";
import { CommandPalette } from "@/components/shell/command-palette";
import { TopProgressBar } from "@/components/shell/progress-bar";
import { Sidebar } from "@/components/shell/sidebar";
import { TopBar } from "@/components/shell/top-bar";
import { QueryProvider } from "@/lib/query";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const reduceMotion = useReducedMotion();
  return (
    <QueryProvider>
      {/* One authority for reduced motion: every framer-motion animation below
          respects the OS preference, matching what globals.css does for CSS. */}
      <MotionConfig reducedMotion="user">
        <BackendWarmup />
        <TopProgressBar />
        {/* First focusable element on the page, so keyboard users can jump the
            sidebar instead of tabbing through it on every navigation. */}
        <a
          href="#main-content"
          className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-[100] focus:rounded-[var(--radius-control)] focus:border focus:border-[color:var(--color-accent-border)] focus:bg-[color:var(--color-surface-1)] focus:px-4 focus:py-2.5 focus:text-sm focus:font-semibold focus:text-[color:var(--color-text)] focus:shadow-[var(--shadow-glass)]"
        >
          Skip to content
        </a>
        <div id="app-shell" className="flex min-h-[100dvh] pb-16 lg:pb-0">
          <Sidebar />
          <main
            id="main-content"
            tabIndex={-1}
            className="flex min-h-[100dvh] min-w-0 flex-1 flex-col"
          >
            <TopBar />
            {/* Mount-only fade. We avoid AnimatePresence/mode="wait" here because
                under Next 15 + React 19 client nav it sometimes blocks the new
                page from mounting until the previous exit animation "completes",
                which leaves the main area visually blank until a hard reload. */}
            <motion.div
              key={pathname}
              initial={reduceMotion ? false : { opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.15 }}
              className="flex-1"
            >
              {children}
            </motion.div>
          </main>
        </div>
        <CommandPalette />
        <Toaster
          position="bottom-right"
          toastOptions={{
            style: {
              background: "var(--color-surface-1)",
              backdropFilter: "blur(20px)",
              border: "1px solid var(--color-border)",
              color: "var(--color-text)",
              boxShadow: "var(--shadow-glass)",
            },
          }}
        />
      </MotionConfig>
    </QueryProvider>
  );
}
