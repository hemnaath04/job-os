// Handshake spider. EXPERIMENTAL, AND OFF BY DEFAULT.
//
// Read this before switching it on.
//
//   1. Handshake has no public job feed. Everything behind /postings requires
//      an authenticated session tied to your school account. There is no key to
//      buy and no guest endpoint to call.
//   2. So this spider cannot work on its own. It only runs if you paste your
//      own logged-in session cookie into HANDSHAKE_SESSION_COOKIE and set
//      HANDSHAKE_HOST. Without both, run() returns nothing at all and the
//      spider is completely inert.
//   3. That cookie expires often, typically within days. When it does this
//      spider quietly returns nothing until you paste a fresh one. Treat it as
//      a manual, occasional tool rather than something that keeps working.
//   4. The request goes out through plain fetch rather than Zyte, because it
//      has to carry your session and your session should not be handed to a
//      third-party proxy.
//   5. Handshake's internal endpoints are not a public API and change without
//      notice. The parsing below is defensive guesswork; a shape change means
//      no results, not a crash.
//   6. Using your own credentials to pull data is on you. Your school's and
//      Handshake's terms of service apply, and you are responsible for
//      complying with them.
//
// Anything that goes wrong here returns an empty list. This spider must never
// be the reason the whole response fails.

import { job, text } from "../html.js";

export const name = "handshake";

const TIMEOUT_MS = 15_000;

export async function run(params, deps) {
  const cookie = (process.env.HANDSHAKE_SESSION_COOKIE ?? "").trim();
  const host = (process.env.HANDSHAKE_HOST ?? "").trim();
  // Inert unless the user explicitly supplied their own session.
  if (!cookie || !host) return [];

  const query = params.titleKeywords.join(" ").trim();
  // Two shapes have been seen in the wild. Try them in turn and take the first
  // that parses into something job-shaped.
  const candidates = [
    `https://${host}/postings.json?${new URLSearchParams({
      query,
      per_page: String(params.limit),
    }).toString()}`,
    `https://${host}/api/v2/job_postings?${new URLSearchParams({
      query,
      per_page: String(params.limit),
    }).toString()}`,
  ];

  for (const url of candidates) {
    try {
      const rows = await fetchPostings(url, cookie);
      if (rows.length > 0) return rows.slice(0, params.limit);
    } catch {
      // Try the next shape. A failure here is expected often enough that it is
      // not worth reporting on its own.
    }
  }

  // Reached only when the session is stale or the endpoints moved.
  deps.note(
    "handshake returned nothing: the session cookie is probably expired, or the endpoint moved",
  );
  return [];
}

async function fetchPostings(url, cookie) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const res = await fetch(url, {
      signal: controller.signal,
      headers: {
        accept: "application/json",
        cookie,
        "x-requested-with": "XMLHttpRequest",
      },
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return parse(await res.json(), new URL(url).host);
  } finally {
    clearTimeout(timer);
  }
}

/** Every access is optional: this is an internal structure, not a contract. */
function parse(payload, host) {
  const rows = Array.isArray(payload)
    ? payload
    : Array.isArray(payload?.postings)
      ? payload.postings
      : Array.isArray(payload?.results)
        ? payload.results
        : Array.isArray(payload?.data)
          ? payload.data
          : [];

  const out = [];
  for (const row of rows) {
    if (!row || typeof row !== "object") continue;
    const title = text(row.title ?? row.job_title ?? row.name);
    const id = row.id ?? row.job_id ?? row.posting_id;
    if (!title || id === undefined || id === null) continue;
    out.push(
      job({
        title,
        url: text(row.url) ?? `https://${host}/jobs/${id}`,
        company: text(row.employer?.name ?? row.employer_name ?? row.company?.name),
        location: text(
          row.location?.name ?? row.formatted_location ?? row.location_name ?? row.city,
        ),
        posted_at: text(row.created_at ?? row.posted_at ?? row.published_at),
        description: text(row.description),
        id: String(id),
      }),
    );
  }
  return out;
}
