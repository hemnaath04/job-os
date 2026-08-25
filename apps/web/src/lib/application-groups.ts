import { isThisMonth, isThisWeek, isToday, isYesterday, parseISO } from "date-fns";

/**
 * The item model behind the grouped application list, kept out of the
 * component so it can be tested against real pipelines rather than only
 * looked at.
 *
 * The rule this module exists to hold: a date heading and the order it sits
 * in have to come from the SAME date. When they came from different ones,
 * the list could not keep its own promise of one heading per group.
 */

/** The minimum a row needs to be ordered and bucketed. */
export type GroupableApplication = {
  id: string;
  applied_at: string | null;
  updated_at: string;
};

/** The sorts whose order IS a date, and so the only ones that can carry date headings. */
export type GroupedSort = "updated" | "applied";

export function isGroupedSort(sort: string): sort is GroupedSort {
  return sort === "updated" || sort === "applied";
}

/**
 * The one date a grouped list is both ordered by and bucketed under.
 *
 * Both callers must go through this. They used to disagree: the comparator
 * read `applied_at ?? ""` (so a row that was never applied to sorted last)
 * while the bucketer read `applied_at ?? updated_at` (so that same row was
 * bucketed by when it was last touched, which is usually recent). A wishlist
 * row therefore sorted to the bottom of the list wearing a "Today" or "This
 * month" label, which put a second copy of a heading below rows that had
 * already been filed under it.
 */
export function groupDate(application: GroupableApplication, sort: GroupedSort): string | null {
  return sort === "applied" ? application.applied_at : application.updated_at;
}

/** Most recent first, with rows that have no date for this sort last. */
export function compareByGroupDate(
  a: GroupableApplication,
  b: GroupableApplication,
  sort: GroupedSort,
): number {
  return (groupDate(b, sort) ?? "").localeCompare(groupDate(a, sort) ?? "");
}

/**
 * Every heading the list can show, in the order they appear.
 *
 * This order is the descending-recency order the grouped sorts already use,
 * so in the ordinary case laying rows out bucket by bucket reproduces the
 * sort exactly. It is declared rather than derived because it is also what
 * makes a duplicate heading unrepresentable.
 */
export const BUCKETS = [
  "Today",
  "Yesterday",
  "This week",
  "This month",
  "Earlier",
  "Not applied yet",
] as const;

export type Bucket = (typeof BUCKETS)[number];

/** Buckets recency into the words someone would actually use for it. */
export function bucketOf(iso: string | null): Bucket {
  // Only reachable under the "applied" sort, where a row that was never
  // applied to has no date at all. Saying so is more honest than filing it
  // under whenever the row was last edited.
  if (!iso) return "Not applied yet";
  const date = parseISO(iso);
  if (Number.isNaN(date.getTime())) return "Earlier";
  if (isToday(date)) return "Today";
  if (isYesterday(date)) return "Yesterday";
  if (isThisWeek(date, { weekStartsOn: 1 })) return "This week";
  if (isThisMonth(date)) return "This month";
  return "Earlier";
}

export type ListItem<T> =
  | { kind: "group"; key: string; label: Bucket }
  | { kind: "row"; key: string; application: T };

/**
 * The flat, virtualizer-ready item list: headings and rows in one indexed
 * sequence, because the virtualizer places one sequence and nesting would
 * cost the fixed-size fast path.
 *
 * Rows are collected per bucket and then emitted in `BUCKETS` order, rather
 * than walked once while watching for the label to change. The walk only
 * merged rows that were already ADJACENT, so it emitted a fresh heading every
 * time a label reappeared later in the list -- and because a heading was keyed
 * by its label alone, the repeat also collided with the first one's React key,
 * which is what turned a cosmetic repeat into a heading rendered with nothing
 * under it. Collecting first means a heading exists only if rows were filed
 * under it, and only one can exist per bucket, so neither an empty heading nor
 * a duplicate key is representable.
 *
 * Grouping applies only to date sorts. Under "highest match" these headings
 * would repeat and mean nothing.
 */
export function buildListItems<T extends GroupableApplication>(
  applications: T[],
  sort: string,
): ListItem<T>[] {
  if (!isGroupedSort(sort)) {
    return applications.map((application) => ({
      kind: "row" as const,
      key: application.id,
      application,
    }));
  }

  const byBucket = new Map<Bucket, T[]>();
  for (const application of applications) {
    const bucket = bucketOf(groupDate(application, sort));
    const rows = byBucket.get(bucket);
    if (rows) rows.push(application);
    else byBucket.set(bucket, [application]);
  }

  const items: ListItem<T>[] = [];
  for (const bucket of BUCKETS) {
    const rows = byBucket.get(bucket);
    if (!rows?.length) continue;
    items.push({ kind: "group", key: `group:${bucket}`, label: bucket });
    for (const application of rows) {
      items.push({ kind: "row", key: application.id, application });
    }
  }
  return items;
}
