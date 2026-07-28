// A job.os custom source: several site spiders behind one endpoint.
//
// job.os POSTs its search filters here and renders whatever comes back. It
// never talks to the job sites itself. This function does, on your Vercel
// project, with your Zyte key, against the sites you enabled in SPIDERS. You
// are responsible for those sites. See the README.
//
// Zero dependencies on purpose: Node built-ins and global fetch only, so there
// is no build step and nothing to keep up to date.

import { resolve } from "../spiders/index.js";
import { zyteExtract, zyteFetch } from "../zyte.js";

/** Browser rendering across several sites is slow. Take the whole budget. */
export const config = { maxDuration: 60 };

const DEFAULT_LIMIT = 25;

export default async function handler(req, res) {
  if (req.method !== "POST" && req.method !== "GET") {
    res.setHeader("allow", "GET, POST");
    return json(res, 405, { error: "method not allowed" });
  }

  // Optional shared secret. When set, job.os has to send it back in the auth
  // header you configured there.
  const secret = process.env.SHARED_SECRET;
  if (secret && req.headers["x-custom-source-key"] !== secret) {
    return json(res, 401, { error: "unauthorized" });
  }

  if (!process.env.ZYTE_API_KEY) {
    return json(res, 500, { error: "ZYTE_API_KEY is not set on this deployment" });
  }

  const params = readParams(req);
  const { spiders, unknown } = resolve(splitList(process.env.SPIDERS));
  const errors = unknown.map((n) => `unknown spider "${n}"`);

  if (spiders.length === 0) {
    return json(res, 500, {
      error: "no spiders enabled: check the SPIDERS environment variable",
      errors,
    });
  }

  // One slow or blocked site must not hold up the others, so they all run at
  // once and every outcome is collected rather than awaited in turn.
  const settled = await Promise.allSettled(
    spiders.map((spider) => {
      const notes = [];
      const deps = { zyteFetch, zyteExtract, note: (m) => notes.push(m) };
      return Promise.resolve(spider.run(params, deps)).then((rows) => ({
        rows: Array.isArray(rows) ? rows : [],
        notes,
      }));
    }),
  );

  const seen = new Set();
  const results = [];
  let failed = 0;

  settled.forEach((outcome, i) => {
    const spiderName = spiders[i].name;
    if (outcome.status === "rejected") {
      failed += 1;
      errors.push(`${spiderName}: ${outcome.reason?.message ?? "failed"}`);
      return;
    }
    for (const note of outcome.value.notes) errors.push(`${spiderName}: ${note}`);
    for (const row of outcome.value.rows) {
      const clean = normalize(row);
      if (!clean) continue;
      const key = clean.url.toLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      results.push(clean);
    }
  });

  // Every spider blowing up is a real failure. Some of them failing while
  // others produced rows is a partial result, which is still a useful one.
  if (results.length === 0 && failed === spiders.length) {
    return json(res, 502, { error: `every spider failed: ${errors.join("; ")}`, errors });
  }

  // The cap is applied here rather than per spider so whichever site answers
  // first cannot swallow the whole page.
  return json(res, 200, { results: results.slice(0, params.limit), errors });
}

/** Guard the contract at the edge: job.os drops a row with no title or url. */
function normalize(row) {
  if (!row || typeof row !== "object") return null;
  const title = typeof row.title === "string" ? row.title.trim() : "";
  const url = typeof row.url === "string" ? row.url.trim() : "";
  if (!title || !url) return null;
  return {
    title,
    url,
    company: row.company ?? null,
    company_domain: row.company_domain ?? null,
    location: row.location ?? null,
    country_code: row.country_code ?? null,
    posted_at: row.posted_at ?? null,
    description: row.description ?? null,
    id: row.id ?? null,
  };
}

/**
 * job.os sends its filters in the POST body. A GET is here so you can open the
 * URL in a browser and see that the deployment works.
 */
function readParams(req) {
  const src = req.method === "POST" ? parseBody(req.body) : (req.query ?? {});
  const countryCodes = toArray(src.country_codes);
  return {
    titleKeywords: toArray(src.title_keywords),
    // The spiders take one location string. A country code is a usable
    // fallback when the search did not name a place.
    location: str(src.location) ?? countryCodes[0] ?? "",
    countryCodes,
    maxAgeDays: toInt(src.max_age_days, 0),
    limit: toInt(src.limit, DEFAULT_LIMIT),
  };
}

function parseBody(body) {
  if (!body) return {};
  if (typeof body === "object") return body;
  try {
    return JSON.parse(body);
  } catch {
    return {};
  }
}

function toArray(value) {
  if (Array.isArray(value)) {
    return value.filter((v) => typeof v === "string" && v.trim()).map((v) => v.trim());
  }
  // A GET carries them as a comma-separated string.
  return typeof value === "string" ? splitList(value) : [];
}

function splitList(value) {
  return String(value ?? "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

function str(value) {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function toInt(value, fallback) {
  const n = Number.parseInt(String(value ?? ""), 10);
  return Number.isFinite(n) && n > 0 ? n : fallback;
}

function json(res, status, payload) {
  res.statusCode = status;
  res.setHeader("content-type", "application/json");
  res.setHeader("cache-control", "no-store");
  res.end(JSON.stringify(payload));
}
