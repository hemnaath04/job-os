"use client";

import { useQuery } from "@tanstack/react-query";
import { format, isPast, isThisWeek, isToday, parseISO } from "date-fns";
import { Building2, Calendar as CalendarIcon } from "lucide-react";
import Link from "next/link";
import { EmptyState } from "@/components/empty-state";
import { InfoChip, PageIntro } from "@/components/page-intro";
import { api } from "@/lib/api";
import type { CalendarEntry } from "@/lib/types";
import { STATUS_LABELS } from "@/lib/types";

type Bucket = "overdue" | "today" | "this_week" | "later";

const BUCKETS: { id: Bucket; label: string }[] = [
  { id: "overdue", label: "Overdue" },
  { id: "today", label: "Today" },
  { id: "this_week", label: "This week" },
  { id: "later", label: "Later" },
];

function bucketOf(entry: CalendarEntry): Bucket {
  const when = parseISO(entry.when);
  if (isPast(when) && !isToday(when)) return "overdue";
  if (isToday(when)) return "today";
  if (isThisWeek(when, { weekStartsOn: 1 })) return "this_week";
  return "later";
}

export default function CalendarPage() {
  const { data: entries = [], isLoading } = useQuery({
    queryKey: ["calendar", "upcoming"],
    queryFn: () => api.listCalendar({ days: 120, include_past: 14 }),
  });

  const grouped: Record<Bucket, CalendarEntry[]> = {
    overdue: [],
    today: [],
    this_week: [],
    later: [],
  };
  for (const e of entries) grouped[bucketOf(e)].push(e);

  return (
    <div className="workspace-page max-w-6xl">
      <PageIntro
        eyebrow="Next-action timeline"
        title="Calendar"
        description="Deadlines and follow-ups arranged by urgency, not buried in a generic month grid. Every item links straight back to its application."
        icon={CalendarIcon}
      >
        <InfoChip tone="sage">{entries.length} upcoming</InfoChip>
        <InfoChip>{grouped.overdue.length} overdue</InfoChip>
        <InfoChip tone="clay">120-day horizon</InfoChip>
      </PageIntro>

      {isLoading && (
        <div className="loading-surface mt-6" />
      )}

      {!isLoading && entries.length === 0 && (
        <EmptyState
          icon={CalendarIcon}
          title="No upcoming follow-ups"
          description="Set a next-action date on any application and it will land here, bucketed by urgency."
          cta={{ href: "/applications", label: "Open Applications" }}
        />
      )}

      {!isLoading && entries.length > 0 && (
        <div className="relative mt-7 space-y-8 before:absolute before:bottom-2 before:left-[6px] before:top-3 before:w-px before:bg-gradient-to-b before:from-[#9AA7FF]/35 before:via-white/10 before:to-transparent">
          {BUCKETS.map(({ id, label }) => {
            const items = grouped[id];
            if (items.length === 0) return null;
            return (
              <section key={id} className="relative pl-7">
                <span className="absolute left-0 top-1 size-3 rounded-full border-2 border-[#15181D] bg-[#9AA7FF] shadow-[0_0_0_1px_rgba(154,167,255,.25)]" />
                <h2 className="section-kicker">
                  {label}
                  <span className="ml-2 text-[color:var(--color-text-muted)]">
                    {items.length}
                  </span>
                </h2>
                <ul className="mt-2 space-y-2">
                  {items.map((e) => (
                    <EntryRow
                      key={e.application_id + e.when}
                      entry={e}
                      overdue={id === "overdue"}
                    />
                  ))}
                </ul>
              </section>
            );
          })}
        </div>
      )}
    </div>
  );
}

function EntryRow({
  entry,
  overdue,
}: {
  entry: CalendarEntry;
  overdue: boolean;
}) {
  const when = parseISO(entry.when);
  const company = entry.company_name ?? "Unknown";
  return (
    <li>
      <Link
        href="/applications"
        className="workspace-panel workspace-panel-interactive flex items-start justify-between px-5 py-4"
      >
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-sm">
            <span className="font-medium">{entry.label}</span>
            <span className="rounded-full bg-white/[0.04] px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-[color:var(--color-text-muted)]">
              {STATUS_LABELS[entry.status]}
            </span>
          </div>
          <div className="mt-1 flex items-center gap-1 text-xs text-[color:var(--color-text-muted)]">
            <Building2 className="size-3" /> {company} · {entry.job_title}
          </div>
        </div>
        <div
          className={`shrink-0 text-right text-xs ${
            overdue ? "text-rose-300" : "text-[color:var(--color-text-muted)]"
          }`}
        >
          <div>{format(when, "EEE, MMM d")}</div>
          <div className="text-[10px] text-[color:var(--color-text-dim)]">
            {format(when, "h:mm a")}
          </div>
        </div>
      </Link>
    </li>
  );
}
