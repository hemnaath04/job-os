import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  buildJobPicker,
  isUnreadableTitle,
  jobOptionLabel,
  normalizeJobText,
  type PickableJob,
} from "./tailor-job-options.ts";

let nextId = 0;
function job(partial: Partial<PickableJob> = {}): PickableJob {
  nextId += 1;
  return { id: `job-${nextId}`, title: "Software Engineer", ...partial };
}

function labels(jobs: PickableJob[], options?: Parameters<typeof buildJobPicker>[1]) {
  return buildJobPicker(jobs, options).options.map((option) => option.label);
}

describe("isUnreadableTitle", () => {
  it("rejects the placeholders the create routes write", () => {
    // routers/jobs.py inserts "Untitled" and leaves jd_ingest.py to fill the
    // real heading in. A row still holding it is an import that never landed.
    assert.equal(isUnreadableTitle("Untitled"), true);
    assert.equal(isUnreadableTitle("untitled"), true);
    assert.equal(isUnreadableTitle("Unknown"), true);
    assert.equal(isUnreadableTitle(""), true);
    assert.equal(isUnreadableTitle(null), true);
    assert.equal(isUnreadableTitle("   "), true);
  });

  it("rejects a page title the scrape read instead of a posting", () => {
    // The reported one: Disney's error page, saved as if it were the role.
    assert.equal(isUnreadableTitle("Custom Job Error - Disney Careers"), true);
    assert.equal(isUnreadableTitle("404 Not Found"), true);
    assert.equal(isUnreadableTitle("Just a moment..."), true);
    assert.equal(isUnreadableTitle("Access Denied"), true);
    assert.equal(isUnreadableTitle("Sign in to continue"), true);
  });

  it("keeps real roles, including the ones with awkward words in them", () => {
    assert.equal(isUnreadableTitle("Software Engineer, Winter 2026 Intern"), false);
    assert.equal(isUnreadableTitle("2027 SWE Intern"), false);
    assert.equal(isUnreadableTitle("Builder, Scale AI"), false);
    // "Site Reliability" carries none of the error words, and neither does a
    // seniority prefix that happens to contain a number.
    assert.equal(isUnreadableTitle("Senior Site Reliability Engineer II"), false);
  });
});

describe("normalizeJobText", () => {
  it("folds away the punctuation two imports of one posting differ by", () => {
    assert.equal(
      normalizeJobText("Winter 2026 Intern - SWE"),
      normalizeJobText("Winter 2026 Intern – SWE"),
    );
  });

  it("keeps a company written in a non-Latin script", () => {
    assert.equal(normalizeJobText("楽天"), "楽天");
  });
});

describe("buildJobPicker", () => {
  it("hides the untitled and error-titled rows, and counts them", () => {
    const jobs = [
      job({ title: "Software Engineer", company: { name: "Datadog" } }),
      job({ title: "Untitled", company: { name: "Unknown" } }),
      job({ title: "Untitled", company: { name: "GlossGenius" } }),
      job({
        title: "Custom Job Error - Disney Careers",
        company: { name: "Disneycareers" },
      }),
    ];
    const picked = buildJobPicker(jobs);
    assert.deepEqual(picked.options.map((o) => o.label), [
      "Software Engineer · Datadog",
    ]);
    assert.equal(picked.unreadableCount, 3);
  });

  it("collapses an exact duplicate into one option", () => {
    // Datadog, Workato, Scale AI and NVIDIA each appeared twice in the live
    // list: re-importing a URL makes a second row rather than updating the first.
    const jobs = [
      job({ title: "Winter 2026 Intern", company: { name: "Datadog" } }),
      job({ title: "Winter 2026 Intern", company: { name: "Datadog" } }),
    ];
    const picked = buildJobPicker(jobs);
    assert.deepEqual(picked.options.map((o) => o.label), [
      "Winter 2026 Intern · Datadog",
    ]);
    assert.equal(picked.options[0].duplicates, 2);
    assert.equal(picked.duplicateCount, 1);
  });

  it("folds a row that lost its company into the named one", () => {
    // The NVIDIA pair: same role, one row never got a company name.
    const jobs = [
      job({ title: "2027 SWE Intern", company: { name: "Unknown" } }),
      job({ title: "2027 SWE Intern", company: { name: "NVIDIA" } }),
    ];
    assert.deepEqual(labels(jobs), ["2027 SWE Intern · NVIDIA"]);
  });

  it("leaves an unnamed row alone when two companies share the title", () => {
    // Guessing which of them it belongs to would be worse than showing it.
    const jobs = [
      job({ title: "Software Engineer", company: { name: "Stripe" } }),
      job({ title: "Software Engineer", company: { name: "Figma" } }),
      job({ title: "Software Engineer", company: null }),
    ];
    assert.deepEqual(labels(jobs), [
      "Software Engineer · Stripe",
      "Software Engineer · Figma",
      "Software Engineer",
    ]);
  });

  it("treats the same link as the same posting whatever the rows are titled", () => {
    const jobs = [
      job({ title: "Untitled", company: { name: "Unknown" }, source_url: "https://jobs.lever.co/workato/abc" }),
      job({
        title: "Intern, AI Engineering",
        company: { name: "Workato" },
        source_url: "http://www.jobs.lever.co/workato/abc/",
      }),
    ];
    const picked = buildJobPicker(jobs);
    // The readable row represents the pair, and the unreadable one is a
    // duplicate rather than something held back.
    assert.deepEqual(picked.options.map((o) => o.label), [
      "Intern, AI Engineering · Workato",
    ]);
    assert.equal(picked.unreadableCount, 0);
    assert.equal(picked.duplicateCount, 1);
  });

  it("prefers the duplicate the tailor can actually run against", () => {
    // A run against a row whose parse never landed throws on dispatch, so the
    // readable one has to be the row the picker offers.
    const pending = job({
      title: "Builder",
      company: { name: "Scale AI" },
      jd_parsed: { parse_pending: true },
    });
    const parsed = job({ title: "Builder", company: { name: "Scale AI" }, jd_parsed: {} });
    const picked = buildJobPicker([pending, parsed]);
    assert.equal(picked.options.length, 1);
    assert.equal(picked.options[0].value, parsed.id);
  });

  it("never hides or collapses away the job the page was pointed at", () => {
    // /tailor?job_id=... arrives from "Tailor a resume for this role". A picker
    // that dropped that very row would show the placeholder with a job set.
    const broken = job({ title: "Untitled", company: { name: "Unknown" } });
    const picked = buildJobPicker([broken, job({ company: { name: "Datadog" } })], {
      selectedId: broken.id,
    });
    assert.equal(picked.options.some((o) => o.value === broken.id), true);
    assert.equal(picked.options[0].label, "Untitled · Unknown (could not be read)");
    assert.equal(picked.unreadableCount, 0);
  });

  it("keeps the selected row as its group's representative", () => {
    const chosen = job({ title: "Winter 2026 Intern", company: { name: "Datadog" } });
    const other = job({ title: "Winter 2026 Intern", company: { name: "Datadog" } });
    const picked = buildJobPicker([other, chosen], { selectedId: chosen.id });
    assert.equal(picked.options.length, 1);
    assert.equal(picked.options[0].value, chosen.id);
  });

  it("gives everything back when the hidden rows are revealed", () => {
    // The escape hatch behind the page's "show them" control: a title this
    // module reads wrong is still reachable, and nothing was deleted.
    const jobs = [
      job({ title: "Software Engineer", company: { name: "Datadog" } }),
      job({ title: "Untitled", company: { name: "GlossGenius" } }),
    ];
    assert.deepEqual(labels(jobs, { includeUnreadable: true }), [
      "Software Engineer · Datadog",
      "Untitled · GlossGenius (could not be read)",
    ]);
  });

  it("keeps the list in the order it arrived", () => {
    const jobs = [
      job({ title: "Third", company: { name: "C" } }),
      job({ title: "Untitled", company: { name: "Unknown" } }),
      job({ title: "First", company: { name: "A" } }),
      job({ title: "Second", company: { name: "B" } }),
    ];
    assert.deepEqual(labels(jobs), ["Third · C", "First · A", "Second · B"]);
  });

  it("has nothing to say about an empty list", () => {
    assert.deepEqual(buildJobPicker([]), {
      options: [],
      unreadableCount: 0,
      duplicateCount: 0,
    });
  });
});

describe("jobOptionLabel", () => {
  it("drops the separator when there is no company to name", () => {
    assert.equal(jobOptionLabel({ id: "a", title: "Software Engineer" }), "Software Engineer");
  });

  it("stands in for a missing title rather than rendering a blank row", () => {
    assert.equal(
      jobOptionLabel({ id: "a", title: "", company: { name: "Datadog" } }, true),
      "Untitled import · Datadog (could not be read)",
    );
  });
});
