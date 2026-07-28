// Indeed spider.
//
// Works, but it is the brittle one. Indeed runs serious anti-bot protection, so
// every request goes through Zyte's browser rendering, and the data is read out
// of a JSON blob the page embeds for its own React app rather than out of the
// markup. That blob is an internal contract: Indeed can rename it any time and
// this spider goes quiet.
//
// Nothing here throws on a shape change. A missing blob returns no rows, the
// orchestrator notes it, and the other spiders still answer.

import { extractJsonAfter, job, text } from "../html.js";

export const name = "indeed";

const BASE = "https://www.indeed.com/jobs";
/** Browser rendering is slow and Indeed pages hard, so keep the walk short. */
const MAX_PAGES = 3;
const PAGE_SIZE = 10;
const BLOB_MARKER = 'window.mosaic.providerData["mosaic-provider-jobcards"]';

export async function run(params, deps) {
  const q = params.titleKeywords.join(" ").trim();
  const l = params.location.trim();

  const results = [];
  const seen = new Set();

  for (let page = 0; page < MAX_PAGES && results.length < params.limit; page += 1) {
    const url = buildUrl({ q, l, maxAgeDays: params.maxAgeDays, start: page * PAGE_SIZE });
    const html = await deps.zyteFetch({ url, browserHtml: true });

    const cards = readCards(html);
    if (cards.length === 0) {
      // Page one coming back empty is the signal that the blob moved. Later
      // pages coming back empty just means we ran out of jobs.
      if (page === 0) {
        throw new Error(
          "no job cards found: Indeed changed its page shape, or the request was blocked",
        );
      }
      break;
    }

    for (const card of cards) {
      if (results.length >= params.limit) break;
      const row = toJob(card);
      if (!row) continue;
      if (seen.has(row.url)) continue;
      seen.add(row.url);
      results.push(row);
    }
  }

  return results;
}

function buildUrl({ q, l, maxAgeDays, start }) {
  const params = new URLSearchParams();
  if (q) params.set("q", q);
  if (l) params.set("l", l);
  if (maxAgeDays && maxAgeDays > 0) params.set("fromage", String(Math.round(maxAgeDays)));
  if (start > 0) params.set("start", String(start));
  return `${BASE}?${params.toString()}`;
}

/** Every access below is guarded: this is someone else's internal structure. */
function readCards(html) {
  const blob = extractJsonAfter(html, BLOB_MARKER);
  const rows = blob?.metaData?.mosaicProviderJobCardsModel?.results;
  return Array.isArray(rows) ? rows : [];
}

function toJob(card) {
  if (!card || typeof card !== "object") return null;
  const jobkey = typeof card.jobkey === "string" ? card.jobkey.trim() : "";
  const title = text(card.title);
  if (!jobkey || !title) return null;

  return job({
    title,
    url: `https://www.indeed.com/viewjob?jk=${encodeURIComponent(jobkey)}`,
    company: text(card.company),
    location: text(card.formattedLocation),
    // Indeed only publishes a relative age ("3 days ago") in
    // formattedRelativeTime, and turning that into a date guesses at a
    // timezone. job.os keeps an undated posting rather than treating it as old,
    // so null is the honest answer.
    posted_at: null,
    description: text(card.snippet),
    id: jobkey,
  });
}
