"use client";

import { useQuery } from "@tanstack/react-query";
import { motion, useReducedMotion, type Variants } from "framer-motion";
import {
  ArrowUpRight,
  BriefcaseBusiness,
  CalendarClock,
  CheckCircle2,
  Clock3,
  DatabaseZap,
  FileText,
  Radar,
  Sparkles,
  TrendingUp,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { useMemo } from "react";
import { CompanyAvatar } from "@/components/company-avatar";
import { StatusPill } from "@/components/status-pill";
import { api } from "@/lib/api";
import type { Application, AppStatus } from "@/lib/types";
import { STATUS_LABELS } from "@/lib/types";

const DAY = 86_400_000;
const WEEKDAY = ["S", "M", "T", "W", "T", "F", "S"];

const STATUS_COLORS: Record<AppStatus, string> = {
  wishlist: "#9C948A",
  ready_to_apply: "#3F6FA6",
  applied: "#8A6D12",
  oa_received: "#B0791C",
  interview_scheduled: "#4E8A5F",
  offer: "#4E8A5F",
  accepted: "#3E7A54",
  rejected: "#C0555F",
  withdrawn: "#9C948A",
  ghosted: "#B7ABB2",
};

export default function DashboardClient({
  initialApplications,
}: {
  initialApplications: Application[] | null;
}) {
  const reduceMotion = useReducedMotion();
  const { data: applications = [], isLoading, isError, refetch } = useQuery({
    queryKey: ["applications"],
    queryFn: () => api.listApplications(),
    initialData: initialApplications ?? undefined,
  });
  const intelligence = useMemo(() => buildIntelligence(applications), [applications]);

  if (isLoading) return <DashboardSkeleton />;
  if (isError) {
    return (
      <div className="workspace-page">
        <div className="product-empty-state">
          <DatabaseZap className="size-6" />
          <h1>Dashboard data is unavailable</h1>
          <p>Your applications are safe. Try reconnecting to the data service.</p>
          <button className="product-button product-button-primary" onClick={() => refetch()}>
            Try again
          </button>
        </div>
      </div>
    );
  }

  if (!applications.length) return <FirstLaunch />;

  const itemVariants: Variants = {
    hidden: reduceMotion ? {} : { opacity: 0, y: 12 },
    visible: { opacity: 1, y: 0 },
  };

  return (
    <div className="workspace-page max-w-[1560px]">
      {/* Header */}
      <motion.header
        initial={reduceMotion ? false : { opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.35, ease: [0.16, 1, 0.3, 1] }}
        className="mb-7 flex flex-col gap-4 md:flex-row md:items-center md:justify-between"
      >
        <div>
          <h1 className="text-[2rem] font-extrabold leading-none tracking-[-0.03em] text-[color:var(--color-text)]">
            Dashboard
          </h1>
          <p className="mt-2.5 text-sm text-[color:var(--color-text-muted)]">
            Track, tailor, and land your next role, all in one place.
          </p>
        </div>
        <div className="flex flex-wrap gap-2.5">
          <Link href="/jobs" className="product-button product-button-secondary">
            <Radar className="size-4" />
            Find roles
          </Link>
          <Link href="/tailor" className="product-button product-button-primary">
            <Sparkles className="size-4" />
            Tailor resume
          </Link>
        </div>
      </motion.header>

      {/* KPI row — hero card filled jasmine, Donezo-style */}
      <motion.div
        initial="hidden"
        animate="visible"
        variants={{ visible: { transition: { staggerChildren: reduceMotion ? 0 : 0.05 } } }}
        className="grid gap-3.5 sm:grid-cols-2 xl:grid-cols-4"
      >
        <KpiCard
          variants={itemVariants}
          accent
          icon={BriefcaseBusiness}
          label="Total Applications"
          value={intelligence.total}
          trend={`${signed(intelligence.weekDelta)} this week`}
          positive={intelligence.weekDelta >= 0}
        />
        <KpiCard
          variants={itemVariants}
          icon={TrendingUp}
          label="Response Rate"
          value={`${intelligence.responseRate}%`}
          trend={`${intelligence.responses} responses`}
          positive
        />
        <KpiCard
          variants={itemVariants}
          icon={CalendarClock}
          label="Interviews"
          value={intelligence.interviews}
          trend={`${intelligence.interviewRate}% conversion`}
          positive
        />
        <KpiCard
          variants={itemVariants}
          icon={CheckCircle2}
          label="Offers"
          value={intelligence.offers}
          trend={intelligence.offers ? "Offer stage reached" : "No offers yet"}
          positive={intelligence.offers > 0}
        />
      </motion.div>

      {/* Analytics bars + pipeline gauge */}
      <motion.div
        initial="hidden"
        animate="visible"
        variants={{ visible: { transition: { staggerChildren: reduceMotion ? 0 : 0.06 } } }}
        className="mt-3.5 grid gap-3.5 xl:grid-cols-[minmax(0,1.6fr)_minmax(300px,.7fr)]"
      >
        <DashboardPanel variants={itemVariants} title="Application activity" icon={TrendingUp}>
          <WeekdayBars intelligence={intelligence} reduceMotion={Boolean(reduceMotion)} />
        </DashboardPanel>
        <DashboardPanel variants={itemVariants} title="Pipeline progress" icon={DatabaseZap}>
          <ProgressGauge intelligence={intelligence} reduceMotion={Boolean(reduceMotion)} />
        </DashboardPanel>
      </motion.div>

      {/* Recent + next actions */}
      <motion.div
        initial="hidden"
        animate="visible"
        variants={{ visible: { transition: { staggerChildren: reduceMotion ? 0 : 0.06 } } }}
        className="mt-3.5 grid gap-3.5 xl:grid-cols-[minmax(0,1.6fr)_minmax(300px,.7fr)]"
      >
        <DashboardPanel
          variants={itemVariants}
          title="Recent applications"
          icon={BriefcaseBusiness}
          action={{ href: "/applications", label: "View all" }}
        >
          <RecentApplications applications={intelligence.recent} />
        </DashboardPanel>
        <DashboardPanel
          variants={itemVariants}
          title="Next actions"
          icon={Clock3}
          action={{ href: "/calendar", label: "Calendar" }}
        >
          <NextActions applications={intelligence.nextMoves} />
        </DashboardPanel>
      </motion.div>
    </div>
  );
}

function KpiCard({
  icon: Icon,
  label,
  value,
  trend,
  positive,
  accent = false,
  variants,
}: {
  icon: LucideIcon;
  label: string;
  value: string | number;
  trend: string;
  positive?: boolean;
  accent?: boolean;
  variants: Variants;
}) {
  return (
    <motion.section
      variants={variants}
      whileHover={{ y: -3 }}
      transition={{ type: "spring", stiffness: 320, damping: 26 }}
      className={
        "relative overflow-hidden rounded-2xl p-5 " +
        (accent
          ? "border border-[color:var(--color-accent-border)] bg-[color:var(--color-accent)] text-[color:var(--color-on-accent)] shadow-[var(--shadow-brand-glow)]"
          : "border border-[color:var(--color-border)] bg-[color:var(--color-surface-1)] shadow-[var(--shadow-glass)]")
      }
    >
      <div className="flex items-start justify-between">
        <span
          className={
            "grid size-9 place-items-center rounded-xl " +
            (accent
              ? "bg-[color:var(--color-on-accent)]/10 text-[color:var(--color-on-accent)]"
              : "border border-[color:var(--color-accent-border)] bg-[color:var(--color-accent-soft)] text-[color:var(--color-accent-ink)]")
          }
        >
          <Icon className="size-[18px]" />
        </span>
        <span
          className={
            "grid size-7 place-items-center rounded-full border " +
            (accent
              ? "border-[color:var(--color-on-accent)]/20 text-[color:var(--color-on-accent)]"
              : "border-[color:var(--color-border)] text-[color:var(--color-text-dim)]")
          }
        >
          <ArrowUpRight className="size-3.5" />
        </span>
      </div>
      <div className="mt-6 text-[2.4rem] font-extrabold leading-none tracking-[-0.045em]">{value}</div>
      <div
        className={
          "mt-2.5 text-[0.8rem] font-semibold " +
          (accent ? "text-[color:var(--color-on-accent)]/80" : "text-[color:var(--color-text-muted)]")
        }
      >
        {label}
      </div>
      <div
        className={
          "mt-2 inline-flex items-center gap-1.5 text-[0.72rem] font-medium " +
          (accent
            ? "text-[color:var(--color-on-accent)]/70"
            : positive
              ? "text-[color:var(--color-mint)]"
              : "text-[color:var(--color-text-dim)]")
        }
      >
        <TrendingUp className={"size-3.5 " + (positive ? "" : "rotate-180")} />
        {trend}
      </div>
    </motion.section>
  );
}

function DashboardPanel({
  title,
  icon: Icon,
  action,
  children,
  variants,
}: {
  title: string;
  icon: LucideIcon;
  action?: { href: string; label: string };
  children: React.ReactNode;
  variants: Variants;
}) {
  return (
    <motion.section variants={variants} className="product-panel">
      <div className="flex items-center justify-between border-b border-[color:var(--color-border)] px-5 py-4">
        <div className="flex items-center gap-2.5">
          <span className="grid size-7 place-items-center rounded-lg border border-[color:var(--color-accent-border)] bg-[color:var(--color-accent-soft)] text-[color:var(--color-accent-ink)]">
            <Icon className="size-3.5" />
          </span>
          <h2 className="text-sm font-semibold text-[color:var(--color-text)]">{title}</h2>
        </div>
        {action && (
          <Link
            href={action.href}
            className="inline-flex items-center gap-1.5 rounded-full border border-[color:var(--color-border)] px-3 py-1 text-xs font-medium text-[color:var(--color-text-muted)] transition hover:border-[color:var(--color-accent-border)] hover:text-[color:var(--color-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-accent-ink)]"
          >
            {action.label}
            <ArrowUpRight className="size-3" />
          </Link>
        )}
      </div>
      <div className="p-5">{children}</div>
    </motion.section>
  );
}

function WeekdayBars({
  intelligence,
  reduceMotion,
}: {
  intelligence: Intelligence;
  reduceMotion: boolean;
}) {
  const now = Date.now();
  const last7 = intelligence.daily.slice(-7).map((day, i) => {
    const date = new Date(now - (6 - i) * DAY);
    return { value: day.value, label: WEEKDAY[date.getDay()] };
  });
  const peak = Math.max(...last7.map((d) => d.value), 1);
  const busiest = last7.reduce((a, b) => (b.value >= a.value ? b : a), last7[0]);

  return (
    <div>
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="text-2xl font-extrabold tracking-tight text-[color:var(--color-text)]">
            {intelligence.last30}
          </div>
          <p className="mt-1 text-xs text-[color:var(--color-text-dim)]">Applications added in 30 days</p>
        </div>
        <div className="inline-flex items-center gap-1.5 rounded-full bg-[color:var(--color-accent-soft)] px-2.5 py-1 text-xs font-medium text-[color:var(--color-accent-ink)]">
          <TrendingUp className="size-3.5" />
          {intelligence.velocity} this week
        </div>
      </div>
      <div className="mt-6 flex h-44 items-end gap-2.5">
        {last7.map((d, i) => {
          const h = d.value > 0 ? Math.max((d.value / peak) * 100, 14) : 5;
          const isPeak = d.value === busiest.value && d.value > 0;
          return (
            <div key={i} className="flex h-full flex-1 flex-col items-center justify-end gap-2">
              <div className="relative flex w-full flex-1 items-end justify-center">
                {isPeak && (
                  <span
                    className="absolute z-10 rounded-md bg-[color:var(--color-text)] px-1.5 py-0.5 text-[9px] font-bold text-[color:var(--color-surface-1)]"
                    style={{ bottom: `calc(${h}% + 6px)` }}
                  >
                    {d.value}
                  </span>
                )}
                <motion.div
                  style={{ height: `${h}%` }}
                  initial={reduceMotion ? false : { scaleY: 0 }}
                  animate={{ scaleY: 1 }}
                  transition={{ duration: 0.55, delay: reduceMotion ? 0 : i * 0.05, ease: [0.16, 1, 0.3, 1] }}
                  className={
                    "w-full max-w-[40px] origin-bottom rounded-lg " +
                    (isPeak
                      ? "bg-[color:var(--color-accent)]"
                      : d.value > 0
                        ? "bg-[color:var(--color-accent)]/50"
                        : "bg-[color:var(--color-surface-3)]")
                  }
                />
              </div>
              <span className="text-[11px] font-medium text-[color:var(--color-text-dim)]">{d.label}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function ProgressGauge({
  intelligence,
  reduceMotion,
}: {
  intelligence: Intelligence;
  reduceMotion: boolean;
}) {
  const pct = intelligence.responseRate;
  const legend = [
    { label: "Responses", value: intelligence.responses, color: "var(--color-accent-ink)" },
    { label: "Interviews", value: intelligence.interviews, color: "var(--color-mint)" },
    { label: "Offers", value: intelligence.offers, color: "var(--color-sky)" },
  ];
  return (
    <div className="flex flex-col items-center">
      <div className="relative w-full max-w-[220px]">
        <svg viewBox="0 0 120 68" className="w-full" role="img" aria-label={`Response rate ${pct}%`}>
          <path
            d="M8 62 A52 52 0 0 1 112 62"
            fill="none"
            stroke="var(--color-surface-3)"
            strokeWidth="13"
            strokeLinecap="round"
            pathLength={100}
          />
          <motion.path
            d="M8 62 A52 52 0 0 1 112 62"
            fill="none"
            stroke="var(--color-accent)"
            strokeWidth="13"
            strokeLinecap="round"
            pathLength={100}
            initial={reduceMotion ? false : { strokeDasharray: "0 100" }}
            animate={{ strokeDasharray: `${pct} 100` }}
            transition={{ duration: 0.9, ease: [0.16, 1, 0.3, 1] }}
          />
        </svg>
        <div className="absolute inset-x-0 bottom-0 flex flex-col items-center">
          <span className="text-[2.1rem] font-extrabold leading-none tracking-[-0.04em] text-[color:var(--color-text)]">
            {pct}%
          </span>
          <span className="mt-1 text-[11px] text-[color:var(--color-text-dim)]">Response rate</span>
        </div>
      </div>
      <div className="mt-6 grid w-full grid-cols-3 gap-2">
        {legend.map((l) => (
          <div key={l.label} className="flex flex-col items-center rounded-xl bg-[color:var(--color-surface-2)] py-2.5">
            <span className="text-lg font-bold text-[color:var(--color-text)]">{l.value}</span>
            <span className="mt-0.5 flex items-center gap-1 text-[10px] text-[color:var(--color-text-dim)]">
              <span className="size-1.5 rounded-full" style={{ backgroundColor: l.color }} />
              {l.label}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

function NextActions({ applications }: { applications: Application[] }) {
  if (!applications.length) {
    return (
      <div className="flex min-h-52 flex-col items-center justify-center text-center">
        <CalendarClock className="size-5 text-[color:var(--color-text-dim)]" />
        <p className="mt-3 text-sm text-[color:var(--color-text-muted)]">No upcoming actions</p>
        <p className="mt-1 max-w-xs text-xs leading-5 text-[color:var(--color-text-dim)]">
          Add a follow-up date to an application to see it here.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-1">
      {applications.slice(0, 5).map((app) => (
        <Link key={app.id} href="/applications" className="product-row group">
          <CompanyAvatar name={app.job.company?.name || "Unknown"} domain={app.job.company?.domain} size={28} />
          <div className="min-w-0 flex-1">
            <div className="truncate text-xs font-semibold text-[color:var(--color-text)]">
              {app.next_action_label || "Follow up"}
            </div>
            <div className="mt-0.5 truncate text-[11px] text-[color:var(--color-text-dim)]">
              {app.job.company?.name || "Unknown company"} / {app.job.title}
            </div>
          </div>
          <span className="shrink-0 text-[11px] font-semibold text-[color:var(--color-accent-ink)]">
            {relativeDate(app.next_action_at)}
          </span>
          <ArrowUpRight className="size-3.5 text-[color:var(--color-text-dim)] transition-transform group-hover:-translate-y-0.5 group-hover:translate-x-0.5 group-hover:text-[color:var(--color-text-muted)]" />
        </Link>
      ))}
    </div>
  );
}

function RecentApplications({ applications }: { applications: Application[] }) {
  return (
    <div className="space-y-1">
      {applications.slice(0, 6).map((app) => (
        <Link key={app.id} href="/applications" className="product-row group">
          <CompanyAvatar name={app.job.company?.name || "Unknown"} domain={app.job.company?.domain} size={30} />
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-semibold text-[color:var(--color-text)]">{app.job.title}</div>
            <div className="mt-0.5 truncate text-[11px] text-[color:var(--color-text-dim)]">
              {app.job.company?.name || "Unknown company"}
            </div>
          </div>
          <StatusPill status={app.status} />
          <span className="hidden w-16 text-right text-[11px] text-[color:var(--color-text-dim)] sm:block">
            {relativeDate(app.updated_at)}
          </span>
          <ArrowUpRight className="size-3.5 text-[color:var(--color-text-dim)] transition group-hover:-translate-y-0.5 group-hover:translate-x-0.5 group-hover:text-[color:var(--color-text-muted)]" />
        </Link>
      ))}
    </div>
  );
}

function FirstLaunch() {
  return (
    <div className="workspace-page">
      <motion.div
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.35 }}
        className="product-empty-state min-h-[420px]"
      >
        <span className="product-icon size-11">
          <FileText className="size-5" />
        </span>
        <h1>Add your first application</h1>
        <p>Import a job posting, track it through the pipeline, and tailor a resume when you are ready.</p>
        <Link href="/jobs" className="product-button product-button-primary">
          <Radar className="size-4" />
          Find roles
        </Link>
      </motion.div>
    </div>
  );
}

function DashboardSkeleton() {
  return (
    <div className="workspace-page max-w-[1560px]" aria-label="Loading dashboard">
      <div className="h-9 w-56 animate-pulse rounded-lg bg-[color:var(--color-surface-hover)]" />
      <div className="mt-3 h-4 w-[24rem] max-w-full animate-pulse rounded bg-[color:var(--color-surface-2)]" />
      <div className="mt-7 grid gap-3.5 sm:grid-cols-2 xl:grid-cols-4">
        {[0, 1, 2, 3].map((item) => (
          <div key={item} className="loading-surface min-h-[150px]" />
        ))}
      </div>
      <div className="mt-3.5 grid gap-3.5 xl:grid-cols-[1.6fr_.7fr]">
        <div className="loading-surface min-h-80" />
        <div className="loading-surface min-h-80" />
      </div>
    </div>
  );
}

type Distribution = { name: string; value: number; color: string };
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
    .map(([status, value]) => ({
      name: STATUS_LABELS[status],
      value,
      color: STATUS_COLORS[status],
    }));
  const nextMoves = applications
    .filter((app) => app.next_action_at)
    .sort((a, b) => new Date(a.next_action_at!).getTime() - new Date(b.next_action_at!).getTime());

  return {
    total: applications.length,
    responses,
    interviews,
    offers,
    responseRate: applied ? Math.round((responses / applied) * 100) : 0,
    interviewRate: applied ? Math.round((interviews / applied) * 100) : 0,
    weekDelta:
      applications.filter((app) => createdWithin(app, 7)).length -
      applications.filter((app) => createdWithin(app, 7, 7)).length,
    last30: daily.reduce((sum, day) => sum + day.value, 0),
    velocity: applications.filter((app) => createdWithin(app, 7)).length,
    daily,
    distribution,
    nextMoves,
    recent: [...applications].sort(
      (a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime(),
    ),
  };
}

function relativeDate(value: string | null) {
  if (!value) return "Not set";
  const delta = new Date(value).getTime() - Date.now();
  const days = Math.round(delta / DAY);
  if (days === 0) return "Today";
  if (days === 1) return "Tomorrow";
  if (days === -1) return "Yesterday";
  if (days > 0 && days < 14) return `In ${days}d`;
  if (days < 0 && days > -14) return `${Math.abs(days)}d ago`;
  return new Date(value).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function signed(value: number) {
  return value > 0 ? `+${value}` : String(value);
}

export type { Distribution };
