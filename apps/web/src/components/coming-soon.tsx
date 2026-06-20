import { type LucideIcon } from "lucide-react";
import Link from "next/link";
import type { Route } from "next";

export function ComingSoon({
  title,
  milestone,
  description,
  icon: Icon,
  cta,
}: {
  title: string;
  milestone: string;
  description: string;
  icon: LucideIcon;
  cta?: { href: Route; label: string };
}) {
  return (
    <div className="mx-auto max-w-3xl px-8 py-16">
      <div className="glass rounded-[var(--radius-card)] p-10 text-center">
        <div className="mx-auto flex size-12 items-center justify-center rounded-xl bg-white/[0.04]">
          <Icon className="size-5 text-[color:var(--color-violet)]" />
        </div>
        <div className="mt-5 inline-block rounded-full bg-[color:var(--color-violet)]/15 px-2.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-[color:var(--color-violet)]">
          {milestone}
        </div>
        <h1 className="mt-2 text-2xl font-medium tracking-tight">{title}</h1>
        <p className="mx-auto mt-3 max-w-lg text-sm leading-relaxed text-[color:var(--color-text-muted)]">
          {description}
        </p>
        {cta && (
          <Link
            href={cta.href}
            className="mt-6 inline-block rounded-full border border-white/10 bg-white/[0.04] px-4 py-1.5 text-sm hover:bg-white/[0.08]"
          >
            {cta.label}
          </Link>
        )}
      </div>
    </div>
  );
}
