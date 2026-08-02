import { auth } from "@clerk/nextjs/server";
import { ArrowRight, ShieldCheck, Sparkles, Workflow } from "lucide-react";
import Link from "next/link";
import { redirect } from "next/navigation";
import { BackendReadiness } from "@/components/backend-readiness";
import { MarketingNav } from "@/components/marketing/marketing-nav";

export default async function Landing() {
  const { userId } = await auth();
  if (userId) redirect("/dashboard");

  return (
    <main className="relative isolate min-h-screen overflow-x-hidden">
      <BackendReadiness />
      <MarketingNav />

      <section className="relative flex flex-col items-center px-6 pb-24 pt-32 md:pt-40">
        {/* The warm bloom behind the hero. Sits under everything and never takes
            a pointer event, so it cannot interfere with the CTA under it. */}
        <div
          aria-hidden="true"
          className="animate-marketing-glow pointer-events-none absolute left-1/2 top-[6rem] -z-10 h-[34rem] w-[min(72rem,120vw)] blur-[110px]"
          style={{
            background:
              "radial-gradient(50% 50% at 50% 50%, rgba(255,231,135,0.30) 0%, rgba(248,214,79,0.14) 42%, transparent 72%)",
          }}
        />

        <p className="animate-rise-in inline-flex items-center gap-2 rounded-full border border-[color:var(--color-border-strong)] bg-[color:var(--color-surface-2)] px-4 py-1.5 text-xs text-[color:var(--color-text-muted)] backdrop-blur-sm">
          Every bullet traceable to evidence you control
        </p>

        <h1 className="text-gradient-night animate-rise-in mt-8 max-w-4xl text-balance text-center text-4xl font-medium leading-[1.06] tracking-[-0.045em] md:text-6xl lg:text-[4.25rem]">
          Track every application.
          <br />
          Tailor every resume.
          <br />
          Never lie on your CV.
        </h1>

        <p className="animate-rise-in mt-7 max-w-xl text-pretty text-center text-base leading-relaxed text-[color:var(--color-text-muted)]">
          A single workspace for the co-op and new-grad grind. Tracker, resume
          tailoring, and a discovery feed, wired together by agents that refuse
          to invent experience you do not have.
        </p>

        <div className="animate-rise-in mt-10 flex flex-wrap items-center justify-center gap-3">
          <Link
            href="/sign-up"
            className="bg-gradient-brand inline-flex h-12 items-center gap-2 rounded-xl px-7 text-base font-semibold text-[color:var(--color-on-accent)] shadow-[var(--shadow-brand-glow)] transition hover:scale-[1.02] active:scale-[.98]"
          >
            Get started <ArrowRight className="size-4" />
          </Link>
          <a
            href="https://github.com/hemnaath04/job-os"
            target="_blank"
            rel="noreferrer"
            className="inline-flex h-12 items-center rounded-xl border border-[color:var(--color-border-strong)] px-6 text-base text-[color:var(--color-text)] transition hover:bg-white/5"
          >
            View the source
          </a>
        </div>

        <MatchPreview />
      </section>

      <section id="how" className="mx-auto max-w-6xl px-6 pb-28">
        <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
          <Feature
            icon={<Workflow className="size-4" />}
            title="One workspace"
            body="Kanban, table and calendar over every wishlist, OA, interview and offer."
          />
          <Feature
            icon={<Sparkles className="size-4" />}
            title="Resume tailoring"
            body="Paste a job description and get a resume tuned to it, with provenance on every bullet."
          />
          <Feature
            icon={<ShieldCheck className="size-4" />}
            title="No invented experience"
            body="The agent raises a gap question instead of inventing a skill you do not have."
          />
        </div>
      </section>

      <section id="honest" className="mx-auto max-w-3xl px-6 pb-32 text-center">
        <h2 className="text-balance text-3xl font-medium tracking-[-0.035em] md:text-4xl">
          Most resume tools write fiction.
        </h2>
        <p className="mx-auto mt-5 max-w-xl text-pretty text-base leading-relaxed text-[color:var(--color-text-muted)]">
          job.os builds only from facts you have entered and verified. If a role
          wants something your evidence does not cover, it tells you that is a
          gap rather than quietly filling it in. The resume you send is one you
          can defend in the interview.
        </p>
        <Link
          href="/sign-up"
          className="bg-gradient-brand mt-9 inline-flex h-12 items-center gap-2 rounded-xl px-7 text-base font-semibold text-[color:var(--color-on-accent)] shadow-[var(--shadow-brand-glow)] transition hover:scale-[1.02] active:scale-[.98]"
        >
          Start with your resume <ArrowRight className="size-4" />
        </Link>
      </section>

      <footer className="border-t border-[color:var(--color-border)] px-6 py-8">
        <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-3 text-xs text-[color:var(--color-text-dim)] sm:flex-row">
          <span className="font-mono">job.os</span>
          <span>Your data stays attached to your own account.</span>
        </div>
      </footer>
    </main>
  );
}

/**
 * A still of the real Job Match card rather than a screenshot.
 *
 * Marked up rather than exported as an image on purpose: it stays sharp on any
 * display, weighs nothing, reads to a screen reader, and cannot drift out of
 * date the way a PNG of last month's UI does. The numbers are the ones the
 * scorer actually produces for a backend role, not decoration.
 */
function MatchPreview() {
  return (
    <div className="animate-rise-in relative mt-20 w-full max-w-3xl">
      <div className="rounded-2xl border border-[color:var(--color-border)] bg-[color:var(--color-surface-1)]/80 p-2 shadow-2xl backdrop-blur-xl">
        <div className="rounded-xl border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] p-5 text-left">
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0">
              <p className="truncate text-sm font-medium text-[color:var(--color-text)]">
                Backend Engineer, New Grad
              </p>
              <p className="mt-0.5 truncate text-xs text-[color:var(--color-text-muted)]">
                Remote, posted 2 days ago
              </p>
            </div>
            <span className="shrink-0 rounded-full border border-[color:var(--color-mint-ink)]/35 bg-[color:var(--color-mint-ink)]/15 px-2 py-0.5 text-[11px] font-semibold tabular-nums text-[color:var(--color-mint-ink)]">
              71% fit
            </span>
          </div>

          <div className="mt-4 flex flex-wrap gap-1.5">
            {["Python", "PostgreSQL", "AWS", "Docker", "Distributed systems"].map(
              (skill) => (
                <span
                  key={skill}
                  className="rounded-full border border-[color:var(--color-mint-ink)]/30 bg-[color:var(--color-mint-ink)]/10 px-2 py-0.5 text-[11px] text-[color:var(--color-mint-ink)]"
                >
                  {skill}
                </span>
              ),
            )}
          </div>

          <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[11px] text-[color:var(--color-text-dim)]">
            <span className="uppercase tracking-wide">Gaps</span>
            {["Microservices", "REST APIs"].map((gap) => (
              <span
                key={gap}
                className="rounded-full bg-[color:var(--color-surface-3)] px-2 py-0.5"
              >
                {gap}
              </span>
            ))}
          </div>
        </div>
      </div>
    </div>
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
    <div className="rounded-[var(--radius-card-lg)] border border-[color:var(--color-border)] bg-[color:var(--color-surface-1)]/60 p-6 backdrop-blur-sm transition hover:border-[color:var(--color-border-strong)] hover:bg-[color:var(--color-surface-1)]">
      <div className="flex size-9 items-center justify-center rounded-xl bg-gradient-brand text-[color:var(--color-on-accent)]">
        {icon}
      </div>
      <h3 className="mt-4 text-base font-medium">{title}</h3>
      <p className="mt-1.5 text-sm leading-relaxed text-[color:var(--color-text-muted)]">
        {body}
      </p>
    </div>
  );
}
