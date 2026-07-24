"use client";

import { motion } from "framer-motion";
import { usePathname } from "next/navigation";
import { Toaster } from "sonner";
import { AuthProvider } from "@/components/auth-provider";
import { BackendWarmup } from "@/components/shell/backend-warmup";
import { CommandPalette } from "@/components/shell/command-palette";
import { TopProgressBar } from "@/components/shell/progress-bar";
import { Sidebar } from "@/components/shell/sidebar";
import { TopBar } from "@/components/shell/top-bar";
import { QueryProvider } from "@/lib/query";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  return (
    <AuthProvider>
      <QueryProvider>
        <BackendWarmup />
        <TopProgressBar />
        <div className="flex min-h-screen">
          <Sidebar />
          <main className="flex min-h-screen flex-1 flex-col">
            <TopBar />
            {/* Mount-only fade. We avoid AnimatePresence/mode="wait" here because
                under Next 15 + React 19 client nav it sometimes blocks the new
                page from mounting until the previous exit animation "completes",
                which leaves the main area visually blank until a hard reload. */}
            <motion.div
              key={pathname}
              initial={{ opacity: 0, y: 6 }}
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
              background: "color-mix(in oklch, #1A1A24 90%, transparent)",
              backdropFilter: "blur(20px)",
              border: "1px solid rgba(255,255,255,0.08)",
              color: "#F5F5FA",
            },
          }}
        />
      </QueryProvider>
    </AuthProvider>
  );
}
