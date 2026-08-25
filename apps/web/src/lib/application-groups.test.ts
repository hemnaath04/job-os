import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  BUCKETS,
  bucketOf,
  buildListItems,
  compareByGroupDate,
  groupDate,
  isGroupedSort,
  type GroupableApplication,
  type ListItem,
} from "./application-groups.ts";

const DAY = 86_400_000;

/** Dates are relative to the run so the suite cannot rot into a fixed month. */
function daysAgo(days: number): string {
  return new Date(Date.now() - days * DAY).toISOString();
}

function app(
  id: string,
  overrides: Partial<GroupableApplication> = {},
): GroupableApplication {
  return { id, applied_at: null, updated_at: daysAgo(0), ...overrides };
}

function labels(items: ListItem<GroupableApplication>[]): string[] {
  return items.flatMap((item) => (item.kind === "group" ? [item.label] : []));
}

function rowIds(items: ListItem<GroupableApplication>[]): string[] {
  return items.flatMap((item) => (item.kind === "row" ? [item.application.id] : []));
}

/**
 * The three properties the list promises, checked together because a fix for
 * any one of them alone is what produced the original bug.
 */
function assertWellFormed(items: ListItem<GroupableApplication>[]): void {
  const keys = items.map((item) => item.key);
  assert.equal(new Set(keys).size, keys.length, `duplicate React key in ${keys.join(", ")}`);

  const seen = labels(items);
  assert.equal(new Set(seen).size, seen.length, `heading repeated: ${seen.join(", ")}`);

  items.forEach((item, index) => {
    if (item.kind !== "group") return;
    const next = items[index + 1];
    assert.ok(next && next.kind === "row", `heading "${item.label}" has no rows under it`);
  });

  const order = seen.map((label) => BUCKETS.indexOf(label as (typeof BUCKETS)[number]));
  assert.deepEqual(order, [...order].sort((a, b) => a - b), "headings out of canonical order");
}

describe("bucketOf", () => {
  it("names the recent buckets", () => {
    assert.equal(bucketOf(daysAgo(0)), "Today");
    assert.equal(bucketOf(daysAgo(1)), "Yesterday");
    assert.equal(bucketOf(daysAgo(400)), "Earlier");
  });

  it("says so when there is no date at all, rather than guessing one", () => {
    assert.equal(bucketOf(null), "Not applied yet");
  });

  it("does not throw on a value that is not a date", () => {
    assert.equal(bucketOf("not a date"), "Earlier");
  });
});

describe("groupDate and compareByGroupDate agree", () => {
  it("orders by the same field it buckets by, under each grouped sort", () => {
    const row = app("a", { applied_at: daysAgo(30), updated_at: daysAgo(0) });
    assert.equal(groupDate(row, "applied"), row.applied_at);
    assert.equal(groupDate(row, "updated"), row.updated_at);
  });

  it("sorts most recent first and undated last", () => {
    const older = app("older", { applied_at: daysAgo(10) });
    const newer = app("newer", { applied_at: daysAgo(2) });
    const never = app("never", { applied_at: null });
    const sorted = [older, never, newer].sort((a, b) => compareByGroupDate(a, b, "applied"));
    assert.deepEqual(
      sorted.map((row) => row.id),
      ["newer", "older", "never"],
    );
  });
});

describe("buildListItems", () => {
  it("adds no headings to a sort that is not a date", () => {
    const items = buildListItems([app("a"), app("b")], "match");
    assert.deepEqual(labels(items), []);
    assert.deepEqual(rowIds(items), ["a", "b"]);
  });

  it("gives a bucket exactly one heading even when its rows arrive apart", () => {
    // The shape a real pipeline has, and the one that used to break: an
    // applied row from long ago, plus two rows that were never applied to,
    // one touched today and one touched long ago. The applied sort puts the
    // two undated rows last in the order they came in, so under the old
    // bucketer (which fell back to updated_at) the recent one landed under
    // "Today" BETWEEN two rows that both belonged under "Earlier" -- and the
    // second "Earlier" heading reused the first one's React key.
    const pipeline = [
      app("applied-long-ago", { applied_at: daysAgo(400) }),
      app("wishlist-touched-today", { applied_at: null, updated_at: daysAgo(0) }),
      app("wishlist-touched-long-ago", { applied_at: null, updated_at: daysAgo(400) }),
    ].sort((a, b) => compareByGroupDate(a, b, "applied"));

    const items = buildListItems(pipeline, "applied");

    assertWellFormed(items);
    assert.deepEqual(labels(items), ["Earlier", "Not applied yet"]);
    assert.deepEqual(rowIds(items), [
      "applied-long-ago",
      "wishlist-touched-today",
      "wishlist-touched-long-ago",
    ]);
  });

  it("keeps a row that was never applied to out of the recency buckets", () => {
    const items = buildListItems(
      [app("wishlist", { applied_at: null, updated_at: daysAgo(0) })],
      "applied",
    );
    assert.deepEqual(labels(items), ["Not applied yet"]);
  });

  it("files a row by its update time under the recently-added sort", () => {
    const items = buildListItems(
      [
        app("touched-today", { applied_at: daysAgo(400), updated_at: daysAgo(0) }),
        app("touched-long-ago", { applied_at: null, updated_at: daysAgo(400) }),
      ],
      "updated",
    );
    assertWellFormed(items);
    assert.deepEqual(labels(items), ["Today", "Earlier"]);
  });

  it("does not repeat a heading for a date set in the future", () => {
    // An applied date can be typed in by hand, so it can sit ahead of now and
    // sort above today's rows while still bucketing as this week or later.
    const items = buildListItems(
      [
        app("future", { applied_at: daysAgo(-3) }),
        app("today", { applied_at: daysAgo(0) }),
        app("also-future", { applied_at: daysAgo(-2) }),
      ],
      "applied",
    );
    assertWellFormed(items);
  });

  it("stays well formed over a whole pipeline sorted the way the page sorts it", () => {
    const pipeline = [
      app("a", { applied_at: daysAgo(0) }),
      app("b", { applied_at: null, updated_at: daysAgo(0) }),
      app("c", { applied_at: daysAgo(1) }),
      app("d", { applied_at: null, updated_at: daysAgo(1) }),
      app("e", { applied_at: daysAgo(6) }),
      app("f", { applied_at: daysAgo(20) }),
      app("g", { applied_at: daysAgo(400) }),
      app("h", { applied_at: null, updated_at: daysAgo(400) }),
    ];

    for (const sort of ["updated", "applied"] as const) {
      assert.ok(isGroupedSort(sort));
      const sorted = [...pipeline].sort((a, b) => compareByGroupDate(a, b, sort));
      const items = buildListItems(sorted, sort);
      assertWellFormed(items);
      assert.equal(rowIds(items).length, pipeline.length, "every row survives grouping");
    }
  });
});
