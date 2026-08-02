"use client";

import { useQuery } from "@tanstack/react-query";
import { motion, useReducedMotion, type Variants } from "framer-motion";
import {
  ArrowUpRight,
  CalendarClock,
  DatabaseZap,
  FileText,
  Radar,
  Sparkles,
  TrendingUp,
} from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";
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

      {/* One bordered slab split by hairlines rather than four floating cards.
          Four separate surfaces made four separate things to look at; a single
          row reads as one summary, and drops the icon chips and corner arrows
          that were decorating numbers rather than explaining them.

          The hairlines are a 1px grid gap over a border-coloured background, so
          they stay perfect at every breakpoint instead of needing per-cell
          border rules that break the moment the grid rewraps. */}
      <motion.div
        initial="hidden"
        animate="visible"
        variants={{ visible: { transition: { staggerChildren: reduceMotion ? 0 : 0.05 } } }}
        className="grid gap-px overflow-hidden rounded-2xl border border-[color:var(--color-border)] bg-[color:var(--color-border)] sm:grid-cols-2 xl:grid-cols-4"
      >
        <StatCell
          variants={itemVariants}
          label="Total applications"
          value={intelligence.total}
          delta={`${signed(intelligence.weekDelta)}`}
          note="this week"
          positive={intelligence.weekDelta >= 0}
        />
        <StatCell
          variants={itemVariants}
          label="Response rate"
          value={`${intelligence.responseRate}%`}
          delta={String(intelligence.responses)}
          note="responses"
          positive={intelligence.responses > 0}
        />
        <StatCell
          variants={itemVariants}
          label="Interviews"
          value={intelligence.interviews}
          delta={`${intelligence.interviewRate}%`}
          note="conversion"
          positive={intelligence.interviews > 0}
        />
        <StatCell
          variants={itemVariants}
          label="Offers"
          value={intelligence.offers}
          delta={intelligence.offers ? String(intelligence.offers) : "None"}
          note={intelligence.offers ? "reached offer" : "yet"}
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
        <DashboardPanel
          variants={itemVariants}
          title="Application activity"
          subtitle="Applications added per day."
          badge={intelligence.velocity > 0 ? `${intelligence.velocity} this week` : undefined}
        >
          <ActivityChart intelligence={intelligence} reduceMotion={Boolean(reduceMotion)} />
        </DashboardPanel>
        <DashboardPanel
          variants={itemVariants}
          title="Pipeline progress"
          subtitle="How far your applications have moved."
        >
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
          subtitle="Latest updates across your pipeline."
          action={{ href: "/applications", label: "View all" }}
        >
          <RecentApplications applications={intelligence.recent} />
        </DashboardPanel>
        <DashboardPanel
          variants={itemVariants}
          title="Next actions"
          subtitle="Follow-ups you have dated."
          action={{ href: "/calendar", label: "Calendar" }}
        >
          <NextActions applications={intelligence.nextMoves} />
        </DashboardPanel>
      </motion.div>
    </div>
  );
}

/**
 * One number in the summary row.
 *
 * Label above, figure, then the movement behind it. The order matters: the
 * figure is what the eye lands on, so it gets the size, and everything else is
 * quiet enough to stay out of its way. Only the delta carries colour, and only
 * to say which direction it went.
 */
function StatCell({
  label,
  value,
  delta,
  note,
  positive,
  variants,
}: {
  label: string;
  value: string | number;
  delta: string;
  note: string;
  positive?: boolean;
  variants: Variants;
}) {
  return (
    <motion.section
      variants={variants}
      className="bg-[color:var(--color-surface-1)] p-5 transition-colors hover:bg-[color:var(--color-surface-2)]"
    >
      <div className="text-xs font-medium text-[color:var(--color-text-muted)]">
        {label}
      </div>
      <div className="mt-3 text-[2.1rem] font-semibold leading-none tracking-[-0.04em] tabular-nums text-[color:var(--color-text)]">
        {value}
      </div>
      <div className="mt-3 flex items-center gap-1.5 text-xs">
        <TrendingUp
          aria-hidden="true"
          className={
            "size-3.5 " +
            (positive
              ? "text-[color:var(--color-mint-ink)]"
              : "rotate-180 text-[color:var(--color-text-dim)]")
          }
        />
        <span
          className={
            "font-semibold tabular-nums " +
            (positive
              ? "text-[color:var(--color-mint-ink)]"
              : "text-[color:var(--color-text-dim)]")
          }
        >
          {delta}
        </span>
        <span className="text-[color:var(--color-text-dim)]">{note}</span>
      </div>
    </motion.section>
  );
}

/**
 * A titled panel.
 *
 * The icon chip that used to sit beside every title is gone. It was the same
 * jasmine square on all four panels, so it distinguished nothing and simply
 * pushed the title right. A short subtitle says what the panel is actually
 * showing, which the icon never did.
 */
function DashboardPanel({
  title,
  subtitle,
  badge,
  action,
  children,
  variants,
}: {
  title: string;
  subtitle?: string;
  badge?: string;
  action?: { href: string; label: string };
  children: React.ReactNode;
  variants: Variants;
}) {
  return (
    <motion.section variants={variants} className="product-panel">
      <div className="flex items-start justify-between gap-4 border-b border-[color:var(--color-border)] px-5 py-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-semibold text-[color:var(--color-text)]">{title}</h2>
            {badge && (
              <span className="inline-flex items-center gap-1 rounded-full bg-[color:var(--color-mint)]/12 px-2 py-0.5 text-[11px] font-semibold tabular-nums text-[color:var(--color-mint-ink)]">
                <TrendingUp className="size-3" aria-hidden="true" />
                {badge}
              </span>
            )}
          </div>
          {subtitle && (
            <p className="mt-1 truncate text-xs text-[color:var(--color-text-dim)]">
              {subtitle}
            </p>
          )}
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

type RangeKey = "week" | "month";

/**
 * Applications added per day.
 *
 * The range switch is not decoration: over seven days this chart is usually one
 * bar and six blanks, which looks broken rather than quiet. Thirty days is
 * already computed for the totals, so the wider view costs nothing and is the
 * one that actually shows a shape.
 *
 * Only two ranges. A "1 year" or "All" tab would be inventing options the data
 * cannot fill, and an empty range is worse than an absent one.
 */
function ActivityChart({
  intelligence,
  reduceMotion,
}: {
  intelligence: Intelligence;
  reduceMotion: boolean;
}) {
  const [range, setRange] = useState<RangeKey>("week");
  const count = range === "week" ? 7 : 30;
  const now = Date.now();
  const series = intelligence.daily.slice(-count).map((day, i) => {
    const date = new Date(now - (count - 1 - i) * DAY);
    return {
      value: day.value,
      // A weekday initial reads well across seven columns and turns to mush
      // across thirty, where the date is both shorter and more useful.
      label: count === 7 ? WEEKDAY[date.getDay()] : String(date.getDate()),
      // "S, M, T, W, T, F, S" is fine to look at and useless to listen to.
      spokenLabel: date.toLocaleDateString(undefined, {
        month: "short",
        day: "numeric",
      }),
    };
  });
  const peak = Math.max(...series.map((d) => d.value), 1);
  const busiest = series.reduce((a, b) => (b.value >= a.value ? b : a), series[0]);
  // Whole numbers only: these are counts, and a gridline at 0.5 applications
  // would be measuring something that cannot happen.
  const ticks =
    peak <= 3
      ? Array.from({ length: peak + 1 }, (_, i) => i)
      : [0, Math.round(peak / 2), peak];
  // Every column labelled works at seven and collides at thirty.
  const labelEvery = count === 7 ? 1 : 5;

  return (
    <div>
      <div
        role="group"
        aria-label="Chart range"
        className="mb-6 inline-flex rounded-full bg-[color:var(--color-surface-2)] p-1"
      >
        {(
          [
            ["week", "1 week"],
            ["month", "1 month"],
          ] as [RangeKey, string][]
        ).map(([key, label]) => (
          <button
            key={key}
            type="button"
            onClick={() => setRange(key)}
            aria-pressed={range === key}
            className={
              "rounded-full px-3.5 py-1 text-xs transition " +
              (range === key
                ? "bg-[color:var(--color-surface-1)] font-semibold text-[color:var(--color-text)] shadow-[var(--shadow-xs)]"
                : "text-[color:var(--color-text-muted)] hover:text-[color:var(--color-text)]")
            }
          >
            {label}
          </button>
        ))}
      </div>

      <div className="flex gap-3">
        {/* Axis labels sit outside the plot so the gridlines can run the full
            width without text interrupting them. */}
        <div className="flex h-44 w-6 shrink-0 flex-col-reverse justify-between text-right text-[10px] tabular-nums leading-none text-[color:var(--color-text-dim)]">
          {ticks.map((t) => (
            <span key={t}>{t}</span>
          ))}
        </div>

        <div className="relative min-w-0 flex-1">
          {/* Positioned by value rather than spread evenly, so a line always
              means the number printed beside it. */}
          <div aria-hidden="true" className="absolute inset-x-0 top-0 h-44">
            {ticks.map((t) => (
              <div
                key={t}
                className="absolute inset-x-0 border-t border-dashed border-[color:var(--color-border)]"
                style={{ bottom: `${(t / peak) * 100}%` }}
              />
            ))}
          </div>

          <div
            role="img"
            aria-label={`Applications added per day over the last ${count} days: ${series
              .map((d) => `${d.spokenLabel} ${d.value}`)
              .join(", ")}.`}
            className="relative flex h-44 items-end gap-1.5"
          >
            {series.map((d, i) => {
              const h = d.value > 0 ? Math.max((d.value / peak) * 100, 6) : 2;
              const isPeak = d.value === busiest.value && d.value > 0;
              return (
                <motion.div
                  key={i}
                  style={{ height: `${h}%` }}
                  initial={reduceMotion ? false : { scaleY: 0 }}
                  animate={{ scaleY: 1 }}
                  transition={{
                    duration: 0.55,
                    delay: reduceMotion ? 0 : i * (count === 7 ? 0.05 : 0.012),
                    ease: [0.16, 1, 0.3, 1],
                  }}
                  title={`${d.spokenLabel}: ${d.value}`}
                  className={
                    "min-w-0 flex-1 origin-bottom rounded-full " +
                    (isPeak
                      ? "bg-[color:var(--color-accent)]"
                      : d.value > 0
                        ? "bg-[color:var(--color-accent)]/45"
                        : "bg-[color:var(--color-surface-3)]")
                  }
                />
              );
            })}
          </div>

          <div className="mt-2.5 flex gap-1.5">
            {series.map((d, i) => (
              <span
                key={i}
                className="min-w-0 flex-1 text-center text-[11px] font-medium text-[color:var(--color-text-dim)]"
              >
                {i % labelEvery === 0 ? d.label : ""}
              </span>
            ))}
          </div>
        </div>
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
          <span className="text-[2.1rem] font-extrabold leading-none tracking-[-0.04em] tabular-nums text-[color:var(--color-text)]">
            {pct}%
          </span>
          <span className="mt-1 text-[11px] text-[color:var(--color-text-dim)]">Response rate</span>
        </div>
      </div>
      <div className="mt-6 grid w-full grid-cols-3 gap-2">
        {legend.map((l) => (
          <div key={l.label} className="flex flex-col items-center rounded-xl bg-[color:var(--color-surface-2)] py-2.5">
            <span className="text-lg font-bold tabular-nums text-[color:var(--color-text)]">{l.value}</span>
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
    <div className="workspace-page max-w-[1560px]" role="status" aria-label="Loading dashboard">
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
