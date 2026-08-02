"use client";

import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { ArrowRight, Menu, X } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { BrandMark } from "@/components/brand-mark";
import { ThemeToggle } from "@/components/shell/theme-toggle";

const LINKS = [
  { href: "#how", label: "How it works" },
  { href: "#honest", label: "Why it stays honest" },
  { href: "https://github.com/hemnaath04/job-os", label: "GitHub", external: true },
];

/**
 * The marketing header.
 *
 * A client component only because of the mobile menu; the page around it stays
 * a server component so the signed-in redirect still happens before any HTML
 * is sent.
 */
export function MarketingNav() {
  const [open, setOpen] = useState(false);
  const reduceMotion = useReducedMotion();

  return (
    <header className="fixed inset-x-0 top-0 z-50 border-b border-[color:var(--color-border)] bg-[color:var(--color-bg)]/75 backdrop-blur-xl">
      <nav aria-label="Primary" className="mx-auto max-w-6xl px-6">
        <div className="relative flex h-16 items-center justify-between">
          <Link href="/" className="flex items-center gap-2.5">
            <BrandMark className="drop-shadow-[0_12px_16px_rgba(255,231,135,.28)]" />
            <span className="font-mono text-sm tracking-tight">job.os</span>
          </Link>

          {/* Centred independently of the flex row so the logo and actions can
              be different widths without pulling the links off centre. */}
          <div className="absolute left-1/2 hidden -translate-x-1/2 items-center gap-8 md:flex">
            {LINKS.map((link) => (
              <a
                key={link.href}
                href={link.href}
                {...(link.external
                  ? { target: "_blank", rel: "noreferrer" }
                  : {})}
                className="text-sm text-[color:var(--color-text-muted)] transition-colors hover:text-[color:var(--color-text)]"
              >
                {link.label}
              </a>
            ))}
          </div>

          <div className="hidden items-center gap-2 md:flex">
            <ThemeToggle />
            <Link
              href="/sign-in"
              className="rounded-full px-4 py-1.5 text-sm text-[color:var(--color-text-muted)] transition hover:bg-[color:var(--color-surface-2)] hover:text-[color:var(--color-text)]"
            >
              Sign in
            </Link>
            <Link
              href="/sign-up"
              className="bg-gradient-brand inline-flex items-center gap-1.5 rounded-full px-4 py-1.5 text-sm font-semibold text-[color:var(--color-on-accent)] transition hover:scale-[1.02] active:scale-[.97]"
            >
              Get started <ArrowRight className="size-3.5" />
            </Link>
          </div>

          <div className="flex items-center gap-1.5 md:hidden">
            <ThemeToggle />
            <button
              type="button"
              onClick={() => setOpen((v) => !v)}
              className="-mr-2 p-2 text-[color:var(--color-text)]"
              aria-label={open ? "Close menu" : "Open menu"}
              aria-expanded={open}
            >
              {open ? <X className="size-5" /> : <Menu className="size-5" />}
            </button>
          </div>
        </div>
      </nav>

      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            key="mobile-menu"
            initial={reduceMotion ? false : { opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.22, ease: [0.22, 1, 0.36, 1] }}
            className="overflow-hidden border-t border-[color:var(--color-border)] bg-[color:var(--color-bg)]/95 backdrop-blur-xl md:hidden"
          >
            <div className="flex flex-col gap-1 px-6 py-4">
              {LINKS.map((link) => (
                <a
                  key={link.href}
                  href={link.href}
                  {...(link.external
                    ? { target: "_blank", rel: "noreferrer" }
                    : {})}
                  onClick={() => setOpen(false)}
                  className="py-2 text-sm text-[color:var(--color-text-muted)] transition-colors hover:text-[color:var(--color-text)]"
                >
                  {link.label}
                </a>
              ))}
              <div className="mt-3 flex flex-col gap-2 border-t border-[color:var(--color-border)] pt-4">
                <Link
                  href="/sign-in"
                  onClick={() => setOpen(false)}
                  className="rounded-full border border-[color:var(--color-border-strong)] px-4 py-2 text-center text-sm"
                >
                  Sign in
                </Link>
                <Link
                  href="/sign-up"
                  onClick={() => setOpen(false)}
                  className="bg-gradient-brand rounded-full px-4 py-2 text-center text-sm font-semibold text-[color:var(--color-on-accent)]"
                >
                  Get started
                </Link>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </header>
  );
}
