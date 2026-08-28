/**
 * When are two rows the same job?
 *
 * A search fans out to the index, the SimplifyJobs tables, ~150 ATS boards and
 * three open feeds, and several of those carry the *same posting*: SimplifyJobs
 * is a curated list of links into Greenhouse and Lever, which is precisely
 * where the ATS half is already looking. Verkada's Software Engineering Intern
 * requisition 5211595007 came back once from `greenhouse` and once from
 * `SIMPLIFYJOBS`, and both spent a slot against the 60-result cap.
 *
 * The old rule was "same lowercased URL, or same company + title + location",
 * and it missed that pair twice over:
 *
 *   - the URLs differ by host and tracking parameters alone
 *     (`boards.greenhouse.io/verkada/jobs/5211595007` vs
 *     `job-boards.greenhouse.io/verkada/jobs/5211595007?gh_src=...`)
 *   - the location strings differ by how each source writes them
 *     ("San Mateo, CA" vs "San Mateo")
 *
 * So identity is derived from the thing both rows genuinely share: the ATS and
 * the requisition id inside the URL. That is the employer's own primary key for
 * the posting, and it survives every host alias, redirect wrapper and `utm_`
 * parameter a link aggregator adds on the way.
 *
 * Falling back, in order: a normalized URL when the ATS is not one we can read,
 * then company + title, which is deliberately looser than the old
 * company + title + location -- one requisition listed under two spellings of
 * the same city is one job, and showing it twice is the failure mode this
 * exists to prevent.
 */
import type { DiscoveryResult } from "../types";

/**
 * `<host pattern> -> <ats name>`, checked against the URL's hostname.
 *
 * Host aliases are the point: Greenhouse alone serves the same board from
 * `boards.greenhouse.io`, `job-boards.greenhouse.io` and
 * `boards.eu.greenhouse.io`, and an aggregator will have picked whichever one
 * the employer linked that day.
 */
const ATS_HOSTS: { pattern: RegExp; ats: string }[] = [
  { pattern: /(^|\.)greenhouse\.io$/i, ats: "greenhouse" },
  { pattern: /(^|\.)lever\.co$/i, ats: "lever" },
  { pattern: /(^|\.)ashbyhq\.com$/i, ats: "ashby" },
  { pattern: /(^|\.)smartrecruiters\.com$/i, ats: "smartrecruiters" },
  { pattern: /(^|\.)workable\.com$/i, ats: "workable" },
  { pattern: /(^|\.)myworkdayjobs\.com$/i, ats: "workday" },
];

/**
 * The path segment that is the posting's own id, per ATS.
 *
 * Greenhouse and Workday name the board and then the requisition; Lever and
 * Ashby put a UUID last. Anchored on the shape each vendor actually uses rather
 * than "the last segment", because Greenhouse appends `/application` to an
 * apply link and Lever appends `/apply`, and treating those as the id would
 * split one posting back into two.
 */
const ATS_ID_PATTERNS: Record<string, RegExp[]> = {
  greenhouse: [/\/(?:embed\/job_app\?for=)?([a-z0-9_-]+)\/jobs\/(\d+)/i],
  lever: [/\/([a-z0-9_.-]+)\/([0-9a-f-]{20,})/i],
  ashby: [/\/([a-z0-9_.-]+)\/([0-9a-f-]{20,})/i],
  smartrecruiters: [/\/([a-z0-9_.-]+)\/(\d{6,})/i],
  workable: [/\/([a-z0-9_.-]+)\/j\/([a-z0-9]+)/i],
  workday: [/\/job\/[^/]+\/([^/]+_)?([A-Za-z]+-?\d{4,})/],
};

/** Query parameters every aggregator bolts on, which say nothing about the job. */
const TRACKING_PARAMS =
  /^(utm_|gh_|ref$|source$|src$|lever-source|ashby_jid$|trackingid$|_ga$|fbclid$|gclid$)/i;

export interface JobIdentity {
  /** "<ats>:<board>:<id>" when the URL yielded one. Null otherwise. */
  ats: string | null;
  /** The URL with tracking, host aliases and trailing slashes removed. */
  url: string | null;
  /** company + title, the last-resort match for two rows with unrelated URLs. */
  loose: string;
}

function hostname(raw: string): string | null {
  try {
    return new URL(raw).hostname.toLowerCase().replace(/^www\./, "");
  } catch {
    return null;
  }
}

/**
 * "<ats>:<board>:<requisition>" for a posting URL we can read, else null.
 *
 * Exported for its own sake: this is the strongest identity a discovery row
 * can have, and both the merge step and the per-source dedupe want it.
 */
export function atsPostingId(rawUrl: string | null | undefined): string | null {
  if (!rawUrl) return null;
  const host = hostname(rawUrl);
  if (!host) return null;
  const entry = ATS_HOSTS.find((h) => h.pattern.test(host));
  if (!entry) return null;

  let path: string;
  try {
    const url = new URL(rawUrl);
    // Greenhouse's embedded apply form carries the board in `?for=` rather
    // than in the path, so the query has to be part of what is matched.
    path = url.pathname + url.search;
  } catch {
    return null;
  }
  for (const pattern of ATS_ID_PATTERNS[entry.ats] ?? []) {
    const match = path.match(pattern);
    if (match) {
      return `${entry.ats}:${match[1].toLowerCase()}:${match[2].toLowerCase()}`;
    }
  }
  return null;
}

/** Same URL, minus everything that varies without the destination varying. */
export function normalizeUrl(rawUrl: string | null | undefined): string | null {
  if (!rawUrl) return null;
  let url: URL;
  try {
    url = new URL(rawUrl);
  } catch {
    return rawUrl.trim().toLowerCase() || null;
  }
  for (const key of [...url.searchParams.keys()]) {
    if (TRACKING_PARAMS.test(key)) url.searchParams.delete(key);
  }
  url.hash = "";
  url.protocol = "https:";
  url.hostname = url.hostname.toLowerCase().replace(/^www\./, "");
  url.pathname = url.pathname.replace(/\/+$/, "");
  const params = [...url.searchParams.entries()].sort(([a], [b]) => a.localeCompare(b));
  url.search = params.length
    ? `?${params.map(([k, v]) => `${k}=${v}`).join("&")}`
    : "";
  return url.toString().toLowerCase();
}

export function jobIdentity(result: DiscoveryResult): JobIdentity {
  return {
    ats: atsPostingId(result.source_url),
    url: normalizeUrl(result.source_url),
    loose: [
      (result.company_domain ?? result.company_name ?? "").toLowerCase().trim(),
      result.title.toLowerCase().replace(/\s+/g, " ").trim(),
    ].join("|"),
  };
}

/**
 * Drop every row whose job has already been seen, keeping the first.
 *
 * Callers sort before calling, so "the first" is whichever ordering they chose
 * (newest, or the interleaved order of a merge). The winner claims all three
 * identities, so a later row matching on any one of them is dropped -- an ATS
 * id and a URL alone would still let the third spelling of the same job in.
 *
 * `onDuplicate` exists so the caller can count what it dropped: the result
 * header says how many rows were merged away, and it cannot say that if the
 * merging is invisible.
 */
export function dedupeByJob(
  results: DiscoveryResult[],
  onDuplicate?: (dropped: DiscoveryResult, keptFor: string) => void,
): DiscoveryResult[] {
  const seen = new Set<string>();
  const out: DiscoveryResult[] = [];
  for (const r of results) {
    const identity = jobIdentity(r);
    const keys = [
      identity.ats && `ats:${identity.ats}`,
      identity.url && `url:${identity.url}`,
      // A bare company with no title, or a title with no company, is not an
      // identity -- it would collapse every untitled row from one feed into one.
      identity.loose !== "|" && identity.loose.split("|").every(Boolean)
        ? `loose:${identity.loose}`
        : null,
    ].filter((k): k is string => Boolean(k));

    const hit = keys.find((k) => seen.has(k));
    if (hit) {
      onDuplicate?.(r, hit);
      continue;
    }
    for (const key of keys) seen.add(key);
    out.push(r);
  }
  return out;
}
