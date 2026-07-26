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

  const itemVariants = {
    hidden: reduceMotion ? {} : { opacity: 0, y: 10 },
    visible: { opacity: 1, y: 0 },
  };

  return (
    <div className="workspace-page max-w-[1560px]">
      <motion.header
        initial={reduceMotion ? false : { opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.35, ease: [0.16, 1, 0.3, 1] }}
        className="mb-6 flex flex-col gap-5 border-b border-white/[0.07] pb-6 md:flex-row md:items-end md:justify-between"
      >
        <div>
          <div className="mb-2 flex items-center gap-2 text-xs font-medium text-[color:var(--color-text-muted)]">
            <DatabaseZap className="size-3.5 text-[color:var(--color-kiwi)]" />
            Live application data
          </div>
          <h1 className="text-3xl font-semibold tracking-[-0.045em] text-white sm:text-4xl">
            Your job search, at a glance
          </h1>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-[color:var(--color-text-muted)]">
            Review momentum, upcoming actions, and recent changes without waiting for the agent service.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Link href="/jobs" className="product-button product-button-secondary">
            <Radar className="size-4" />
            Find roles
          </Link>
          <Link href="/tailor" className="product-button product-button-primary">
            <Sparkles className="size-4" />
            Tailor resume
            <ArrowUpRight className="size-3.5" />
          </Link>
        </div>
      </motion.header>

      <motion.div
        initial="hidden"
        animate="visible"
        variants={{ visible: { transition: { staggerChildren: reduceMotion ? 0 : 0.045 } } }}
        className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4"
      >
        <Metric
          variants={itemVariants}
          icon={BriefcaseBusiness}
          label="Applications"
          value={intelligence.total}
          detail={`${signed(intelligence.weekDelta)} this week`}
        />
        <Metric
          variants={itemVariants}
          icon={TrendingUp}
          label="Response rate"
          value={`${intelligence.responseRate}%`}
          detail={`${intelligence.responses} responses`}
        />
        <Metric
          variants={itemVariants}
          icon={CalendarClock}
          label="Interviews"
          value={intelligence.interviews}
          detail={`${intelligence.interviewRate}% conversion`}
        />
        <Metric
          variants={itemVariants}
          icon={CheckCircle2}
          label="Offers"
          value={intelligence.offers}
          detail={intelligence.offers ? "Offer stage reached" : "No offers yet"}
        />
      </motion.div>

      <motion.div
        initial="hidden"
        animate="visible"
        variants={{ visible: { transition: { staggerChildren: reduceMotion ? 0 : 0.055 } } }}
        className="mt-3 grid gap-3 xl:grid-cols-[minmax(0,1.55fr)_minmax(300px,.75fr)]"
      >
        <DashboardPanel variants={itemVariants} title="Application activity" icon={TrendingUp}>
          <ActivityChart intelligence={intelligence} reduceMotion={Boolean(reduceMotion)} />
        </DashboardPanel>
        <DashboardPanel
          variants={itemVariants}
          title="Next actions"
          icon={Clock3}
          action={{ href: "/calendar", label: "Open calendar" }}
        >
          <NextActions applications={intelligence.nextMoves} />
        </DashboardPanel>
      </motion.div>

      <motion.div
        initial="hidden"
        animate="visible"
        variants={{ visible: { transition: { staggerChildren: reduceMotion ? 0 : 0.055 } } }}
        className="mt-3 grid gap-3 xl:grid-cols-[minmax(0,1.45fr)_minmax(300px,.55fr)]"
      >
        <DashboardPanel
          variants={itemVariants}
          title="Recent applications"
          icon={BriefcaseBusiness}
          action={{ href: "/applications", label: "View all" }}
        >
          <RecentApplications applications={intelligence.recent} />
        </DashboardPanel>
        <DashboardPanel variants={itemVariants} title="Pipeline" icon={DatabaseZap}>
          <PipelineSummary distribution={intelligence.distribution} total={intelligence.total} />
        </DashboardPanel>
      </motion.div>
    </div>
  );
}

function Metric({
  icon: Icon,
  label,
  value,
  detail,
  variants,
}: {
  icon: LucideIcon;
  label: string;
  value: string | number;
  detail: string;
  variants: Variants;
}) {
  return (
    <motion.section variants={variants} className="product-metric">
      <div className="flex items-center justify-between">
        <span className="product-icon">
          <Icon className="size-4" />
        </span>
        <ArrowUpRight className="size-3.5 text-white/20" />
      </div>
      <div className="mt-5 text-3xl font-semibold tracking-[-0.045em] text-white">{value}</div>
      <div className="mt-2 text-xs font-medium text-white/72">{label}</div>
      <div className="mt-1 text-xs text-[color:var(--color-text-dim)]">{detail}</div>
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
      <div className="flex items-center justify-between border-b border-white/[0.06] px-5 py-4">
        <div className="flex items-center gap-2.5">
          <Icon className="size-4 text-[color:var(--color-kiwi)]" />
          <h2 className="text-sm font-semibold text-white/90">{title}</h2>
        </div>
        {action && (
          <Link
            href={action.href}
            className="inline-flex items-center gap-1.5 text-xs font-medium text-[color:var(--color-text-muted)] transition hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-kiwi)]"
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

function ActivityChart({
  intelligence,
  reduceMotion,
}: {
  intelligence: Intelligence;
  reduceMotion: boolean;
}) {
  const peak = Math.max(...intelligence.daily.map((day) => day.value), 1);
  const points = intelligence.daily
    .map((day, index) => {
      const x = (index / Math.max(intelligence.daily.length - 1, 1)) * 100;
      const y = 82 - (day.value / peak) * 62;
      return `${x},${y}`;
    })
    .join(" ");

  return (
    <div>
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="text-2xl font-semibold tracking-tight">{intelligence.last30}</div>
          <p className="mt-1 text-xs text-[color:var(--color-text-dim)]">Applications added in 30 days</p>
        </div>
        <div className="inline-flex items-center gap-1.5 text-xs text-[color:var(--color-text-muted)]">
          <TrendingUp className="size-3.5 text-[color:var(--color-kiwi)]" />
          {intelligence.velocity} this week
        </div>
      </div>
      <div className="relative mt-6 h-52 overflow-hidden rounded-xl border border-white/[0.055] bg-black/20 px-3 py-4">
        <div className="product-chart-grid absolute inset-0" />
        <svg viewBox="0 0 100 100" preserveAspectRatio="none" className="relative h-full w-full" role="img" aria-label="Applications added over the last 30 days">
          <defs>
            <linearGradient id="activity-fill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stopColor="#9AA7FF" stopOpacity=".2" />
              <stop offset="1" stopColor="#9AA7FF" stopOpacity="0" />
            </linearGradient>
          </defs>
          <polygon points={`0,100 ${points} 100,100`} fill="url(#activity-fill)" />
          <motion.polyline
            points={points}
            fill="none"
            stroke="#AEB8EE"
            strokeWidth="1.6"
            vectorEffect="non-scaling-stroke"
            strokeLinecap="round"
            strokeLinejoin="round"
            initial={reduceMotion ? false : { pathLength: 0, opacity: 0 }}
            animate={{ pathLength: 1, opacity: 1 }}
            transition={{ duration: 0.8, ease: [0.16, 1, 0.3, 1] }}
          />
        </svg>
        <span className="absolute bottom-3 left-4 text-[10px] text-white/25">30 days ago</span>
        <span className="absolute bottom-3 right-4 text-[10px] text-white/25">Today</span>
      </div>
    </div>
  );
}

function NextActions({ applications }: { applications: Application[] }) {
  if (!applications.length) {
    return (
      <div className="flex min-h-52 flex-col items-center justify-center text-center">
        <CalendarClock className="size-5 text-white/25" />
        <p className="mt-3 text-sm text-white/65">No upcoming actions</p>
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
          <CompanyAvatar name={app.job.company?.name || "Unknown"} size={28} />
          <div className="min-w-0 flex-1">
            <div className="truncate text-xs font-medium text-white/82">
              {app.next_action_label || "Follow up"}
            </div>
            <div className="mt-0.5 truncate text-[11px] text-[color:var(--color-text-dim)]">
              {app.job.company?.name || "Unknown company"} / {app.job.title}
            </div>
          </div>
          <span className="shrink-0 text-[11px] font-medium text-[color:var(--color-kiwi)]">
            {relativeDate(app.next_action_at)}
          </span>
          <ArrowUpRight className="size-3.5 text-white/18 transition-transform group-hover:-translate-y-0.5 group-hover:translate-x-0.5 group-hover:text-white/60" />
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
          <CompanyAvatar name={app.job.company?.name || "Unknown"} size={30} />
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-medium text-white/82">{app.job.title}</div>
            <div className="mt-0.5 truncate text-[11px] text-[color:var(--color-text-dim)]">
              {app.job.company?.name || "Unknown company"}
            </div>
          </div>
          <StatusPill status={app.status} />
          <span className="hidden w-16 text-right text-[11px] text-white/28 sm:block">
            {relativeDate(app.updated_at)}
          </span>
          <ArrowUpRight className="size-3.5 text-white/15 transition group-hover:-translate-y-0.5 group-hover:translate-x-0.5 group-hover:text-white/60" />
        </Link>
      ))}
    </div>
  );
}

function PipelineSummary({
  distribution,
  total,
}: {
  distribution: Distribution[];
  total: number;
}) {
  return (
    <div className="space-y-4">
      {distribution.slice(0, 7).map((item) => {
        const share = total ? Math.round((item.value / total) * 100) : 0;
        return (
          <div key={item.name}>
            <div className="flex items-center gap-2 text-xs">
              <span className="size-2 rounded-full" style={{ backgroundColor: item.color }} />
              <span className="flex-1 text-white/62">{item.name}</span>
              <span className="font-medium text-white/85">{item.value}</span>
              <span className="w-8 text-right text-white/28">{share}%</span>
            </div>
            <div className="mt-2 h-px bg-white/[0.055]">
              <div
                className="h-px origin-left"
                style={{ width: `${share}%`, backgroundColor: item.color }}
              />
            </div>
          </div>
        );
      })}
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
      <div className="h-8 w-72 animate-pulse rounded-lg bg-white/[0.05]" />
      <div className="mt-3 h-4 w-[28rem] max-w-full animate-pulse rounded bg-white/[0.035]" />
      <div className="mt-8 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {[0, 1, 2, 3].map((item) => (
          <div key={item} className="loading-surface min-h-36" />
        ))}
      </div>
      <div className="mt-3 grid gap-3 xl:grid-cols-[1.55fr_.75fr]">
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
