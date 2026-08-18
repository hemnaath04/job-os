import type { ReactNode } from "react";

type BadgeVariant = "accent" | "amber" | "muted";

// "muted" is intentionally text-only, no fill or ring: a badge for an absent
// value (e.g. "Not scored") should read as quieter than the data it stands
// in for, not as a same-weight chip competing with real matches.
const VARIANT_STYLES: Record<BadgeVariant, string> = {
  accent:
    "bg-[color:var(--color-accent-soft)] text-[color:var(--color-accent-ink)] ring-1 ring-inset ring-[color:var(--color-accent-border)] px-2 py-0.5",
  amber:
    "bg-[color:var(--color-amber)]/12 text-[color:var(--color-amber)] ring-1 ring-inset ring-[color:var(--color-amber)]/30 px-2 py-0.5",
  muted: "text-[color:var(--color-text-dim)]",
};

export function Badge({
  variant = "muted",
  className = "",
  children,
}: {
  variant?: BadgeVariant;
  className?: string;
  children: ReactNode;
}) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full text-[11px] font-semibold tabular-nums ${VARIANT_STYLES[variant]} ${className}`}
    >
      {children}
    </span>
  );
}
