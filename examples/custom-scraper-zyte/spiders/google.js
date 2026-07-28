// Google for Jobs spider (udm=8, Google's jobs vertical).
//
// VOLATILITY WARNING. This is the least stable spider in the set. Google
// obfuscates its class names, changes them often, renders the jobs panel with
// JavaScript and lazy-loads more cards only as you scroll. Any spider built on
// its DOM is a spider that breaks.
//
// So the primary path here is not a selector at all: it asks Zyte for
// jobPostingNavigation, which is ML extraction and does not care what the class
// names are this week. The selector parse below it is a fallback that runs only
// when extraction comes back empty, and it is the part you should expect to
// have to update.
//
// If you want Google-for-Jobs coverage without maintaining any of this: job.os
// already ships JSearch as a bring-your-own-key source. It is the same data
// behind a stable API, on a free tier, and it needs no scraping. Use that
// unless you specifically want this to run on your own infrastructure.

import { chunks, cleanUrl, job, match1, text } from "../html.js";

export const name = "google";

/** udm=8 selects Google's jobs vertical. */
const BASE = "https://www.google.com/search";

export async function run(params, deps) {
  const query = [params.titleKeywords.join(" "), params.location]
    .map((s) => s.trim())
    .filter(Boolean)
    .join(" ");
  const url = `${BASE}?${new URLSearchParams({ q: query, udm: "8", hl: "en" }).toString()}`;

  const fromExtraction = await viaExtraction(url, params, deps);
  if (fromExtraction.length > 0) return fromExtraction;

  // Extraction found nothing. Either the query genuinely has no jobs, or Google
  // served something the extractor did not recognise. Try the markup once.
  return viaSelectors(url, params, deps);
}

/**
 * The durable path. `jobPostingNavigation` returns an `items` array of links to
 * individual postings, each with a `url` and the link text in `name`.
 */
async function viaExtraction(url, params, deps) {
  const data = await deps.zyteExtract({ url, jobPostingNavigation: true });
  const items = data?.jobPostingNavigation?.items;
  if (!Array.isArray(items)) return [];

  const results = [];
  const seen = new Set();
  for (const item of items) {
    if (results.length >= params.limit) break;
    const href = cleanUrl(item?.url);
    const title = text(item?.name) ?? text(item?.text);
    if (!href || !title) continue;
    if (seen.has(href)) continue;
    seen.add(href);
    // The navigation path only knows the link, so company, location and date
    // come back null and job.os fills in what it can.
    results.push(job({ title, url: href }));
  }
  return results;
}

/**
 * The fragile path. These class names are what Google was serving as of 2025;
 * treat them as a hint rather than a contract. Everything is best effort and
 * an unrecognised page yields an empty list rather than an error.
 */
async function viaSelectors(url, params, deps) {
  const html = await deps.zyteFetch({
    url,
    browserHtml: true,
    // Cards load as you scroll, so scroll before reading the DOM.
    actions: [{ action: "scrollBottom" }],
  });

  const results = [];
  const seen = new Set();
  for (const card of chunks(html, /<[a-z]+\b[^>]*class="[^"]*\bEimVGf\b[^"]*"/i)) {
    if (results.length >= params.limit) break;
    const title = match1(card, /class="[^"]*\btNxQIb\b[^"]*"[^>]*>([\s\S]*?)</i);
    if (!title) continue;
    const anchor = /<a\b[^>]*class="[^"]*\bMQUd2b\b[^"]*"[^>]*>/i.exec(card);
    const href = anchor ? /href="([^"]+)"/i.exec(anchor[0]) : null;
    const link = cleanUrl(href?.[1]);
    // Without a link there is nothing for job.os to open, so drop the card.
    if (!link) continue;
    if (seen.has(link)) continue;
    seen.add(link);

    results.push(
      job({
        title,
        url: link,
        company: match1(card, /class="[^"]*\bwHYlTd\b[^"]*\bMKCbgd\b[^"]*\ba3jPc\b[^"]*"[^>]*>([\s\S]*?)</i),
        location: match1(card, /class="[^"]*\bwHYlTd\b[^"]*\bFqK3wc\b[^"]*\bMKCbgd\b[^"]*"[^>]*>([\s\S]*?)</i),
      }),
    );
  }
  return results;
}
