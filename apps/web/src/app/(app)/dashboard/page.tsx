"use client";

import { useQuery } from "@tanstack/react-query";
import { eachDayOfInterval, format, isWithinInterval, subDays } from "date-fns";
import { motion } from "framer-motion";
import {
  Briefcase,
  CheckCircle2,
  Clock,
  LineChart,
  type LucideIcon,
  PieChart as PieIcon,
  Sparkles,
  TrendingUp,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import {
  Area,
  AreaChart,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
} from "recharts";
import { EmptyState } from "@/components/empty-state";
import { api } from "@/lib/api";
import type { Application, AppStatus } from "@/lib/types";
import { STATUS_LABELS } from "@/lib/types";

const STATUS_COLORS: Record<AppStatus, string> = {
  wishlist: "#A1A1AE",
  ready_to_apply: "#38BDF8",
  applied: "#8B5CF6",
  oa_received: "#F5B544",
  interview_scheduled: "#34D399",
  offer: "#5EEAD4",
  accepted: "#5EEAD4",
  rejected: "#FF6B8A",
  withdrawn: "#71717A",
  ghosted: "#52525B",
};

export default function DashboardPage() {
  const { data: applications = [], isLoading } = useQuery({
    queryKey: ["applications"],
    queryFn: () => api.listApplications(),
  });

  const stats = useMemo(() => computeStats(applications), [applications]);
  const weeklySeries = useMemo(() => computeWeeklySeries(applications), [applications]);
  const statusDistribution = useMemo(
    () => computeStatusDistribution(applications),
    [applications],
  );

  return (
    <div className="mx-auto max-w-7xl px-8 py-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-3xl font-medium tracking-tight">
            <span className="text-gradient-brand">Welcome back.</span>
          </h1>
          <p className="text-sm text-[color:var(--color-text-muted)]">
            Today across {applications.length} tracked application
            {applications.length === 1 ? "" : "s"}.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Link
            href="/jobs"
            className="rounded-full border border-[color:var(--color-border)] bg-white/[0.03] px-3.5 py-1.5 text-xs hover:bg-white/[0.06]"
          >
            Find jobs
          </Link>
          <Link
            href="/tailor"
            className="inline-flex items-center gap-1.5 rounded-full bg-gradient-brand px-3.5 py-1.5 text-xs font-medium text-white shadow-[var(--shadow-brand-glow)] hover:scale-[1.02]"
          >
            <Sparkles className="size-3.5" /> Tailor a resume
          </Link>
        </div>
      </header>

      {!isLoading && applications.length === 0 && (
        <EmptyState
          icon={Briefcase}
          title="Your dashboard lights up once you add a job"
          description="Add your first job from a URL on Applications, or surf the discovery feed to land one in two clicks."
          cta={{ href: "/jobs", label: "Open Internship Finder" }}
        />
      )}

      {applications.length > 0 && (
        <>
          {/* Stat widget grid */}
          <div className="mt-8 grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <StatWidget
              icon={Briefcase}
              label="Total applications"
              value={stats.total}
              delta={stats.totalDelta}
              trend={stats.totalSpark}
              accent="from-[#6366F1] to-[#A855F7]"
            />
            <StatWidget
              icon={Clock}
              label="Interviews"
              value={stats.interviews}
              delta={stats.interviewDelta}
              trend={stats.interviewSpark}
              accent="from-[#06B6D4] to-[#3B82F6]"
            />
            <StatWidget
              icon={CheckCircle2}
              label="Offers"
              value={stats.offers}
              delta={stats.offerDelta}
              trend={stats.offerSpark}
              accent="from-[#5EEAD4] to-[#34D399]"
            />
            <StatWidget
              icon={TrendingUp}
              label="Response rate"
              value={`${stats.responseRate}%`}
              delta={null}
              trend={stats.responseSpark}
              accent="from-[#F5B544] to-[#FF6B8A]"
            />
          </div>

          {/* Charts */}
          <div className="mt-6 grid grid-cols-1 gap-4 lg:grid-cols-3">
            <ChartCard
              title="Applications · last 30 days"
              subtitle={`${weeklySeries.reduce((s, d) => s + d.count, 0)} this month`}
              icon={LineChart}
              className="lg:col-span-2"
            >
              <ResponsiveContainer width="100%" height={220}>
                <AreaChart data={weeklySeries} margin={{ top: 10, right: 0, left: 0, bottom: 0 }}>
                  <defs>
                    <linearGradient id="appsArea" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#8B5CF6" stopOpacity={0.55} />
                      <stop offset="100%" stopColor="#8B5CF6" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <XAxis
                    dataKey="label"
                    stroke="#71717A"
                    fontSize={10}
                    tickLine={false}
                    axisLine={false}
                    interval={4}
                  />
                  <Tooltip
                    cursor={{ stroke: "#8B5CF6", strokeWidth: 1, strokeOpacity: 0.3 }}
                    contentStyle={{
                      background: "rgba(26,26,36,0.9)",
                      border: "1px solid rgba(255,255,255,0.08)",
                      borderRadius: 12,
                      fontSize: 12,
                    }}
                    labelStyle={{ color: "#F5F5FA" }}
                  />
                  <Area
                    type="monotone"
                    dataKey="count"
                    stroke="#A855F7"
                    strokeWidth={2}
                    fill="url(#appsArea)"
                  />
                </AreaChart>
              </ResponsiveContainer>
            </ChartCard>

            <ChartCard
              title="Status distribution"
              subtitle="Across all tracked apps"
              icon={PieIcon}
            >
              <ResponsiveContainer width="100%" height={220}>
                <PieChart>
                  <Tooltip
                    contentStyle={{
                      background: "rgba(26,26,36,0.9)",
                      border: "1px solid rgba(255,255,255,0.08)",
                      borderRadius: 12,
                      fontSize: 12,
                    }}
                  />
                  <Pie
                    data={statusDistribution}
                    dataKey="value"
                    nameKey="name"
                    innerRadius={50}
                    outerRadius={85}
                    paddingAngle={2}
                    stroke="rgba(255,255,255,0.05)"
                    strokeWidth={1}
                  >
                    {statusDistribution.map((entry) => (
                      <Cell key={entry.name} fill={entry.color} />
                    ))}
                  </Pie>
                </PieChart>
              </ResponsiveContainer>
              <ul className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-[11px]">
                {statusDistribution.map((d) => (
                  <li key={d.name} className="flex items-center gap-1.5">
                    <span
                      className="size-2 shrink-0 rounded-full"
                      style={{ background: d.color }}
                    />
                    <span className="truncate text-[color:var(--color-text-muted)]">
                      {d.name}
                    </span>
                    <span className="ml-auto text-[color:var(--color-text-dim)]">
                      {d.value}
                    </span>
                  </li>
                ))}
              </ul>
            </ChartCard>
          </div>

          {/* Funnel */}
          <ChartCard
            title="Conversion funnel"
            subtitle="From applied to offer"
            icon={TrendingUp}
            className="mt-4"
          >
            <Funnel applications={applications} />
          </ChartCard>
        </>
      )}
    </div>
  );
}

// ---- Stat widget -----------------------------------------------------------

function StatWidget({
  icon: Icon,
  label,
  value,
  delta,
  trend,
  accent,
}: {
  icon: LucideIcon;
  label: string;
  value: number | string;
  delta: number | null;
  trend: { i: number; v: number }[];
  accent: string;
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25 }}
      className="glass hover-lift relative overflow-hidden rounded-[var(--radius-card-lg)] p-5"
    >
      {/* Soft accent glow */}
      <div
        className={`pointer-events-none absolute -right-10 -top-10 size-32 rounded-full bg-gradient-to-br ${accent} opacity-20 blur-3xl`}
      />
      <div className="relative flex items-center justify-between">
        <div
          className={`flex size-9 items-center justify-center rounded-xl bg-gradient-to-br ${accent} text-white shadow-[0_0_30px_-8px_rgba(139,92,246,0.6)]`}
        >
          <Icon className="size-4" />
        </div>
        {delta !== null && delta !== 0 && (
          <span
            className={
              "text-xs font-medium " +
              (delta > 0 ? "text-[color:var(--color-mint)]" : "text-[color:var(--color-rose)]")
            }
          >
            {delta > 0 ? "+" : ""}
            {delta} this wk
          </span>
        )}
      </div>
      <div className="relative mt-4">
        <div className="text-xs uppercase tracking-wider text-[color:var(--color-text-dim)]">
          {label}
        </div>
        <CountUp value={value} className="mt-1 block text-3xl font-semibold tracking-tight" />
      </div>
      {trend.length > 1 && (
        <div className="relative -mx-1 mt-3 h-10">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={trend} margin={{ top: 0, right: 0, left: 0, bottom: 0 }}>
              <defs>
                <linearGradient id={`spark-${label}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#A855F7" stopOpacity={0.6} />
                  <stop offset="100%" stopColor="#A855F7" stopOpacity={0} />
                </linearGradient>
              </defs>
              <Area
                type="monotone"
                dataKey="v"
                stroke="#A855F7"
                strokeWidth={1.5}
                fill={`url(#spark-${label})`}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}
    </motion.div>
  );
}

function CountUp({ value, className = "" }: { value: number | string; className?: string }) {
  const target = typeof value === "string" ? parseFloat(value) : value;
  const isNumber = typeof value === "number" || (typeof value === "string" && !Number.isNaN(target));
  const [displayed, setDisplayed] = useState(0);

  useEffect(() => {
    if (!isNumber) return;
    let frame = 0;
    const start = performance.now();
    const dur = 600;
    const from = 0;
    const to = target;
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / dur);
      const eased = 1 - Math.pow(1 - t, 3);
      setDisplayed(from + (to - from) * eased);
      if (t < 1) frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [target, isNumber]);

  if (!isNumber) return <span className={className}>{value}</span>;
  const isPct = typeof value === "string" && value.includes("%");
  return (
    <span className={className}>
      {Math.round(displayed)}
      {isPct ? "%" : ""}
    </span>
  );
}

// ---- Chart card ------------------------------------------------------------

function ChartCard({
  title,
  subtitle,
  icon: Icon,
  className = "",
  children,
}: {
  title: string;
  subtitle: string;
  icon: LucideIcon;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
      className={`glass rounded-[var(--radius-card-lg)] p-5 ${className}`}
    >
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-medium">{title}</h3>
          <p className="text-xs text-[color:var(--color-text-dim)]">{subtitle}</p>
        </div>
        <Icon className="size-4 text-[color:var(--color-violet)]" />
      </div>
      <div className="mt-3">{children}</div>
    </motion.div>
  );
}

// ---- Conversion funnel -----------------------------------------------------

function Funnel({ applications }: { applications: Application[] }) {
  const tiers: { label: string; statuses: AppStatus[]; color: string }[] = [
    { label: "Applied", statuses: ["applied", "oa_received", "interview_scheduled", "offer", "accepted", "rejected"], color: "#8B5CF6" },
    { label: "OA / Screen", statuses: ["oa_received", "interview_scheduled", "offer", "accepted"], color: "#3B82F6" },
    { label: "Interview", statuses: ["interview_scheduled", "offer", "accepted"], color: "#06B6D4" },
    { label: "Offer", statuses: ["offer", "accepted"], color: "#34D399" },
  ];
  const total = applications.length || 1;
  const rows = tiers.map((t) => ({
    label: t.label,
    color: t.color,
    count: applications.filter((a) => t.statuses.includes(a.status)).length,
  }));
  const peak = rows[0]?.count || 1;
  return (
    <div className="space-y-2">
      {rows.map((r, i) => {
        const pct = (r.count / peak) * 100;
        const ofTotal = ((r.count / total) * 100).toFixed(0);
        return (
          <motion.div
            key={r.label}
            initial={{ opacity: 0, width: 0 }}
            animate={{ opacity: 1, width: "100%" }}
            transition={{ delay: i * 0.08, duration: 0.4 }}
            className="flex items-center gap-3"
          >
            <div className="w-24 shrink-0 text-xs text-[color:var(--color-text-muted)]">
              {r.label}
            </div>
            <div className="relative h-7 flex-1 overflow-hidden rounded-full bg-white/[0.04]">
              <motion.div
                initial={{ width: 0 }}
                animate={{ width: `${pct}%` }}
                transition={{ delay: i * 0.08 + 0.1, duration: 0.5, ease: "easeOut" }}
                className="h-full rounded-full"
                style={{
                  background: `linear-gradient(90deg, ${r.color}, ${r.color}80)`,
                  boxShadow: `0 0 24px -4px ${r.color}80`,
                }}
              />
            </div>
            <div className="w-20 shrink-0 text-right text-xs font-medium">
              {r.count}{" "}
              <span className="text-[color:var(--color-text-dim)]">· {ofTotal}%</span>
            </div>
          </motion.div>
        );
      })}
    </div>
  );
}

// ---- Stats math ------------------------------------------------------------

function computeStats(apps: Application[]) {
  const now = new Date();
  const weekAgo = subDays(now, 7);
  const twoWeeksAgo = subDays(now, 14);

  const inLastWeek = (d: string | null) =>
    d ? isWithinInterval(new Date(d), { start: weekAgo, end: now }) : false;
  const inPrevWeek = (d: string | null) =>
    d
      ? isWithinInterval(new Date(d), { start: twoWeeksAgo, end: weekAgo })
      : false;

  const total = apps.length;
  const interviewStatuses: AppStatus[] = ["interview_scheduled", "offer", "accepted"];
  const interviews = apps.filter((a) => interviewStatuses.includes(a.status)).length;
  const offerStatuses: AppStatus[] = ["offer", "accepted"];
  const offers = apps.filter((a) => offerStatuses.includes(a.status)).length;
  const responded = apps.filter((a) =>
    ["oa_received", "interview_scheduled", "offer", "accepted", "rejected"].includes(a.status),
  ).length;
  const responseRate = total === 0 ? 0 : Math.round((responded / total) * 100);

  const totalDelta =
    apps.filter((a) => inLastWeek(a.created_at)).length -
    apps.filter((a) => inPrevWeek(a.created_at)).length;
  const interviewDelta =
    apps.filter((a) => interviewStatuses.includes(a.status) && inLastWeek(a.updated_at)).length -
    apps.filter((a) => interviewStatuses.includes(a.status) && inPrevWeek(a.updated_at)).length;
  const offerDelta =
    apps.filter((a) => offerStatuses.includes(a.status) && inLastWeek(a.updated_at)).length -
    apps.filter((a) => offerStatuses.includes(a.status) && inPrevWeek(a.updated_at)).length;

  return {
    total,
    interviews,
    offers,
    responseRate,
    totalDelta,
    interviewDelta,
    offerDelta,
    totalSpark: makeSparkline(apps, (a) => a.created_at, 14),
    interviewSpark: makeSparkline(
      apps.filter((a) => interviewStatuses.includes(a.status)),
      (a) => a.updated_at,
      14,
    ),
    offerSpark: makeSparkline(
      apps.filter((a) => offerStatuses.includes(a.status)),
      (a) => a.updated_at,
      14,
    ),
    responseSpark: makeSparkline(
      apps.filter((a) =>
        ["oa_received", "interview_scheduled", "offer", "accepted", "rejected"].includes(a.status),
      ),
      (a) => a.updated_at,
      14,
    ),
  };
}

function makeSparkline(
  apps: Application[],
  pick: (a: Application) => string | null,
  days: number,
) {
  const end = new Date();
  const start = subDays(end, days - 1);
  const dates = eachDayOfInterval({ start, end });
  return dates.map((d, i) => {
    const v = apps.filter((a) => {
      const at = pick(a);
      if (!at) return false;
      const dt = new Date(at);
      return format(dt, "yyyy-MM-dd") === format(d, "yyyy-MM-dd");
    }).length;
    return { i, v };
  });
}

function computeWeeklySeries(apps: Application[]) {
  const end = new Date();
  const start = subDays(end, 29);
  const dates = eachDayOfInterval({ start, end });
  return dates.map((d) => ({
    label: format(d, "MMM d"),
    count: apps.filter((a) => format(new Date(a.created_at), "yyyy-MM-dd") === format(d, "yyyy-MM-dd")).length,
  }));
}

function computeStatusDistribution(apps: Application[]) {
  const counts: Record<string, number> = {};
  for (const a of apps) counts[a.status] = (counts[a.status] ?? 0) + 1;
  return (Object.keys(counts) as AppStatus[])
    .sort((a, b) => counts[b] - counts[a])
    .map((s) => ({
      name: STATUS_LABELS[s],
      value: counts[s],
      color: STATUS_COLORS[s] ?? "#71717A",
    }));
}
