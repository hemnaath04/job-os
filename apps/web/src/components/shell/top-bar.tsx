"use client";

import { motion } from "framer-motion";
import { usePathname } from "next/navigation";
import { CommandPaletteTrigger } from "./command-palette";
import { SIDEBAR_NAV } from "./sidebar";

const FRIENDLY_TITLES: Record<string, string> = {
  "/dashboard": "Dashboard",
  "/applications": "Applications",
  "/tailor": "AI Resume Tailor",
  "/jobs": "Internship Finder",
  "/resumes": "Resumes",
  "/profile": "Profile",
  "/calendar": "Calendar",
  "/settings": "Settings",
};

export function TopBar() {
  const pathname = usePathname();
  const title =
    FRIENDLY_TITLES[pathname] ??
    SIDEBAR_NAV.find((n) => pathname.startsWith(n.href))?.label ??
    "job.os";

  return (
    <motion.div
      initial={{ opacity: 0, y: -6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25 }}
      className="sticky top-0 z-20 flex items-center justify-between border-b border-[color:var(--color-border)] bg-[color:var(--color-bg)]/70 px-6 py-3 backdrop-blur-xl"
    >
      <div className="flex items-center gap-2 text-sm">
        <span className="text-[color:var(--color-text-dim)]">job.os</span>
        <span className="text-[color:var(--color-text-dim)]">/</span>
        <span className="font-medium">{title}</span>
      </div>
      <CommandPaletteTrigger />
    </motion.div>
  );
}
