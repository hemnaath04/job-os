/**
 * First-pass ranking: is this the search the user asked for?
 *
 * Distinct from ./fit-score, which answers the second question -- given that
 * this is a job you searched for, how well do you match it. Fit is scored
 * against the posting's named skills, so a Director of Litigation posting that
 * names none of them and a Platform Security Engineer posting that names
 * several are ordered by how much Python each mentions, which is not the
 * question. A search for "software engineer intern" opened with:
 *
 *   Platform Security Engineer (67% fit), Principal Enterprise Technology
 *   Architect, Localization Manager, EA to CRO, Director of Litigation,
 *   Revenue Accountant Lead, Equipment Maintenance, "Various roles"
 *
 * Every one of those is a real row from a real source. Three things put them at
 * the top and none of them is the fit score:
 *
 *   1. The index matches the searched phrase against `search_text`, which
 *      concatenates the title with 8000 characters of JD body, so a posting
 *      that merely *mentions* software engineering interns matches as strongly
 *      as one titled for them. (The server-side half of this is a title weight
 *      in `job_index.py`; this is the client half, and it covers the live
 *      sources too.)
 *   2. Nothing anywhere read the seniority the user asked for. "Intern" in the
 *      query and "Principal" in the title is a contradiction, not a near miss.
 *   3. Placeholder postings ("Various roles", body: keep an eye on our
 *      website) are not jobs at all, and no ranking of jobs should have to
 *      have an opinion about where they go.
 *
 * So: drop (3), and order by an intent tier before fit, rather than replacing
 * fit. Within a tier the fit score still decides, which is what it is good at.
 */
import type { DiscoveryResult } from "../types";

/**
 * Words that carry no intent. Dropped from a query before asking whether the
 * title overlaps it, so "engineer in boston" does not credit every title
 * containing "in".
 */
const STOPWORDS = new Set([
  "a", "an", "and", "at", "for", "from", "in", "of", "on", "or", "the",
  "to", "with", "role", "roles", "job", "jobs", "position", "positions",
]);

const EARLY_CAREER = [
  /\bintern(s|ship|ships)?\b/i,
  /\bco-?op(s)?\b/i,
  /\bnew grad(uate)?s?\b/i,
  /\bentry[ -]level\b/i,
  /\bearly[ -]career\b/i,
  /\bapprentice(ship)?\b/i,
  /\btrainee\b/i,
  /\bgraduate (program|scheme|analyst|engineer|developer)\b/i,
  /\b(university|campus|student)\b/i,
  /\b(summer|spring|fall|winter) (analyst|associate|scholar)\b/i,
];

/**
 * Titles that contradict an early-career search.
 *
 * "Lead" and "Manager" are here deliberately: they were four of the eight rows
 * that opened that intern search. Checked only after the early-career test
 * passes, so an "Intern - Technical Lead Program" is judged on the intern.
 */
const SENIOR = [
  /\bsenior\b/i,
  /\bsr\.?\b/i,
  /\bstaff\b/i,
  /\bprincipal\b/i,
  /\bdistinguished\b/i,
  /\bfellow\b/i,
  /\blead\b/i,
  /\bdirector\b/i,
  /\bhead of\b/i,
  /\bvp\b/i,
  /\bvice president\b/i,
  /\bchief\b/i,
  /\bpresident\b/i,
  /\bmanager\b/i,
  /\bmgr\b/i,
  /\barchitect\b/i,
  /\bexecutive assistant\b/i,
  /\bpartner\b/i,
  // The ATS convention for a levelled individual-contributor ladder, and
  // always a rung above the one an internship sits on.
  /\bi{2,}\b/i,
];

/** A page on a careers site, not a job. */
const PLACEHOLDER_TITLE = [
  // "Various" alone, but not "Various Data Pipelines Engineer", which is a
  // real posting with an unfortunate first word.
  /^\s*various\s*$/i,
  /\bvarious (roles|positions|openings|vacancies|opportunities)\b/i,
  /\bmultiple (roles|positions|openings)\b/i,
  /\bgeneral (application|interest|inquiry|enquiry)\b/i,
  /\bopen application\b/i,
  /\bspeculative\b/i,
  /\bexpression of interest\b/i,
  /\btalent (community|network|pool|pipeline)\b/i,
  /\bfuture (opportunities|openings|roles|vacancies)\b/i,
  /\bjoin (our|the) (talent|community|network)\b/i,
  /\bdo(n'?t| not) see (a|the|your) (role|job|position)\b/i,
  /\bcandidate pool\b/i,
  /\bother (roles|positions|opportunities)\b/i,
  /^\s*(jobs?|roles?|positions?|openings?|careers?|vacancies)\s*$/i,
];

/** The body of one, when the title alone was not the tell. */
const PLACEHOLDER_BODY = [
  /keep an eye on (our|the) (website|careers|jobs)/i,
  /check back (later|regularly|often|soon)/i,
  /we (do not|don'?t) (currently )?have (a|any) (current |specific )?(opening|vacanc|role)/i,
  /no (current|specific|suitable) (openings|vacancies|roles)/i,
];

function matchesAny(text: string, patterns: RegExp[]): boolean {
  return patterns.some((p) => p.test(text));
}

/**
 * A crude stem, and deliberately so.
 *
 * The exact-word matching the live sources filter with (`matchesTitle` in
 * ./no-key-sources) is right for deciding what to *fetch*: it is the rule the
 * smart-search prompt is written against, and loosening it there would widen
 * every search. For *ordering* what came back it is too tight -- "software
 * engineer intern" would score "Software Engineering Intern" no higher than
 * "Director of Litigation", since "engineer" and "engineering" are different
 * strings. Three suffixes cover the job-title vocabulary that matters
 * (engineering/engineer, internship/intern, analysts/analyst) and the
 * four-character floor keeps "ring" out of "ringing".
 */
export function stem(word: string): string {
  const lower = word.toLowerCase();
  for (const suffix of ["ships", "ship", "ings", "ing", "es", "s"]) {
    if (lower.endsWith(suffix) && lower.length - suffix.length >= 4) {
      return lower.slice(0, lower.length - suffix.length);
    }
  }
  return lower;
}

function words(value: string): string[] {
  return value
    .toLowerCase()
    .split(/[^a-z0-9+#]+/)
    .filter(Boolean);
}

function contentWords(value: string): string[] {
  return words(value)
    .filter((w) => !STOPWORDS.has(w))
    .map(stem);
}

export interface SearchIntent {
  /** The user asked for an internship, co-op, new-grad or equivalent. */
  earlyCareer: boolean;
  /** Stemmed, stopword-free words from the searched title keywords. */
  terms: Set<string>;
  /** The raw phrases, for the full-phrase test. */
  phrases: string[];
}

export function searchIntent(titleKeywords: string[]): SearchIntent {
  const phrases = titleKeywords.map((k) => k.trim()).filter(Boolean);
  const joined = phrases.join(" ");
  return {
    earlyCareer: matchesAny(joined, EARLY_CAREER),
    terms: new Set(phrases.flatMap(contentWords)),
    phrases,
  };
}

export function isPlaceholderPosting(result: {
  title: string;
  description?: string;
}): boolean {
  const title = result.title ?? "";
  if (matchesAny(title, PLACEHOLDER_TITLE)) return true;
  // A body test alone is not enough to condemn a normally-titled posting, so
  // it only applies to a title short enough to be a heading rather than a role.
  const body = result.description ?? "";
  return words(title).length <= 3 && matchesAny(body, PLACEHOLDER_BODY);
}

export interface RelevanceVerdict {
  /** Higher sorts first. Negative means the row contradicts the search. */
  tier: number;
  /** Plain-language reasons, shown on the card's own tooltip. */
  reasons: string[];
  placeholder: boolean;
}

/**
 * How well a row answers the query, before any question of fit.
 *
 * Two independent readings, added:
 *
 *   title match  +2 every word of some searched phrase is in the title
 *                +1 some searched word is in the title
 *                 0 the words only appear in the body, if at all
 *   seniority     0 the search said nothing about level, or the title agrees
 *                -1 an early-career search, and the title is silent on level
 *                -2 an early-career search, and the title says Principal
 *
 * A blank query gives every row a tier of 0, so browsing with no keywords is
 * ordered exactly as it was before: by fit, then recency.
 */
export function relevanceOf(
  result: Pick<DiscoveryResult, "title" | "description">,
  intent: SearchIntent,
): RelevanceVerdict {
  const placeholder = isPlaceholderPosting(result);
  if (placeholder) {
    return { tier: -9, reasons: ["not a specific posting"], placeholder: true };
  }

  const title = result.title ?? "";
  const titleStems = new Set(words(title).map(stem));
  const reasons: string[] = [];
  let tier = 0;

  if (intent.phrases.length) {
    const wholePhrase = intent.phrases.some((phrase) => {
      const needed = contentWords(phrase);
      return needed.length > 0 && needed.every((w) => titleStems.has(w));
    });
    if (wholePhrase) {
      tier += 2;
      reasons.push("the title matches what you searched for");
    } else if ([...intent.terms].some((w) => titleStems.has(w))) {
      tier += 1;
      reasons.push("the title matches part of what you searched for");
    } else {
      reasons.push("only the description mentions your search, not the title");
    }
  }

  if (intent.earlyCareer) {
    if (matchesAny(title, EARLY_CAREER)) {
      reasons.push("early-career role, as searched");
    } else if (matchesAny(title, SENIOR)) {
      tier -= 2;
      reasons.push("a senior title on an early-career search");
    } else {
      tier -= 1;
      reasons.push("the title does not say it is an early-career role");
    }
  }

  return { tier, reasons, placeholder: false };
}

/**
 * Drop the placeholders and order what is left by intent, then by the caller's
 * own comparator.
 *
 * The caller keeps its comparator because "best fit", "recency" and "my
 * location" are three genuinely different questions and this is not one of
 * them: it decides which rows are plausible answers at all, and hands the
 * ordering of the plausible ones back.
 */
export function rankByIntent(
  results: DiscoveryResult[],
  intent: SearchIntent,
  within: (a: DiscoveryResult, b: DiscoveryResult) => number,
): DiscoveryResult[] {
  const tiers = new Map<DiscoveryResult, number>();
  const kept: DiscoveryResult[] = [];
  for (const r of results) {
    const verdict = relevanceOf(r, intent);
    if (verdict.placeholder) continue;
    tiers.set(r, verdict.tier);
    kept.push(r);
  }
  return kept.sort(
    (a, b) => (tiers.get(b) ?? 0) - (tiers.get(a) ?? 0) || within(a, b),
  );
}
