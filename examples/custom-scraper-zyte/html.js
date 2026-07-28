// Tiny HTML and JSON helpers shared by the spiders.
//
// Regex parsing rather than a DOM library, on purpose: this project has no
// dependencies, so there is nothing to install, audit or upgrade. The spiders
// only ever pull a handful of well-known fields out of a page, which is the
// one case where regex parsing is defensible.

const NAMED_ENTITIES = {
  amp: "&",
  lt: "<",
  gt: ">",
  quot: '"',
  apos: "'",
  nbsp: " ",
  ndash: "-",
  mdash: "-",
  hellip: "...",
  rsquo: "'",
  lsquo: "'",
  rdquo: '"',
  ldquo: '"',
};

export function decodeEntities(input) {
  return String(input ?? "").replace(
    /&(#x[0-9a-f]+|#\d+|[a-z]+);/gi,
    (match, body) => {
      if (body.toLowerCase().startsWith("#x")) {
        const code = Number.parseInt(body.slice(2), 16);
        return Number.isNaN(code) ? match : String.fromCodePoint(code);
      }
      if (body.startsWith("#")) {
        const code = Number.parseInt(body.slice(1), 10);
        return Number.isNaN(code) ? match : String.fromCodePoint(code);
      }
      return NAMED_ENTITIES[body.toLowerCase()] ?? match;
    },
  );
}

/** Strip tags, decode entities, collapse whitespace. Returns null when empty. */
export function text(html) {
  const out = decodeEntities(String(html ?? "").replace(/<[^>]*>/g, " "))
    .replace(/\s+/g, " ")
    .trim();
  return out || null;
}

/** First capture group of `re` against `html`, run through text(). */
export function match1(html, re) {
  const m = re.exec(html);
  return m ? text(m[1]) : null;
}

/** Drop the query string. Job URLs carry tracking that ruins deduplication. */
export function cleanUrl(url) {
  if (typeof url !== "string" || !url.trim()) return null;
  const trimmed = decodeEntities(url.trim());
  const cut = trimmed.indexOf("?");
  return cut === -1 ? trimmed : trimmed.slice(0, cut);
}

/**
 * Pull one embedded JSON object out of a page, starting at the first `{` after
 * `marker`.
 *
 * Brace counting rather than a lazy `(\{.+?\});` regex: these blobs routinely
 * contain `});` inside a nested string or function, and the lazy version cuts
 * there and yields unparseable JSON. Quoted strings and their escapes are
 * skipped so a `{` inside a string value cannot throw the count off.
 *
 * Returns the parsed object, or null if the marker is missing or the slice does
 * not parse.
 */
export function extractJsonAfter(html, marker) {
  const at = html.indexOf(marker);
  if (at === -1) return null;
  const start = html.indexOf("{", at + marker.length);
  if (start === -1) return null;

  let depth = 0;
  let inString = false;
  let escaped = false;
  for (let i = start; i < html.length; i += 1) {
    const ch = html[i];
    if (inString) {
      if (escaped) escaped = false;
      else if (ch === "\\") escaped = true;
      else if (ch === '"') inString = false;
      continue;
    }
    if (ch === '"') inString = true;
    else if (ch === "{") depth += 1;
    else if (ch === "}") {
      depth -= 1;
      if (depth === 0) {
        try {
          return JSON.parse(html.slice(start, i + 1));
        } catch {
          return null;
        }
      }
    }
  }
  return null;
}

/**
 * Split a document into the chunks that each start with `openTagRe`.
 *
 * Good enough to isolate one job card at a time without building a DOM: every
 * field a spider wants lives between one card marker and the next.
 */
export function chunks(html, openTagRe) {
  const re = new RegExp(openTagRe.source, openTagRe.flags.includes("g") ? openTagRe.flags : `${openTagRe.flags}g`);
  const starts = [];
  let m;
  while ((m = re.exec(html)) !== null) {
    starts.push(m.index);
    if (m.index === re.lastIndex) re.lastIndex += 1;
  }
  return starts.map((start, i) =>
    html.slice(start, i + 1 < starts.length ? starts[i + 1] : html.length),
  );
}

/** A job.os result row with everything optional filled in. */
export function job({ title, url, company = null, location = null, posted_at = null, description = null, id = null }) {
  return {
    title,
    url,
    company,
    company_domain: null,
    location,
    country_code: null,
    posted_at,
    description,
    id,
  };
}
