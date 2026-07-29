import { auth } from "@clerk/nextjs/server";
import { ArrowRight, ShieldCheck, Sparkles, Workflow } from "lucide-react";
import Link from "next/link";
import { redirect } from "next/navigation";
import { AuroraBackground } from "@/components/aurora-background";
import { BackendReadiness } from "@/components/backend-readiness";
import { BrandMark } from "@/components/brand-mark";

export default async function Landing() {
  const { userId } = await auth();
  if (userId) redirect("/dashboard");

  return (
    <main className="relative isolate mx-auto flex min-h-screen max-w-6xl flex-col px-6 py-10">
      {/* Fixed rather than absolute so the wash spans the viewport instead of
          being clipped to the centred column. */}
      <AuroraBackground className="fixed inset-0 -z-10" />
      <BackendReadiness />
      <header className="flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <BrandMark className="drop-shadow-[0_12px_16px_rgba(233,198,74,.28)]" />
          <span className="font-mono text-sm tracking-tight">job.os</span>
        </div>
        <div className="flex items-center gap-2">
          <Link
            href="/sign-in"
            className="rounded-full border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] px-4 py-1.5 text-sm transition hover:bg-[color:var(--color-surface-hover)]"
          >
            Sign in
          </Link>
          <Link
            href="/sign-up"
            className="inline-flex items-center gap-1 rounded-full bg-gradient-brand px-4 py-1.5 text-sm font-semibold text-[color:var(--color-on-accent)] shadow-[var(--shadow-brand-glow)] transition hover:scale-[1.02]"
          >
            Get started <ArrowRight className="size-3.5" />
          </Link>
        </div>
      </header>

      <section className="mt-24 max-w-3xl">
        <span className="font-mono text-xs uppercase tracking-widest text-[color:var(--color-violet)]">
          /// personal job-search OS
        </span>
        <h1 className="mt-4 text-5xl font-medium leading-tight tracking-tight md:text-6xl">
          Track every application.<br />
          Tailor every resume.<br />
          <span className="text-gradient-brand">Never lie on your CV.</span>
        </h1>
        <p className="mt-6 max-w-xl text-lg leading-relaxed text-[color:var(--color-text-muted)]">
          A single workspace for the co-op and new-grad grind. Tracker, resume
          tailoring, and a discovery feed, wired together by agents that refuse
          to invent experience you don&apos;t have.
        </p>

        <div className="mt-8 flex items-center gap-3">
          <Link
            href="/sign-in"
            className="rounded-full bg-gradient-brand px-5 py-2.5 text-sm font-semibold text-[color:var(--color-on-accent)] shadow-[var(--shadow-brand-glow)] transition hover:scale-[1.02]"
          >
            Sign in to dashboard
          </Link>
          <a
            href="https://github.com/hemnaath04/job-os"
            className="rounded-full border border-[color:var(--color-border)] px-5 py-2.5 text-sm transition hover:bg-[color:var(--color-surface-2)]"
          >
            View on GitHub
          </a>
        </div>
      </section>

      <section className="mt-24 grid grid-cols-1 gap-4 md:grid-cols-3">
        <Feature
          icon={<Workflow className="size-4" />}
          title="One workspace"
          body="Kanban + table + calendar over every wishlist, OA, interview, and offer."
        />
        <Feature
          icon={<Sparkles className="size-4" />}
          title="Resume tailoring"
          body="Paste a JD and get a resume tuned to it, with provenance dots on every bullet."
        />
        <Feature
          icon={<ShieldCheck className="size-4" />}
          title="No hallucinations"
          body="The agent surfaces a gap question instead of inventing a skill you don't have."
        />
      </section>
    </main>
  );
}

function Feature({
  icon,
  title,
  body,
}: {
  icon: React.ReactNode;
  title: string;
  body: string;
}) {
  return (
    <div className="glass hover-lift rounded-[var(--radius-card-lg)] p-5">
      <div className="flex size-9 items-center justify-center rounded-xl bg-gradient-brand text-[color:var(--color-on-accent)] shadow-[var(--shadow-brand-glow)]">
        {icon}
      </div>
      <h3 className="mt-4 text-base font-medium">{title}</h3>
      <p className="mt-1.5 text-sm text-[color:var(--color-text-muted)]">{body}</p>
    </div>
  );
}
