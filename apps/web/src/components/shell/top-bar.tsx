"use client";

import { motion, useReducedMotion } from "framer-motion";
import { usePathname } from "next/navigation";
import { CommandPaletteTrigger } from "./command-palette";
import { SIDEBAR_NAV } from "./sidebar";
import { ThemeToggle } from "./theme-toggle";

const FRIENDLY_TITLES: Record<string, string> = {
  "/dashboard": "Dashboard",
  "/applications": "Applications",
  "/tailor": "AI Resume Tailor",
  "/jobs": "Job Finder",
  "/resumes": "Resumes",
  "/profile": "Profile",
  "/calendar": "Calendar",
  "/settings": "Settings",
};

export function TopBar() {
  const pathname = usePathname();
  const reduceMotion = useReducedMotion();
  const title =
    FRIENDLY_TITLES[pathname] ??
    SIDEBAR_NAV.find((n) => pathname.startsWith(n.href))?.label ??
    "job.os";
  const ActiveIcon =
    SIDEBAR_NAV.find((item) => pathname.startsWith(item.href))?.icon;

  return (
    <motion.div
      initial={reduceMotion ? false : { opacity: 0, y: -6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25 }}
      className="sticky top-0 z-20 flex items-center justify-between border-b border-[color:var(--color-border)] bg-[color:var(--color-bg)]/70 px-4 py-3 backdrop-blur-xl sm:px-6"
    >
      <div className="flex min-w-0 items-center gap-2 text-sm">
        {ActiveIcon && <ActiveIcon className="size-4 shrink-0 text-[color:var(--color-kiwi)]" />}
        <span className="truncate font-medium">{title}</span>
      </div>
      <div className="flex items-center gap-2">
        <CommandPaletteTrigger />
        <ThemeToggle />
      </div>
    </motion.div>
  );
}
