import { ArrowLeft, ArrowRight } from "lucide-react";
import Link from "next/link";
import type { NavItem } from "./_nav";

export function PageNav({ prev, next }: { prev?: NavItem; next?: NavItem }) {
  if (!prev && !next) return null;
  return (
    <div className="mt-12 flex items-stretch gap-3 border-t border-[color:var(--color-border)] pt-6">
      {prev && (
        <Link
          href={prev.href as never}
          className="group flex flex-1 flex-col gap-1 rounded-[var(--radius-card)] border border-[color:var(--color-border)] p-4 transition hover:border-[color:var(--color-border-strong)] hover:bg-[color:var(--color-surface-2)]"
        >
          <span className="flex items-center gap-1.5 text-xs text-[color:var(--color-text-dim)]">
            <ArrowLeft className="size-3" aria-hidden="true" /> Previous
          </span>
          <span className="text-sm font-medium text-[color:var(--color-text)]">{prev.title}</span>
        </Link>
      )}
      {next && (
        <Link
          href={next.href as never}
          className="group flex flex-1 flex-col items-end gap-1 rounded-[var(--radius-card)] border border-[color:var(--color-border)] p-4 text-right transition hover:border-[color:var(--color-border-strong)] hover:bg-[color:var(--color-surface-2)]"
        >
          <span className="flex items-center gap-1.5 text-xs text-[color:var(--color-text-dim)]">
            Next <ArrowRight className="size-3" aria-hidden="true" />
          </span>
          <span className="text-sm font-medium text-[color:var(--color-text)]">{next.title}</span>
        </Link>
      )}
    </div>
  );
}
