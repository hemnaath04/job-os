"use client";

import { motion, useReducedMotion } from "framer-motion";
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
      <BackendWarmup />
      <TopProgressBar />
      <div className="flex min-h-[100dvh] pb-16 lg:pb-0">
        <Sidebar />
        <main className="flex min-h-[100dvh] min-w-0 flex-1 flex-col">
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
        theme="dark"
        position="bottom-right"
        toastOptions={{
          style: {
            background: "color-mix(in oklch, #1B1F25 94%, transparent)",
            backdropFilter: "blur(20px)",
            border: "1px solid rgba(255,255,255,0.08)",
            color: "#F1EEE8",
          },
        }}
      />
    </QueryProvider>
  );
}
