"use client";

import { useQuery } from "@tanstack/react-query";
import {
  addMonths,
  eachDayOfInterval,
  endOfMonth,
  endOfWeek,
  format,
  isPast,
  isSameDay,
  isSameMonth,
  isThisWeek,
  isToday,
  parseISO,
  startOfMonth,
  startOfWeek,
  subMonths,
} from "date-fns";
import {
  Building2,
  Calendar as CalendarIcon,
  ChevronLeft,
  ChevronRight,
  List,
} from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";
import { EmptyState } from "@/components/empty-state";
import { InfoChip, PageIntro } from "@/components/page-intro";
import { api } from "@/lib/api";
import type { AppStatus, CalendarEntry, CalendarHistoryEntry } from "@/lib/types";
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

/** One color per status, reused for both the month grid's dots and the legend. */
const STATUS_COLOR: Record<AppStatus, string> = {
  wishlist: "var(--color-text-dim)",
  ready_to_apply: "var(--color-text-dim)",
  applied: "var(--color-accent-ink)",
  oa_received: "var(--color-amber)",
  interview_scheduled: "var(--color-violet)",
  offer: "var(--color-mint-ink)",
  accepted: "var(--color-mint-ink)",
  rejected: "var(--color-rose-ink)",
  withdrawn: "var(--color-text-dim)",
  ghosted: "var(--color-text-dim)",
};

type DayEvent =
  | { kind: "upcoming"; date: Date; entry: CalendarEntry }
  | { kind: "history"; date: Date; entry: CalendarHistoryEntry };

export default function CalendarPage() {
  const [view, setView] = useState<"month" | "timeline">("month");
  const { data: upcoming = [], isLoading: loadingUpcoming } = useQuery({
    queryKey: ["calendar", "upcoming"],
    queryFn: () => api.listCalendar({ days: 120, include_past: 14 }),
  });
  const { data: history = [], isLoading: loadingHistory } = useQuery({
    queryKey: ["calendar", "history"],
    queryFn: () => api.listCalendarHistory({ days: 180 }),
  });
  const isLoading = loadingUpcoming || loadingHistory;

  const events = useMemo<DayEvent[]>(() => {
    const fromUpcoming: DayEvent[] = upcoming.map((entry) => ({
      kind: "upcoming",
      date: parseISO(entry.when),
      entry,
    }));
    const fromHistory: DayEvent[] = history.map((entry) => ({
      kind: "history",
      date: parseISO(entry.occurred_at),
      entry,
    }));
    return [...fromUpcoming, ...fromHistory];
  }, [upcoming, history]);

  const grouped: Record<Bucket, CalendarEntry[]> = {
    overdue: [],
    today: [],
    this_week: [],
    later: [],
  };
  for (const e of upcoming) grouped[bucketOf(e)].push(e);

  return (
    <div className="workspace-page max-w-6xl">
      <PageIntro
        eyebrow="Pipeline calendar"
        title="Calendar"
        description="A real month view of when things actually happened — applied, rejected, offers — alongside the follow-ups still ahead of you."
        icon={CalendarIcon}
        action={
          <div
            role="group"
            aria-label="Calendar view"
            className="inline-flex rounded-full border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] p-0.5 text-xs"
          >
            <button
              onClick={() => setView("month")}
              aria-pressed={view === "month"}
              className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 transition ${
                view === "month"
                  ? "bg-[color:var(--color-surface-hover)] text-[color:var(--color-text)] shadow-sm"
                  : "text-[color:var(--color-text-muted)] hover:text-[color:var(--color-text)]"
              }`}
            >
              <CalendarIcon className="size-3.5" /> Month
            </button>
            <button
              onClick={() => setView("timeline")}
              aria-pressed={view === "timeline"}
              className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 transition ${
                view === "timeline"
                  ? "bg-[color:var(--color-surface-hover)] text-[color:var(--color-text)] shadow-sm"
                  : "text-[color:var(--color-text-muted)] hover:text-[color:var(--color-text)]"
              }`}
            >
              <List className="size-3.5" /> Timeline
            </button>
          </div>
        }
      >
        <InfoChip tone="sage">{upcoming.length} upcoming</InfoChip>
        <InfoChip>{grouped.overdue.length} overdue</InfoChip>
        <InfoChip tone="clay">{history.length} past 180 days</InfoChip>
      </PageIntro>

      {isLoading && <div className="loading-surface mt-6" />}

      {!isLoading && events.length === 0 && (
        <EmptyState
          icon={CalendarIcon}
          title="Nothing to show yet"
          description="Set a next-action date on any application, or move one through the pipeline, and it'll show up here."
          cta={{ href: "/applications", label: "Open Applications" }}
        />
      )}

      {!isLoading && events.length > 0 && view === "month" && <MonthView events={events} />}

      {!isLoading && events.length > 0 && view === "timeline" && (
        <div className="relative mt-7 space-y-8 before:absolute before:bottom-2 before:left-[6px] before:top-3 before:w-px before:bg-gradient-to-b before:from-[#8A6D12]/35 before:via-[color:var(--color-border-strong)] before:to-transparent">
          {BUCKETS.map(({ id, label }) => {
            const items = grouped[id];
            if (items.length === 0) return null;
            return (
              <section key={id} className="relative pl-7">
                <span className="absolute left-0 top-1 size-3 rounded-full border-2 border-[color:var(--color-bg)] bg-[color:var(--color-accent)] shadow-[0_0_0_1px_rgba(233,198,74,.35)]" />
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

function MonthView({ events }: { events: DayEvent[] }) {
  const [cursor, setCursor] = useState(() => startOfMonth(new Date()));
  const [selected, setSelected] = useState<Date | null>(null);

  const days = useMemo(() => {
    const start = startOfWeek(startOfMonth(cursor), { weekStartsOn: 1 });
    const end = endOfWeek(endOfMonth(cursor), { weekStartsOn: 1 });
    return eachDayOfInterval({ start, end });
  }, [cursor]);

  const eventsByDay = useMemo(() => {
    const map = new Map<string, DayEvent[]>();
    for (const event of events) {
      const key = format(event.date, "yyyy-MM-dd");
      const list = map.get(key) ?? [];
      list.push(event);
      map.set(key, list);
    }
    return map;
  }, [events]);

  const selectedEvents = selected
    ? (eventsByDay.get(format(selected, "yyyy-MM-dd")) ?? [])
    : [];

  return (
    <div className="mt-6">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-medium tracking-[-0.01em]">{format(cursor, "MMMM yyyy")}</h2>
        <div className="flex items-center gap-1">
          <button
            onClick={() => setCursor((c) => subMonths(c, 1))}
            aria-label="Previous month"
            className="rounded-lg p-1.5 text-[color:var(--color-text-muted)] transition hover:bg-[color:var(--color-surface-2)] hover:text-[color:var(--color-text)]"
          >
            <ChevronLeft className="size-4" />
          </button>
          <button
            onClick={() => setCursor(startOfMonth(new Date()))}
            className="rounded-full border border-[color:var(--color-border)] px-3 py-1 text-xs text-[color:var(--color-text-muted)] transition hover:bg-[color:var(--color-surface-2)] hover:text-[color:var(--color-text)]"
          >
            Today
          </button>
          <button
            onClick={() => setCursor((c) => addMonths(c, 1))}
            aria-label="Next month"
            className="rounded-lg p-1.5 text-[color:var(--color-text-muted)] transition hover:bg-[color:var(--color-surface-2)] hover:text-[color:var(--color-text)]"
          >
            <ChevronRight className="size-4" />
          </button>
        </div>
      </div>

      <div className="mt-4 grid grid-cols-7 gap-px overflow-hidden rounded-[var(--radius-card-lg)] border border-[color:var(--color-border)] bg-[color:var(--color-border)]">
        {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((label) => (
          <div
            key={label}
            className="bg-[color:var(--color-surface-1)] px-2 py-1.5 text-center text-[10px] font-medium uppercase tracking-wide text-[color:var(--color-text-dim)]"
          >
            {label}
          </div>
        ))}
        {days.map((day) => {
          const key = format(day, "yyyy-MM-dd");
          const dayEvents = eventsByDay.get(key) ?? [];
          const inMonth = isSameMonth(day, cursor);
          const isSelected = selected && isSameDay(day, selected);
          return (
            <button
              key={key}
              onClick={() => setSelected(day)}
              className={`flex min-h-20 flex-col items-start gap-1 bg-[color:var(--color-surface-1)] p-1.5 text-left transition hover:bg-[color:var(--color-surface-2)] sm:min-h-24 ${
                !inMonth ? "opacity-40" : ""
              } ${isSelected ? "ring-2 ring-inset ring-[color:var(--color-accent-ink)]" : ""}`}
            >
              <span
                className={`flex size-5 items-center justify-center rounded-full text-[11px] ${
                  isToday(day)
                    ? "bg-gradient-brand font-semibold text-[color:var(--color-on-accent)]"
                    : "text-[color:var(--color-text-muted)]"
                }`}
              >
                {format(day, "d")}
              </span>
              <div className="flex flex-wrap gap-1">
                {dayEvents.slice(0, 4).map((event, i) => (
                  <span
                    key={i}
                    className="size-1.5 shrink-0 rounded-full"
                    style={{
                      backgroundColor: STATUS_COLOR[event.entry.status],
                    }}
                  />
                ))}
                {dayEvents.length > 4 && (
                  <span className="text-[9px] text-[color:var(--color-text-dim)]">
                    +{dayEvents.length - 4}
                  </span>
                )}
              </div>
            </button>
          );
        })}
      </div>

      <div className="mt-4 flex flex-wrap gap-3 text-[11px] text-[color:var(--color-text-dim)]">
        {(
          ["applied", "interview_scheduled", "offer", "rejected", "oa_received"] as AppStatus[]
        ).map((status) => (
          <span key={status} className="inline-flex items-center gap-1.5">
            <span
              className="size-1.5 rounded-full"
              style={{ backgroundColor: STATUS_COLOR[status] }}
            />
            {STATUS_LABELS[status]}
          </span>
        ))}
      </div>

      <div className="mt-6">
        <h3 className="text-sm font-semibold">
          {selected ? format(selected, "EEEE, MMMM d") : "Select a day"}
        </h3>
        {selected && selectedEvents.length === 0 && (
          <p className="mt-2 text-xs text-[color:var(--color-text-dim)]">Nothing on this day.</p>
        )}
        {selectedEvents.length > 0 && (
          <ul className="mt-2 space-y-2">
            {selectedEvents.map((event, i) => (
              <li key={i}>
                <Link
                  href="/applications"
                  className="workspace-panel workspace-panel-interactive flex items-center justify-between px-4 py-3"
                >
                  <div className="min-w-0">
                    <div className="flex items-center gap-2 text-sm">
                      <span
                        className="size-2 shrink-0 rounded-full"
                        style={{ backgroundColor: STATUS_COLOR[event.entry.status] }}
                      />
                      <span className="font-medium">
                        {event.kind === "upcoming"
                          ? event.entry.label
                          : `Moved to ${STATUS_LABELS[event.entry.status]}`}
                      </span>
                    </div>
                    <div className="mt-1 flex items-center gap-1 pl-4 text-xs text-[color:var(--color-text-muted)]">
                      <Building2 className="size-3" />
                      {event.entry.company_name ?? "Unknown"} · {event.entry.job_title}
                    </div>
                  </div>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>
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
            <span className="rounded-full bg-[color:var(--color-surface-2)] px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-[color:var(--color-text-muted)]">
              {STATUS_LABELS[entry.status]}
            </span>
          </div>
          <div className="mt-1 flex items-center gap-1 text-xs text-[color:var(--color-text-muted)]">
            <Building2 className="size-3" /> {company} · {entry.job_title}
          </div>
        </div>
        <div
          className={`shrink-0 text-right text-xs ${
            overdue ? "text-[color:var(--color-rose-ink)]" : "text-[color:var(--color-text-muted)]"
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
