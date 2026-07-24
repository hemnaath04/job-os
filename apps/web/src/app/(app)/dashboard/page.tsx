"use client";

import { useQuery } from "@tanstack/react-query";
import { motion, useReducedMotion } from "framer-motion";
import {
  ArrowUpRight,
  BriefcaseBusiness,
  CalendarClock,
  Check,
  ChevronRight,
  CircleDot,
  Crosshair,
  Radar,
  Sparkles,
  TrendingUp,
  Zap,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { useMemo } from "react";
import { api } from "@/lib/api";
import type { Application, AppStatus } from "@/lib/types";
import { STATUS_LABELS } from "@/lib/types";

const STATUS_COLORS: Record<AppStatus, string> = {
  wishlist: "#898C91",
  ready_to_apply: "#7F9CCB",
  applied: "#9AA7FF",
  oa_received: "#D0A15E",
  interview_scheduled: "#7FA28E",
  offer: "#91AA9A",
  accepted: "#A7B99F",
  rejected: "#CC7A82",
  withdrawn: "#74777C",
  ghosted: "#565A61",
};

const DAY = 86_400_000;

export default function DashboardPage() {
  const reduceMotion = useReducedMotion();
  const { data: applications = [], isLoading } = useQuery({
    queryKey: ["applications"],
    queryFn: () => api.listApplications(),
  });

  const intelligence = useMemo(() => buildIntelligence(applications), [applications]);

  if (isLoading) return <DashboardSkeleton />;

  return (
    <div className="dashboard-stage relative isolate mx-auto min-h-full w-full max-w-[1600px] overflow-hidden px-4 pb-24 pt-5 sm:px-6 lg:px-8 lg:pb-10">
      <div className="dashboard-grid pointer-events-none absolute inset-0 -z-20" />
      <div className="signal-orb signal-orb-a pointer-events-none absolute -z-10" />
      <div className="signal-orb signal-orb-b pointer-events-none absolute -z-10" />

      <motion.header
        initial={reduceMotion ? false : { opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.45, ease: [0.22, 1, 0.36, 1] }}
        className="flex flex-col gap-5 border-b border-white/[0.07] pb-6 md:flex-row md:items-end md:justify-between"
      >
        <div className="max-w-3xl">
          <div className="mb-3 flex items-center gap-2 font-mono text-[10px] uppercase tracking-[0.24em] text-[color:var(--color-kiwi)]">
            <span className="status-beacon" />
            Search system online
            <span className="text-white/25">//</span>
            {formatShortDate(new Date())}
          </div>
          <h1 className="text-balance text-[clamp(2rem,5vw,4.6rem)] font-medium leading-[0.92] tracking-[-0.065em] text-white">
            Your search is
            <span className="ml-[0.18em] inline-block text-gradient-brand">in motion.</span>
          </h1>
          <p className="mt-4 max-w-xl text-sm leading-relaxed text-white/48 sm:text-base">
            One flight deck for every application, signal, and next move. Keep the
            pipeline warm and the momentum visible.
          </p>
        </div>

        <div className="flex flex-wrap gap-2">
          <Link href="/jobs" className="kinetic-button kinetic-button-secondary group">
            <Radar className="size-4" />
            Scan roles
            <ChevronRight className="size-3.5 transition-transform group-hover:translate-x-0.5" />
          </Link>
          <Link href="/tailor" className="kinetic-button kinetic-button-primary group">
            <Sparkles className="size-4" />
            Tailor resume
            <ArrowUpRight className="size-3.5 transition-transform group-hover:-translate-y-0.5 group-hover:translate-x-0.5" />
          </Link>
        </div>
      </motion.header>

      {applications.length === 0 ? (
        <FirstLaunch />
      ) : (
        <motion.div
          initial="hidden"
          animate="visible"
          variants={{
            hidden: {},
            visible: { transition: { staggerChildren: reduceMotion ? 0 : 0.055 } },
          }}
          className="mt-5 grid grid-cols-1 gap-3 xl:grid-cols-12"
        >
          <Panel className="min-h-[360px] xl:col-span-8 xl:row-span-2" label="Momentum map" code="01">
            <MomentumPanel intelligence={intelligence} />
          </Panel>

          <div className="grid grid-cols-2 gap-3 xl:col-span-4">
            <Metric
              icon={BriefcaseBusiness}
              label="In orbit"
              value={intelligence.total}
              detail={`${signed(intelligence.weekDelta)} this week`}
              hot
            />
            <Metric
              icon={Crosshair}
              label="Interviews"
              value={intelligence.interviews}
              detail={`${intelligence.interviewRate}% conversion`}
            />
            <Metric
              icon={Zap}
              label="Responses"
              value={`${intelligence.responseRate}%`}
              detail={`${intelligence.responses} signals`}
            />
            <Metric
              icon={Check}
              label="Offers"
              value={intelligence.offers}
              detail={intelligence.offers ? "Momentum found" : "Still building"}
            />
          </div>

          <Panel className="xl:col-span-4" label="Next moves" code="02">
            <NextMoves applications={intelligence.nextMoves} />
          </Panel>

          <Panel className="xl:col-span-4" label="Pipeline orbit" code="03">
            <Orbit distribution={intelligence.distribution} total={intelligence.total} />
          </Panel>

          <Panel className="xl:col-span-8" label="Signal feed" code="04">
            <SignalFeed applications={intelligence.recent} />
          </Panel>

          <Panel className="xl:col-span-12" label="Conversion runway" code="05">
            <ConversionRunway tiers={intelligence.funnel} />
          </Panel>
        </motion.div>
      )}
    </div>
  );
}

function Panel({
  children,
  className = "",
  label,
  code,
}: {
  children: React.ReactNode;
  className?: string;
  label: string;
  code: string;
}) {
  return (
    <motion.section
      variants={{
        hidden: { opacity: 0, y: 12, scale: 0.99 },
        visible: { opacity: 1, y: 0, scale: 1 },
      }}
      transition={{ duration: 0.42, ease: [0.22, 1, 0.36, 1] }}
      className={`flight-panel group relative overflow-hidden rounded-[22px] ${className}`}
    >
      <div className="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-[color:var(--color-kiwi)]/45 to-transparent opacity-0 transition-opacity duration-500 group-hover:opacity-100" />
      <div className="flex items-center justify-between px-5 pt-4 font-mono text-[9px] uppercase tracking-[0.22em] text-white/32">
        <span>{label}</span>
        <span>{code} / job.os</span>
      </div>
      <div className="p-5 pt-4">{children}</div>
    </motion.section>
  );
}

function Metric({
  icon: Icon,
  label,
  value,
  detail,
  hot = false,
}: {
  icon: LucideIcon;
  label: string;
  value: string | number;
  detail: string;
  hot?: boolean;
}) {
  return (
    <motion.div
      variants={{
        hidden: { opacity: 0, y: 10 },
        visible: { opacity: 1, y: 0 },
      }}
      className={`metric-tile relative min-h-36 overflow-hidden rounded-[20px] p-4 ${hot ? "metric-tile-hot" : ""}`}
    >
      <div className="flex items-start justify-between">
        <span className="flex size-8 items-center justify-center rounded-full border border-white/10 bg-white/[0.04] text-white/60">
          <Icon className="size-3.5" />
        </span>
        <span className="font-mono text-[8px] uppercase tracking-[0.18em] text-white/25">live</span>
      </div>
      <div className="mt-5 text-[clamp(1.8rem,3vw,2.7rem)] font-medium leading-none tracking-[-0.06em]">
        {value}
      </div>
      <div className="mt-2 text-[10px] uppercase tracking-[0.14em] text-white/40">{label}</div>
      <div className="mt-1 truncate text-[10px] text-white/24">{detail}</div>
    </motion.div>
  );
}

function MomentumPanel({ intelligence }: { intelligence: Intelligence }) {
  const peak = Math.max(...intelligence.daily.map((d) => d.value), 1);
  const points = intelligence.daily
    .map((d, i) => {
      const x = (i / Math.max(intelligence.daily.length - 1, 1)) * 100;
      const y = 82 - (d.value / peak) * 62;
      return `${x},${y}`;
    })
    .join(" ");

  return (
    <div>
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <div className="text-3xl font-medium tracking-[-0.045em] sm:text-4xl">
            {intelligence.last30} launches
          </div>
          <p className="mt-1 text-xs text-white/35">Applications sent over the last 30 days</p>
        </div>
        <div className="flex items-center gap-2 rounded-full border border-[color:var(--color-kiwi)]/20 bg-[color:var(--color-kiwi)]/[0.06] px-3 py-1.5 font-mono text-[9px] uppercase tracking-[0.16em] text-[color:var(--color-kiwi)]">
          <TrendingUp className="size-3" />
          {intelligence.velocity} / week velocity
        </div>
      </div>

      <div className="relative mt-7 h-48 overflow-hidden rounded-2xl border border-white/[0.05] bg-black/35 px-3 py-4">
        <div className="chart-grid absolute inset-0" />
        <svg
          viewBox="0 0 100 100"
          preserveAspectRatio="none"
          className="relative h-full w-full overflow-visible"
          role="img"
          aria-label="Applications sent in the last 30 days"
        >
          <defs>
            <linearGradient id="momentum-line" x1="0" y1="0" x2="1" y2="0">
              <stop offset="0" stopColor="#68739F" />
              <stop offset="0.55" stopColor="#9AA7FF" />
              <stop offset="1" stopColor="#D7DAF5" />
            </linearGradient>
            <linearGradient id="momentum-fill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stopColor="#9AA7FF" stopOpacity=".28" />
              <stop offset="1" stopColor="#9AA7FF" stopOpacity="0" />
            </linearGradient>
          </defs>
          <polygon points={`0,100 ${points} 100,100`} fill="url(#momentum-fill)" />
          <polyline
            points={points}
            fill="none"
            stroke="url(#momentum-line)"
            strokeWidth="1.5"
            vectorEffect="non-scaling-stroke"
            strokeLinecap="round"
            strokeLinejoin="round"
            className="momentum-path"
          />
        </svg>
        <div className="absolute bottom-3 left-4 font-mono text-[8px] uppercase tracking-[0.16em] text-white/20">T−30 days</div>
        <div className="absolute bottom-3 right-4 font-mono text-[8px] uppercase tracking-[0.16em] text-white/20">Now</div>
      </div>
    </div>
  );
}

function NextMoves({ applications }: { applications: Application[] }) {
  if (!applications.length) {
    return (
      <div className="flex min-h-40 flex-col items-center justify-center text-center">
        <CalendarClock className="size-6 text-white/20" />
        <p className="mt-3 text-sm text-white/50">No deadlines on radar.</p>
        <p className="mt-1 text-[11px] text-white/25">Add a next action to an application.</p>
      </div>
    );
  }

  return (
    <div className="space-y-1.5">
      {applications.slice(0, 4).map((app, index) => (
        <Link
          href="/applications"
          key={app.id}
          className="group/move flex items-center gap-3 rounded-xl border border-transparent px-2 py-2.5 transition hover:border-white/[0.06] hover:bg-white/[0.025]"
        >
          <div className="flex size-7 shrink-0 items-center justify-center rounded-full border border-white/[0.08] font-mono text-[9px] text-white/35">
            {String(index + 1).padStart(2, "0")}
          </div>
          <div className="min-w-0 flex-1">
            <div className="truncate text-xs text-white/75">{app.next_action_label || "Follow up"}</div>
            <div className="mt-0.5 truncate text-[10px] text-white/28">
              {app.job.company?.name || "Unknown company"} · {app.job.title}
            </div>
          </div>
          <div className="text-right">
            <div className="font-mono text-[9px] text-[color:var(--color-kiwi)]">
              {relativeDate(app.next_action_at)}
            </div>
            <ChevronRight className="ml-auto mt-1 size-3 text-white/15 transition-transform group-hover/move:translate-x-0.5" />
          </div>
        </Link>
      ))}
    </div>
  );
}

function Orbit({ distribution, total }: { distribution: Distribution[]; total: number }) {
  let cursor = 0;
  const circumference = 2 * Math.PI * 42;
  return (
    <div className="flex flex-col items-center gap-5 sm:flex-row xl:flex-col 2xl:flex-row">
      <div className="relative size-36 shrink-0">
        <svg viewBox="0 0 100 100" className="size-full -rotate-90" aria-label="Application status distribution">
          <circle cx="50" cy="50" r="42" fill="none" stroke="rgba(255,255,255,.045)" strokeWidth="8" />
          {distribution.map((d) => {
            const length = total ? (d.value / total) * circumference : 0;
            const dashOffset = -cursor;
            cursor += length;
            return (
              <circle
                key={d.name}
                cx="50"
                cy="50"
                r="42"
                fill="none"
                stroke={d.color}
                strokeWidth="8"
                strokeDasharray={`${Math.max(length - 2, 0)} ${circumference}`}
                strokeDashoffset={dashOffset}
                strokeLinecap="round"
                className="orbit-segment"
              />
            );
          })}
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <span className="text-3xl font-medium tracking-[-0.06em]">{total}</span>
          <span className="font-mono text-[8px] uppercase tracking-[0.2em] text-white/25">tracked</span>
        </div>
      </div>
      <div className="grid w-full grid-cols-2 gap-x-4 gap-y-2">
        {distribution.slice(0, 6).map((d) => (
          <div key={d.name} className="flex min-w-0 items-center gap-2 text-[10px]">
            <span className="size-1.5 shrink-0 rounded-full" style={{ background: d.color, boxShadow: `0 0 9px ${d.color}` }} />
            <span className="truncate text-white/38">{d.name}</span>
            <span className="ml-auto font-mono text-white/65">{d.value}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function SignalFeed({ applications }: { applications: Application[] }) {
  return (
    <div className="divide-y divide-white/[0.045]">
      {applications.slice(0, 5).map((app) => (
        <Link
          href="/applications"
          key={app.id}
          className="signal-row group/signal grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3 py-3 sm:grid-cols-[minmax(0,1.3fr)_minmax(120px,.7fr)_auto]"
        >
          <div className="flex min-w-0 items-center gap-3">
            <span className="relative flex size-8 shrink-0 items-center justify-center rounded-lg border border-white/[0.07] bg-white/[0.025] font-mono text-[9px] uppercase text-white/40">
              {(app.job.company?.name || "?").slice(0, 2)}
              <span className="absolute -right-0.5 -top-0.5 size-1.5 rounded-full" style={{ background: STATUS_COLORS[app.status] }} />
            </span>
            <div className="min-w-0">
              <div className="truncate text-xs text-white/78 transition-colors group-hover/signal:text-[color:var(--color-kiwi)]">
                {app.job.title}
              </div>
              <div className="mt-0.5 truncate text-[10px] text-white/27">{app.job.company?.name || "Unknown company"}</div>
            </div>
          </div>
          <div className="hidden min-w-0 sm:block">
            <StatusSignal status={app.status} />
          </div>
          <div className="flex items-center gap-3">
            <span className="font-mono text-[9px] text-white/22">{relativeDate(app.updated_at)}</span>
            <ArrowUpRight className="size-3.5 text-white/18 transition group-hover/signal:-translate-y-0.5 group-hover/signal:translate-x-0.5 group-hover/signal:text-white/55" />
          </div>
        </Link>
      ))}
    </div>
  );
}

function StatusSignal({ status }: { status: AppStatus }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-white/[0.06] bg-white/[0.025] px-2 py-1 font-mono text-[8px] uppercase tracking-[0.12em] text-white/38">
      <span className="size-1.5 rounded-full" style={{ background: STATUS_COLORS[status] }} />
      {STATUS_LABELS[status]}
    </span>
  );
}

function ConversionRunway({ tiers }: { tiers: FunnelTier[] }) {
  const peak = Math.max(tiers[0]?.count || 0, 1);
  return (
    <div className="grid gap-3 md:grid-cols-4">
      {tiers.map((tier, index) => {
        const pct = Math.round((tier.count / peak) * 100);
        return (
          <div key={tier.label} className="runway-step relative overflow-hidden rounded-2xl border border-white/[0.055] bg-black/25 p-4">
            <div className="flex items-center justify-between">
              <span className="font-mono text-[9px] uppercase tracking-[0.16em] text-white/35">
                {String(index + 1).padStart(2, "0")} · {tier.label}
              </span>
              <span className="text-xs text-white/70">{tier.count}</span>
            </div>
            <div className="mt-6 h-1 overflow-hidden rounded-full bg-white/[0.05]">
              <motion.div
                initial={{ scaleX: 0 }}
                animate={{ scaleX: pct / 100 }}
                transition={{ duration: 0.7, delay: index * 0.08, ease: [0.22, 1, 0.36, 1] }}
                className="h-full origin-left rounded-full"
                style={{ background: tier.color, boxShadow: `0 0 18px ${tier.color}` }}
              />
            </div>
            <div className="mt-2 flex items-center justify-between font-mono text-[8px] uppercase tracking-[0.13em] text-white/20">
              <span>conversion</span>
              <span>{pct}%</span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function FirstLaunch() {
  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.985 }}
      animate={{ opacity: 1, scale: 1 }}
      className="first-launch relative mt-5 min-h-[520px] overflow-hidden rounded-[28px] border border-white/[0.07]"
    >
      <div className="radar-field absolute left-1/2 top-1/2 size-[min(82vw,460px)] -translate-x-1/2 -translate-y-1/2 rounded-full" />
      <div className="relative z-10 mx-auto flex min-h-[520px] max-w-xl flex-col items-center justify-center px-6 text-center">
        <span className="mb-6 flex size-14 items-center justify-center rounded-full border border-[color:var(--color-kiwi)]/25 bg-[color:var(--color-kiwi)]/[0.07] text-[color:var(--color-kiwi)] shadow-[0_12px_28px_-18px_rgba(107,120,210,.48)]">
          <CircleDot className="size-5" />
        </span>
        <div className="font-mono text-[9px] uppercase tracking-[0.25em] text-[color:var(--color-kiwi)]">Awaiting first signal</div>
        <h2 className="mt-4 text-4xl font-medium leading-[0.95] tracking-[-0.055em] sm:text-5xl">Launch your first application.</h2>
        <p className="mt-4 max-w-md text-sm leading-relaxed text-white/42">
          Add a role and job.os will turn this empty radar into a living map of your search.
        </p>
        <Link href="/jobs" className="kinetic-button kinetic-button-primary mt-7">
          <Radar className="size-4" /> Find the first signal
        </Link>
      </div>
    </motion.div>
  );
}

function DashboardSkeleton() {
  return (
    <div className="mx-auto w-full max-w-[1600px] px-4 py-6 sm:px-6 lg:px-8">
      <div className="h-4 w-44 animate-pulse rounded-full bg-white/[0.05]" />
      <div className="mt-5 h-16 max-w-2xl animate-pulse rounded-2xl bg-white/[0.05]" />
      <div className="mt-8 grid grid-cols-1 gap-3 xl:grid-cols-12">
        <div className="shimmer min-h-96 rounded-[22px] border border-white/[0.06] bg-white/[0.025] xl:col-span-8" />
        <div className="grid grid-cols-2 gap-3 xl:col-span-4">
          {[0, 1, 2, 3].map((n) => <div key={n} className="shimmer min-h-44 rounded-[20px] border border-white/[0.06] bg-white/[0.025]" />)}
        </div>
      </div>
    </div>
  );
}

type Distribution = { name: string; value: number; color: string };
type FunnelTier = { label: string; count: number; color: string };
type Intelligence = ReturnType<typeof buildIntelligence>;

function buildIntelligence(applications: Application[]) {
  const now = Date.now();
  const dayKey = (value: string | number | Date) => {
    const date = new Date(value);
    return `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`;
  };
  const createdWithin = (app: Application, days: number, offset = 0) => {
    const created = new Date(app.created_at).getTime();
    return created >= now - (days + offset) * DAY && created < now - offset * DAY;
  };

  const daily = Array.from({ length: 30 }, (_, index) => {
    const date = new Date(now - (29 - index) * DAY);
    return {
      label: date.toLocaleDateString(undefined, { month: "short", day: "numeric" }),
      value: applications.filter((app) => dayKey(app.created_at) === dayKey(date)).length,
    };
  });

  const responseStatuses: AppStatus[] = ["oa_received", "interview_scheduled", "offer", "accepted", "rejected"];
  const interviewStatuses: AppStatus[] = ["interview_scheduled", "offer", "accepted"];
  const offerStatuses: AppStatus[] = ["offer", "accepted"];
  const responses = applications.filter((app) => responseStatuses.includes(app.status)).length;
  const interviews = applications.filter((app) => interviewStatuses.includes(app.status)).length;
  const offers = applications.filter((app) => offerStatuses.includes(app.status)).length;
  const applied = applications.filter((app) => !["wishlist", "ready_to_apply", "withdrawn"].includes(app.status)).length;

  const counts = new Map<AppStatus, number>();
  applications.forEach((app) => counts.set(app.status, (counts.get(app.status) || 0) + 1));
  const distribution = Array.from(counts.entries())
    .sort((a, b) => b[1] - a[1])
    .map(([status, value]) => ({ name: STATUS_LABELS[status], value, color: STATUS_COLORS[status] }));

  const nextMoves = applications
    .filter((app) => app.next_action_at)
    .sort((a, b) => new Date(a.next_action_at!).getTime() - new Date(b.next_action_at!).getTime());

  const funnel: FunnelTier[] = [
    { label: "Applied", count: applied, color: "#9AA7FF" },
    { label: "Response", count: responses, color: "#D0A15E" },
    { label: "Interview", count: interviews, color: "#7FA28E" },
    { label: "Offer", count: offers, color: "#91AA9A" },
  ];

  return {
    total: applications.length,
    responses,
    interviews,
    offers,
    responseRate: applied ? Math.round((responses / applied) * 100) : 0,
    interviewRate: applied ? Math.round((interviews / applied) * 100) : 0,
    weekDelta: applications.filter((app) => createdWithin(app, 7)).length - applications.filter((app) => createdWithin(app, 7, 7)).length,
    last30: daily.reduce((sum, day) => sum + day.value, 0),
    velocity: applications.filter((app) => createdWithin(app, 7)).length,
    daily,
    distribution,
    nextMoves,
    funnel,
    recent: [...applications].sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()),
  };
}

function relativeDate(value: string | null) {
  if (!value) return "—";
  const delta = new Date(value).getTime() - Date.now();
  const days = Math.round(delta / DAY);
  if (days === 0) return "today";
  if (days === 1) return "tomorrow";
  if (days === -1) return "yesterday";
  if (days > 0 && days < 14) return `in ${days}d`;
  if (days < 0 && days > -14) return `${Math.abs(days)}d ago`;
  return new Date(value).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function formatShortDate(date: Date) {
  return date.toLocaleDateString(undefined, { month: "short", day: "2-digit" });
}

function signed(value: number) {
  return value > 0 ? `+${value}` : String(value);
}
