import { auth } from "@clerk/nextjs/server";
import { redirect } from "next/navigation";
import Link from "next/link";
import { ArrowRight, ShieldCheck, Sparkles, Workflow } from "lucide-react";

export default async function Landing() {
  const { userId } = await auth();
  if (userId) redirect("/applications");

  return (
    <main className="relative mx-auto flex min-h-screen max-w-6xl flex-col px-6 py-10">
      <header className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className="size-7 rounded-md bg-gradient-to-br from-[#7C5CFF] to-[#5EEAD4] shadow-[0_0_30px_-5px_#7C5CFF]" />
          <span className="font-mono text-sm tracking-tight">job.os</span>
        </div>
        <div className="flex items-center gap-2">
          <Link
            href="/sign-in"
            className="rounded-full border border-white/10 bg-white/[0.03] px-4 py-1.5 text-sm hover:bg-white/[0.08]"
          >
            Sign in
          </Link>
          <Link
            href="/sign-up"
            className="rounded-full bg-[#7C5CFF] px-4 py-1.5 text-sm font-medium text-white shadow-[0_0_25px_-6px_#7C5CFF] hover:bg-[#8C6CFF]"
          >
            Get started <ArrowRight className="ml-1 inline size-3.5" />
          </Link>
        </div>
      </header>

      <section className="mt-24 max-w-3xl">
        <span className="font-mono text-xs text-[color:var(--color-violet)]">
          /// personal job-search OS
        </span>
        <h1 className="mt-3 text-5xl font-medium tracking-tight">
          Track every application. Tailor every resume.{" "}
          <span className="bg-gradient-to-br from-white to-[#7C5CFF] bg-clip-text text-transparent">
            Never lie on your CV.
          </span>
        </h1>
        <p className="mt-5 max-w-xl text-lg leading-relaxed text-[color:var(--color-text-muted)]">
          A single workspace for the co-op and new-grad grind. Tracker, resume
          tailoring, and a discovery feed — wired together by agents that refuse
          to invent experience you don&apos;t have.
        </p>

        <div className="mt-8 flex items-center gap-3">
          <Link
            href="/sign-in"
            className="rounded-full bg-[#7C5CFF] px-5 py-2.5 text-sm font-medium text-white shadow-[0_0_30px_-5px_#7C5CFF] hover:bg-[#8C6CFF]"
          >
            Sign in to dashboard
          </Link>
          <a
            href="https://github.com/hemnaath04"
            className="rounded-full border border-white/10 px-5 py-2.5 text-sm hover:bg-white/[0.04]"
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
          body="Paste a JD; get a resume tuned to it — with provenance dots on every bullet."
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
    <div className="glass rounded-[var(--radius-card)] p-5">
      <div className="flex size-8 items-center justify-center rounded-md bg-white/[0.06] text-[color:var(--color-violet)]">
        {icon}
      </div>
      <h3 className="mt-4 text-base font-medium">{title}</h3>
      <p className="mt-1.5 text-sm text-[color:var(--color-text-muted)]">{body}</p>
    </div>
  );
}
