// A job.os custom source, backed by the Zyte API.
//
// job.os POSTs its search filters here and renders whatever this returns. It
// never talks to the job sites itself: this function does, on your Vercel
// project, with your Zyte key and against the pages you chose in TARGET_URLS.
// You are responsible for those pages. See the README.
//
// Zero dependencies on purpose: Node built-ins and global fetch only, so there
// is no build step and nothing to keep up to date.

/** Zyte browser extraction is not fast, and we may hit several pages. */
export const config = { maxDuration: 60 };

const ZYTE_ENDPOINT = "https://api.zyte.com/v1/extract";
const DEFAULT_LIMIT = 20;
/** One target that hangs must not eat the whole function budget. */
const ZYTE_TIMEOUT_MS = 25_000;

export default async function handler(req, res) {
  if (req.method !== "POST" && req.method !== "GET") {
    res.setHeader("allow", "GET, POST");
    return json(res, 405, { error: "method not allowed" });
  }

  // Optional shared secret. When it is set, job.os has to send it back in the
  // auth header you configured there.
  const secret = process.env.SHARED_SECRET;
  if (secret && req.headers["x-custom-source-key"] !== secret) {
    return json(res, 401, { error: "unauthorized" });
  }

  const apiKey = process.env.ZYTE_API_KEY;
  if (!apiKey) {
    return json(res, 500, {
      error: "ZYTE_API_KEY is not set on this deployment",
    });
  }

  const targets = splitList(process.env.TARGET_URLS);
  if (targets.length === 0) {
    return json(res, 500, {
      error: "TARGET_URLS is not set. Add a comma-separated list of job listing pages.",
    });
  }

  const params = readParams(req);
  const hydrateLimit = toInt(process.env.ZYTE_HYDRATE_LIMIT, 0);

  const seen = new Set();
  const results = [];
  const failures = [];

  for (const target of targets) {
    if (results.length >= params.limit) break;
    try {
      const listed = await listJobs(apiKey, target);
      for (const job of listed) {
        if (results.length >= params.limit) break;
        const key = job.url.toLowerCase();
        if (seen.has(key)) continue;
        seen.add(key);
        results.push(job);
      }
    } catch (e) {
      // One dead target should not sink the rest. Note the target, not the key.
      failures.push(`${target}: ${e.message}`);
    }
  }

  if (results.length === 0 && failures.length === targets.length) {
    return json(res, 502, { error: `every target failed: ${failures.join("; ")}` });
  }

  if (hydrateLimit > 0) {
    await hydrate(apiKey, results.slice(0, hydrateLimit));
  }

  return json(res, 200, { results });
}

// ---------------------------------------------------------------------------
// Zyte
// ---------------------------------------------------------------------------

/**
 * Ask Zyte for the job links on a listing page. `jobPostingNavigation` returns
 * an `items` array of link requests, each with a `url` and the link text in
 * `name`.
 */
async function listJobs(apiKey, target) {
  const data = await zyte(apiKey, { url: target, jobPostingNavigation: true });
  const items = data?.jobPostingNavigation?.items;
  if (!Array.isArray(items)) return [];

  const out = [];
  for (const item of items) {
    const url = text(item?.url);
    if (!url) continue;
    const title =
      text(item?.name) ?? text(item?.text) ?? text(item?.jobPosting?.jobTitle);
    if (!title) continue;
    out.push({
      title,
      url,
      company: null,
      company_domain: null,
      location: null,
      country_code: null,
      posted_at: null,
      description: null,
      id: null,
    });
  }
  return out;
}

/**
 * Fill in the details Zyte only returns from the posting itself. Best effort by
 * design: a posting that fails to hydrate keeps the title and url it already
 * has, and job.os is happy with that.
 */
async function hydrate(apiKey, jobs) {
  for (const job of jobs) {
    try {
      const data = await zyte(apiKey, { url: job.url, jobPosting: true });
      const posting = data?.jobPosting;
      if (!posting) continue;
      job.title = text(posting.jobTitle) ?? job.title;
      job.company = text(posting.hiringOrganization?.name) ?? job.company;
      job.location = text(posting.jobLocation?.raw) ?? job.location;
      job.posted_at = text(posting.datePosted) ?? job.posted_at;
      job.description = text(posting.description) ?? job.description;
    } catch {
      /* leave the row as it came off the listing page */
    }
  }
}

/** Zyte authenticates with HTTP Basic: the API key as the user, no password. */
async function zyte(apiKey, payload) {
  const auth = Buffer.from(`${apiKey}:`).toString("base64");
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), ZYTE_TIMEOUT_MS);
  try {
    const res = await fetch(ZYTE_ENDPOINT, {
      method: "POST",
      signal: controller.signal,
      headers: {
        authorization: `Basic ${auth}`,
        "content-type": "application/json",
        accept: "application/json",
      },
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw new Error(`Zyte HTTP ${res.status}`);
    return await res.json();
  } catch (e) {
    if (e.name === "AbortError") throw new Error("Zyte timed out");
    throw e;
  } finally {
    clearTimeout(timer);
  }
}

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

/**
 * job.os sends its filters in the POST body. A GET is here so you can open the
 * URL in a browser and see that the deployment works.
 */
function readParams(req) {
  const body = req.method === "POST" ? parseBody(req.body) : {};
  const fromQuery = req.query?.limit;
  return {
    limit: toInt(body.limit ?? fromQuery, DEFAULT_LIMIT),
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

function splitList(value) {
  return String(value ?? "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

function text(value) {
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
