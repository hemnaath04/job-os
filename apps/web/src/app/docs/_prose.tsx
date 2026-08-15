import { AlertTriangle, Info } from "lucide-react";
import type { ReactNode } from "react";

export function Prose({ children }: { children: ReactNode }) {
  return (
    <div className="max-w-2xl space-y-4 text-[15px] leading-relaxed text-[color:var(--color-text-muted)] [&_a]:text-[color:var(--color-accent-ink)] [&_a]:underline [&_a]:underline-offset-2 [&_a:hover]:text-[color:var(--color-text)] [&_code]:rounded [&_code]:bg-[color:var(--color-surface-2)] [&_code]:px-1.5 [&_code]:py-0.5 [&_code]:text-[13px] [&_code]:text-[color:var(--color-text)] [&_strong]:font-medium [&_strong]:text-[color:var(--color-text)] [&_ol]:list-decimal [&_ol]:space-y-2 [&_ol]:pl-5 [&_ul]:list-disc [&_ul]:space-y-2 [&_ul]:pl-5">
      {children}
    </div>
  );
}

export function H2({ id, children }: { id: string; children: ReactNode }) {
  return (
    <h2
      id={id}
      className="scroll-mt-24 pt-6 text-xl font-medium tracking-[-0.01em] text-[color:var(--color-text)] first:pt-0"
    >
      <a href={`#${id}`} className="no-underline hover:underline">
        {children}
      </a>
    </h2>
  );
}

export function Callout({
  title,
  children,
  type = "info",
}: {
  title?: string;
  children: ReactNode;
  type?: "info" | "warning";
}) {
  const Icon = type === "warning" ? AlertTriangle : Info;
  return (
    <div
      className={
        "flex gap-3 rounded-[var(--radius-card)] border p-4 text-sm not-prose " +
        (type === "warning"
          ? "border-[color:var(--color-rose-ink)]/30 bg-[color:var(--color-rose-ink)]/10"
          : "border-[color:var(--color-border-strong)] bg-[color:var(--color-surface-2)]")
      }
    >
      <Icon
        className={
          "mt-0.5 size-4 shrink-0 " +
          (type === "warning"
            ? "text-[color:var(--color-rose-ink)]"
            : "text-[color:var(--color-accent-ink)]")
        }
        aria-hidden="true"
      />
      <div className="space-y-1">
        {title && <p className="font-medium text-[color:var(--color-text)]">{title}</p>}
        <div className="text-[color:var(--color-text-muted)]">{children}</div>
      </div>
    </div>
  );
}

export function Steps({ children }: { children: ReactNode }) {
  return <ol className="not-prose space-y-6">{children}</ol>;
}

export function Step({
  n,
  title,
  children,
}: {
  n: number;
  title: string;
  children: ReactNode;
}) {
  return (
    <li className="flex gap-4">
      <span className="mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full bg-gradient-brand text-xs font-semibold text-[color:var(--color-on-accent)]">
        {n}
      </span>
      <div className="space-y-1.5 pb-1">
        <p className="font-medium text-[color:var(--color-text)]">{title}</p>
        <div className="text-sm leading-relaxed text-[color:var(--color-text-muted)]">
          {children}
        </div>
      </div>
    </li>
  );
}
