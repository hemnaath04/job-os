"use client";

import { Moon, Sun } from "lucide-react";
import { useEffect, useState } from "react";

/**
 * Light and dark switch.
 *
 * The palette for both themes already exists in globals.css and flips on a
 * `dark` class on <html>, and the inline script in the root layout applies the
 * stored choice before first paint. Until now nothing could set it, so the dark
 * theme was unreachable. This is that missing control.
 *
 * State is read from the DOM on mount rather than from localStorage, because
 * the pre-paint script is the thing that decided what is actually on screen and
 * reading the class cannot disagree with it. Rendering `false` on the server and
 * correcting after mount is deliberate too: the alternative is reading
 * localStorage during render, which does not exist on the server and produces a
 * hydration mismatch. The worst case here is one frame of the wrong icon, with
 * no layout shift behind it.
 */
export function ThemeToggle({ className = "" }: { className?: string }) {
  const [dark, setDark] = useState(false);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setDark(document.documentElement.classList.contains("dark"));
    setMounted(true);
  }, []);

  function toggle() {
    const next = !dark;
    setDark(next);
    document.documentElement.classList.toggle("dark", next);
    try {
      localStorage.setItem("theme", next ? "dark" : "light");
    } catch {
      // Private mode or a blocked store. The theme still applies for this
      // session, it just will not be remembered, which is not worth failing on.
    }
  }

  const label = dark ? "Switch to light theme" : "Switch to dark theme";

  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={label}
      title={label}
      aria-pressed={dark}
      className={
        "inline-flex size-9 shrink-0 items-center justify-center rounded-lg border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] text-[color:var(--color-text-muted)] transition hover:bg-[color:var(--color-surface-hover)] hover:text-[color:var(--color-text)] active:scale-[.96] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-accent-ink)] " +
        className
      }
    >
      {/* Before mount the icon would be a guess, so render neither rather than
          flash the wrong one. The button keeps its size either way. */}
      {mounted ? (
        dark ? (
          <Sun className="size-4" aria-hidden="true" />
        ) : (
          <Moon className="size-4" aria-hidden="true" />
        )
      ) : (
        <span className="size-4" aria-hidden="true" />
      )}
    </button>
  );
}
