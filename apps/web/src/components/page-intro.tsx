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
          <h1 className="mt-2 text-3xl font-semibold tracking-[-0.04em] text-white sm:text-[2rem]">
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
