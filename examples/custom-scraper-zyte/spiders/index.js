// Spider registry.
//
// Add a site by writing a module that exports `name` and
// `async run(params, deps) -> Job[]`, then listing it here. `params` carries
// the job.os filters ({ titleKeywords, location, countryCodes, maxAgeDays,
// limit }) and `deps` carries { zyteFetch, zyteExtract, note }.
//
// A spider throws when it is broken (blocked, shape changed) and returns an
// empty array when the search simply had no hits. The orchestrator treats those
// differently: the first becomes an error note, the second does not.

import * as google from "./google.js";
import * as handshake from "./handshake.js";
import * as indeed from "./indeed.js";
import * as linkedin from "./linkedin.js";

export const SPIDERS = {
  [linkedin.name]: linkedin,
  [indeed.name]: indeed,
  [google.name]: google,
  [handshake.name]: handshake,
};

/**
 * What runs when SPIDERS is not set. Handshake is excluded on purpose: it does
 * nothing without the user's own session cookie.
 */
export const DEFAULT_SPIDERS = ["linkedin", "indeed", "google"];

export function resolve(names) {
  const wanted = names.length > 0 ? names : DEFAULT_SPIDERS;
  const found = [];
  const unknown = [];
  for (const raw of wanted) {
    const key = raw.trim().toLowerCase();
    if (!key) continue;
    if (SPIDERS[key]) found.push(SPIDERS[key]);
    else unknown.push(key);
  }
  return { spiders: found, unknown };
}
