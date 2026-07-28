// Zyte API client.
//
// Two entry points, because Zyte does two different jobs for us:
//
//   zyteFetch   gives back page HTML, rendered in a real browser when asked.
//               Use it when a spider parses the markup itself.
//   zyteExtract gives back Zyte's own ML extraction (jobPosting,
//               jobPostingNavigation). Use it when you would rather not own a
//               set of CSS selectors.
//
// The API key is read from the environment on every call and is never logged.

const ZYTE_ENDPOINT = "https://api.zyte.com/v1/extract";
/** Browser rendering plus anti-bot work is slow. Give it room, but bound it. */
const DEFAULT_TIMEOUT_MS = 30_000;

/** Zyte authenticates with HTTP Basic: the API key as the user, no password. */
function authorization() {
  const key = process.env.ZYTE_API_KEY;
  if (!key) throw new Error("ZYTE_API_KEY is not set on this deployment");
  return `Basic ${Buffer.from(`${key}:`).toString("base64")}`;
}

/**
 * POST one request body to Zyte and return the parsed JSON response.
 *
 * The error message deliberately carries the status and nothing else: a Zyte
 * error body can echo request details, and none of that belongs in a log.
 */
async function zyteRequest(body, opts = {}) {
  const controller = new AbortController();
  const timeoutMs = opts.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(ZYTE_ENDPOINT, {
      method: "POST",
      signal: controller.signal,
      headers: {
        authorization: authorization(),
        "content-type": "application/json",
        accept: "application/json",
      },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`Zyte HTTP ${res.status}`);
    return await res.json();
  } catch (e) {
    if (e.name === "AbortError") throw new Error(`Zyte timed out after ${timeoutMs}ms`);
    throw e;
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Fetch a page and return its HTML as a string.
 *
 * `browserHtml` renders the page in a real browser and carries Zyte's anti-bot
 * handling, which is what Indeed and Google need. Pass `browserHtml: false` for
 * a plain HTTP fetch when the target is a static fragment, which is cheaper and
 * faster: LinkedIn's guest endpoint is the example.
 *
 * Zyte rejects a request that asks for both browserHtml and httpResponseBody,
 * so exactly one is set here.
 */
export async function zyteFetch({ url, browserHtml = true, actions, timeoutMs }) {
  const body = { url };
  if (browserHtml) body.browserHtml = true;
  else body.httpResponseBody = true;
  if (Array.isArray(actions) && actions.length > 0) body.actions = actions;

  const data = await zyteRequest(body, { timeoutMs });
  if (typeof data?.browserHtml === "string") return data.browserHtml;
  if (typeof data?.httpResponseBody === "string") {
    // httpResponseBody is base64 because it may carry binary content.
    return Buffer.from(data.httpResponseBody, "base64").toString("utf8");
  }
  return "";
}

/**
 * Run Zyte's automatic extraction and return the raw response object.
 *
 * Pass the extraction flags you want alongside the url, for example
 * `{ url, jobPostingNavigation: true }` for the job links on a listing page or
 * `{ url, jobPosting: true }` for one posting. This path is ML-based, so it
 * survives markup changes that would break a selector.
 */
export async function zyteExtract(body, opts = {}) {
  return zyteRequest(body, opts);
}
