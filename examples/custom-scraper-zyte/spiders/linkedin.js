// LinkedIn spider.
//
// Uses the public jobs-guest endpoint, which is what the logged-out job search
// page calls as you scroll. No login, no cookie, and it answers with a plain
// HTML fragment of <li> cards rather than a rendered app, so a cheap HTTP fetch
// through Zyte is enough. Zyte is still in the path because a bare request from
// one IP gets rate limited within a few pages.
//
// This is the most reliable spider in the set: the markup below has been stable
// for years. It is still LinkedIn's markup and can change without notice.

import { chunks, cleanUrl, job, match1, text } from "../html.js";

export const name = "linkedin";

const BASE = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search";
/** LinkedIn stops being useful long before this; the cap is a runaway guard. */
const MAX_PAGES = 5;

export async function run(params, deps) {
  const keywords = params.titleKeywords.join(" ").trim();
  const location = params.location.trim();

  const results = [];
  const seen = new Set();
  let start = 0;

  for (let page = 0; page < MAX_PAGES && results.length < params.limit; page += 1) {
    const url = buildUrl({ keywords, location, maxAgeDays: params.maxAgeDays, start });
    // The fragment is static markup, so skip browser rendering: it costs more
    // and buys nothing here.
    const html = await deps.zyteFetch({ url, browserHtml: false });
    const cards = chunks(html, /<li\b/i);
    if (cards.length === 0) break;

    for (const card of cards) {
      if (results.length >= params.limit) break;
      const row = parseCard(card);
      if (!row) continue;
      if (seen.has(row.url)) continue;
      seen.add(row.url);
      results.push(row);
    }

    // Advance by what the page actually returned rather than a hardcoded step:
    // this endpoint has paged at both 10 and 25 cards at different times, and
    // guessing wrong either skips results or loops over the same ones.
    start += cards.length;
  }

  return results;
}

function buildUrl({ keywords, location, maxAgeDays, start }) {
  const params = new URLSearchParams();
  if (keywords) params.set("keywords", keywords);
  if (location) params.set("location", location);
  params.set("start", String(start));
  // f_TPR is a "posted within N seconds" filter.
  if (maxAgeDays && maxAgeDays > 0) {
    params.set("f_TPR", `r${Math.round(maxAgeDays * 86400)}`);
  }
  return `${BASE}?${params.toString()}`;
}

function parseCard(card) {
  // Match the anchor tag first, then read its href: the attribute order on
  // this element is not fixed, so one combined regex would be fragile.
  const anchor = /<a\b[^>]*base-card__full-link[^>]*>/i.exec(card);
  const href = anchor ? /href="([^"]+)"/i.exec(anchor[0]) : null;
  const url = cleanUrl(href?.[1]);
  const title = match1(card, /<h3\b[^>]*base-search-card__title[^>]*>([\s\S]*?)<\/h3>/i);
  if (!url || !title) return null;

  return job({
    title,
    url,
    // The subtitle usually wraps an <a>, which text() flattens for us.
    company: match1(card, /<h4\b[^>]*base-search-card__subtitle[^>]*>([\s\S]*?)<\/h4>/i),
    location: match1(card, /<span\b[^>]*job-search-card__location[^>]*>([\s\S]*?)<\/span>/i),
    posted_at: postedAt(card),
  });
}

/** The <time> element carries a real ISO date; the visible text is relative. */
function postedAt(card) {
  const tag = /<time\b[^>]*>/i.exec(card);
  if (!tag) return null;
  const attr = /datetime="([^"]+)"/i.exec(tag[0]);
  return attr ? text(attr[1]) : null;
}
