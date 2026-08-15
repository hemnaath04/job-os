"use client";

import * as Dialog from "@radix-ui/react-dialog";
import { Search } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { FLAT_NAV } from "./_nav";

export function DocsSearch() {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        setOpen((v) => !v);
      }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, []);

  useEffect(() => {
    if (!open) setQuery("");
  }, [open]);

  const q = query.trim().toLowerCase();
  const results = q
    ? FLAT_NAV.filter(
        (item) =>
          item.title.toLowerCase().includes(q) || item.description.toLowerCase().includes(q),
      )
    : FLAT_NAV;

  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <Dialog.Trigger asChild>
        <button
          type="button"
          className="flex h-9 w-full items-center gap-2 rounded-lg border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] px-3 text-sm text-[color:var(--color-text-dim)] transition hover:border-[color:var(--color-border-strong)]"
        >
          <Search className="size-3.5" aria-hidden="true" />
          Search docs
          <kbd className="ml-auto rounded border border-[color:var(--color-border)] bg-[color:var(--color-surface-1)] px-1.5 py-0.5 font-mono text-[10px]">
            &#8984;K
          </kbd>
        </button>
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/50 backdrop-blur-sm data-[state=open]:animate-in data-[state=open]:fade-in-0" />
        <Dialog.Content className="fixed left-1/2 top-24 z-50 w-full max-w-lg -translate-x-1/2">
          <Dialog.Title className="sr-only">Search docs</Dialog.Title>
          <div className="glass overflow-hidden rounded-[var(--radius-card-lg)] border border-[color:var(--color-border)] bg-[color:var(--color-surface-1)] shadow-2xl">
            <div className="flex items-center gap-2 border-b border-[color:var(--color-border)] px-4 py-3">
              <Search className="size-4 text-[color:var(--color-text-dim)]" aria-hidden="true" />
              <input
                autoFocus
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search docs..."
                className="w-full bg-transparent text-sm outline-none placeholder:text-[color:var(--color-text-dim)]"
              />
            </div>
            <ul className="max-h-80 overflow-y-auto p-2">
              {results.length === 0 && (
                <li className="px-3 py-6 text-center text-sm text-[color:var(--color-text-dim)]">
                  No pages match &ldquo;{query}&rdquo;.
                </li>
              )}
              {results.map((item) => (
                <li key={item.href}>
                  <Link
                    href={item.href as never}
                    onClick={() => setOpen(false)}
                    className="block rounded-lg px-3 py-2 transition hover:bg-[color:var(--color-surface-2)]"
                  >
                    <p className="text-sm font-medium text-[color:var(--color-text)]">
                      {item.title}
                    </p>
                    <p className="truncate text-xs text-[color:var(--color-text-muted)]">
                      {item.description}
                    </p>
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
