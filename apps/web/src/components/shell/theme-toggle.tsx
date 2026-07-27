"use client";

import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { Moon, Sun } from "lucide-react";
import { useEffect, useState } from "react";

/**
 * Light/dark toggle. The initial class is set by an inline no-FOUC script in
 * the root layout; this component only reflects and flips it, persisting the
 * choice to localStorage so it survives reloads and sessions.
 */
export function ThemeToggle() {
  const [dark, setDark] = useState(false);
  const [mounted, setMounted] = useState(false);
  const reduce = useReducedMotion();

  useEffect(() => {
    setDark(document.documentElement.classList.contains("dark"));
    setMounted(true);
    // Keep already-open tabs in sync when the theme changes elsewhere.
    const onStorage = (e: StorageEvent) => {
      if (e.key !== "theme") return;
      const d = e.newValue === "dark";
      document.documentElement.classList.toggle("dark", d);
      setDark(d);
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  function toggle() {
    const next = !dark;
    setDark(next);
    document.documentElement.classList.toggle("dark", next);
    try {
      localStorage.setItem("theme", next ? "dark" : "light");
    } catch {
      /* storage may be unavailable; class still applied for this session */
    }
  }

  const Icon = dark ? Sun : Moon;

  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={dark ? "Switch to light mode" : "Switch to dark mode"}
      title={dark ? "Light mode" : "Dark mode"}
      className="relative inline-flex size-9 items-center justify-center rounded-full border border-[color:var(--color-border)] bg-[color:var(--color-surface-1)] text-[color:var(--color-text-muted)] transition-colors duration-150 hover:border-[color:var(--color-accent-border)] hover:text-[color:var(--color-text)]"
    >
      <AnimatePresence mode="wait" initial={false}>
        <motion.span
          key={mounted ? (dark ? "sun" : "moon") : "placeholder"}
          initial={reduce ? false : { rotate: -35, opacity: 0, scale: 0.8 }}
          animate={{ rotate: 0, opacity: 1, scale: 1 }}
          exit={reduce ? undefined : { rotate: 35, opacity: 0, scale: 0.8 }}
          transition={{ duration: 0.18, ease: [0.22, 1, 0.36, 1] }}
          className="inline-flex"
        >
          <Icon className="size-[18px]" />
        </motion.span>
      </AnimatePresence>
    </button>
  );
}
