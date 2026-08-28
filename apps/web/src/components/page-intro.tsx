import type { LucideIcon } from "lucide-react";

export function PageIntro({
  eyebrow,
  title,
  description,
  icon: Icon,
  action,
  children,
}: {
  eyebrow: string;
  title: string;
  description: string;
  icon: LucideIcon;
  action?: React.ReactNode;
  children?: React.ReactNode;
}) {
  return (
    <section className="page-intro">
      <div className="page-intro-grid" aria-hidden="true" />
      <div className="relative z-10 flex min-w-0 flex-1 items-start gap-4">
        <div className="page-intro-icon">
          <Icon className="size-5" />
        </div>
        <div className="min-w-0">
          <div className="section-kicker">{eyebrow}</div>
          <h1 className="mt-2 text-3xl font-semibold tracking-[-0.04em] text-[color:var(--color-text)] sm:text-[2rem]">
            {title}
          </h1>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-[color:var(--color-text-muted)]">
            {description}
          </p>
          {children && <div className="mt-4 flex flex-wrap gap-2">{children}</div>}
        </div>
      </div>
      {action && <div className="relative z-10 shrink-0">{action}</div>}
    </section>
  );
}

export function InfoChip({ children, tone = "default" }: { children: React.ReactNode; tone?: "default" | "sage" | "clay" }) {
  return <span className={`info-chip info-chip-${tone}`}>{children}</span>;
}

/**
 * A chip-shaped placeholder for a count that has not arrived yet.
 *
 * "0 saved roles" is not a smaller version of "50 saved roles", it is a
 * different claim, and it was the first thing a signed-in user read on /tailor
 * before hydration replaced it. `label` is what a screen reader hears in its
 * place, since a bar that is only a shape has nothing to announce.
 */
export function ChipSkeleton({ label, width = "6.5rem" }: { label: string; width?: string }) {
  return (
    <span className="info-chip" style={{ width }}>
      <span className="sr-only">{label}</span>
      <span
        aria-hidden="true"
        className="h-2 w-full animate-pulse rounded-full bg-[color:var(--color-surface-3)]"
      />
    </span>
  );
}
