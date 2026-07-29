"use client";

import { Command } from "cmdk";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { Plus, Radar, Search, Sparkles, UserRound } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { SIDEBAR_NAV } from "./sidebar";

const ACTIONS = [
  { id: "add-job", label: "Add job from URL", action: "/applications?add=1", icon: Plus },
  { id: "tailor", label: "Tailor a resume", action: "/tailor", icon: Sparkles },
  { id: "find-jobs", label: "Find internships", action: "/jobs", icon: Radar },
  { id: "profile", label: "Edit profile", action: "/profile", icon: UserRound },
] as const;

export function CommandPalette() {
  const router = useRouter();
  const reduceMotion = useReducedMotion();
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const isToggle =
        (e.key === "k" || e.key === "K") && (e.metaKey || e.ctrlKey);
      if (isToggle) {
        e.preventDefault();
        setOpen((o) => !o);
      } else if (e.key === "Escape") {
        setOpen(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // The palette is a modal, so the rest of the app has to stop being reachable
  // while it is up: `inert` takes the shell out of the tab order and the
  // accessibility tree, and focus goes back to whatever opened the palette.
  useEffect(() => {
    if (!open) return;
    const shell = document.getElementById("app-shell");
    const opener = document.activeElement as HTMLElement | null;
    shell?.setAttribute("inert", "");
    return () => {
      shell?.removeAttribute("inert");
      opener?.focus?.();
    };
  }, [open]);

  function go(href: string) {
    setOpen(false);
    router.push(href);
  }

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          initial={reduceMotion ? false : { opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.15 }}
          className="fixed inset-0 z-50 flex items-start justify-center bg-black/40 backdrop-blur-sm px-4 pt-[15vh]"
          onClick={() => setOpen(false)}
        >
          <motion.div
            initial={reduceMotion ? false : { opacity: 0, y: -8, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -8, scale: 0.98 }}
            transition={{ duration: 0.18 }}
            onClick={(e) => e.stopPropagation()}
            role="dialog"
            aria-modal="true"
            aria-label="Command palette"
            className="w-full max-w-xl overflow-hidden rounded-2xl border border-[color:var(--color-border-strong)] bg-[color:var(--color-surface-1)]/95 shadow-[0_30px_80px_-20px_rgba(0,0,0,0.6)] backdrop-blur-2xl"
          >
            <Command label="Global command palette" className="text-sm">
              <div className="flex items-center gap-2 border-b border-[color:var(--color-border)] px-4 py-3">
                <Search className="size-4 text-[color:var(--color-text-dim)]" />
                <Command.Input
                  autoFocus
                  placeholder="Search applications, jump to a page, run an action…"
                  className="flex-1 bg-transparent outline-none placeholder:text-[color:var(--color-text-dim)]"
                />
                <kbd className="rounded-md border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] px-1.5 py-0.5 font-mono text-[10px] text-[color:var(--color-text-dim)]">
                  ESC
                </kbd>
              </div>
              <Command.List className="max-h-80 overflow-y-auto overscroll-contain p-2">
                <Command.Empty className="px-3 py-6 text-center text-xs text-[color:var(--color-text-dim)]">
                  No matches. Try another query.
                </Command.Empty>

                <Command.Group
                  heading="Pages"
                  className="text-[10px] uppercase tracking-wider text-[color:var(--color-text-dim)] [&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1"
                >
                  {SIDEBAR_NAV.map((item) => (
                    <Command.Item
                      key={item.href}
                      value={`page ${item.label}`}
                      onSelect={() => go(item.href)}
                      className="flex cursor-pointer items-center gap-2 rounded-lg px-2.5 py-2 text-sm text-[color:var(--color-text-muted)] data-[selected=true]:bg-[color:var(--color-surface-hover)] data-[selected=true]:text-[color:var(--color-text)]"
                    >
                      <item.icon className="size-4 text-[color:var(--color-violet)]" />
                      <span>{item.label}</span>
                    </Command.Item>
                  ))}
                </Command.Group>

                <Command.Group
                  heading="Actions"
                  className="mt-1 text-[10px] uppercase tracking-wider text-[color:var(--color-text-dim)] [&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1"
                >
                  {ACTIONS.map((a) => {
                    const Icon = a.icon;
                    return (
                      <Command.Item
                        key={a.id}
                        value={`action ${a.label}`}
                        onSelect={() => go(a.action)}
                        className="flex cursor-pointer items-center gap-2 rounded-lg px-2.5 py-2 text-sm text-[color:var(--color-text-muted)] data-[selected=true]:bg-[color:var(--color-surface-hover)] data-[selected=true]:text-[color:var(--color-text)]"
                      >
                        <Icon className="size-3.5 text-[color:var(--color-cyan)]" />
                        <span>{a.label}</span>
                      </Command.Item>
                    );
                  })}
                </Command.Group>
              </Command.List>
            </Command>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

export function CommandPaletteTrigger() {
  return (
    <button
      type="button"
      onClick={() => {
        const evt = new KeyboardEvent("keydown", {
          key: "k",
          metaKey: true,
          ctrlKey: true,
        });
        window.dispatchEvent(evt);
      }}
      className="inline-flex items-center gap-2 rounded-lg border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] px-3 py-1.5 text-xs text-[color:var(--color-text-muted)] transition hover:bg-[color:var(--color-surface-hover)] hover:text-[color:var(--color-text)] active:scale-[.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-kiwi)]"
    >
      <Search className="size-3.5" />
      <span>Search or jump</span>
      <kbd className="ml-2 rounded border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] px-1 py-0.5 font-mono text-[10px]">
        ⌘K
      </kbd>
    </button>
  );
}
