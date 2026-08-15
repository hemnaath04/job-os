import Link from "next/link";
import type { ReactNode } from "react";
import { BrandMark } from "@/components/brand-mark";
import { DocsSidebar } from "./_sidebar";

export default function DocsLayout({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-30 border-b border-[color:var(--color-border)] bg-[color:var(--color-bg)]/85 backdrop-blur-xl">
        <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-6">
          <Link href="/" className="flex items-center gap-2.5">
            <BrandMark className="size-6" />
            <span className="font-mono text-sm tracking-tight">job.os</span>
            <span className="ml-1 rounded-full border border-[color:var(--color-border)] px-2 py-0.5 text-[11px] text-[color:var(--color-text-dim)]">
              docs
            </span>
          </Link>
          <div className="flex items-center gap-4 text-sm">
            <a
              href="https://github.com/hemnaath04/job-os"
              target="_blank"
              rel="noreferrer"
              className="text-[color:var(--color-text-muted)] transition-colors hover:text-[color:var(--color-text)]"
            >
              GitHub
            </a>
            <Link
              href="/dashboard"
              className="bg-gradient-brand inline-flex items-center rounded-full px-4 py-1.5 text-sm font-semibold text-[color:var(--color-on-accent)] transition hover:scale-[1.02] active:scale-[.97]"
            >
              Open app
            </Link>
          </div>
        </div>
      </header>

      <div className="mx-auto flex max-w-6xl gap-10 px-6 py-10">
        <DocsSidebar />
        {children}
      </div>
    </div>
  );
}
