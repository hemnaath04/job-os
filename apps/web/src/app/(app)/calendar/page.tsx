"use client";

import { useQuery } from "@tanstack/react-query";
import { format, isPast, isThisWeek, isToday, parseISO } from "date-fns";
import { Building2, Calendar as CalendarIcon } from "lucide-react";
import Link from "next/link";
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
    <div className="mx-auto max-w-4xl px-8 py-6">
      <header>
        <h1 className="text-2xl font-medium tracking-tight">Calendar</h1>
        <p className="text-sm text-[color:var(--color-text-muted)]">
          {entries.length} upcoming follow-up{entries.length === 1 ? "" : "s"}.
          Set next-action dates from each application.
        </p>
      </header>

      {isLoading && (
        <div className="mt-8 text-sm text-[color:var(--color-text-muted)]">
          loading…
        </div>
      )}

      {!isLoading && entries.length === 0 && (
        <div className="glass mt-8 rounded-[var(--radius-card)] p-8 text-center">
          <CalendarIcon className="mx-auto size-6 text-[color:var(--color-violet)]" />
          <h3 className="mt-3 text-base font-medium">No upcoming follow-ups</h3>
          <p className="mx-auto mt-1 max-w-md text-sm text-[color:var(--color-text-muted)]">
            On the{" "}
            <Link
              href="/applications"
              className="text-[color:var(--color-violet)] underline"
            >
              Applications
            </Link>{" "}
            page, click into an application and set a next-action date — it will
            land here.
          </p>
        </div>
      )}

      {!isLoading && entries.length > 0 && (
        <div className="mt-6 space-y-6">
          {BUCKETS.map(({ id, label }) => {
            const items = grouped[id];
            if (items.length === 0) return null;
            return (
              <section key={id}>
                <h2 className="text-xs font-medium uppercase tracking-wider text-[color:var(--color-text-dim)]">
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
        className="glass flex items-start justify-between rounded-[var(--radius-card)] px-4 py-3 hover:bg-white/[0.04]"
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
