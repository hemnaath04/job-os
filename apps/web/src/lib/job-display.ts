/**
 * How a saved job is named on screen when its import has not finished reading it.
 *
 * `/jobs` and the text importer both insert a row immediately and parse in the
 * background: `routers/jobs.py` writes the title as "Untitled" with the company
 * as "Unknown" or a slug guessed off the URL host, and `services/jd_ingest.py`
 * fills in the real heading when the parse lands. Between those two moments,
 * and forever if the parse fails, the row is a placeholder.
 *
 * /tailor's picker already hides those (see tailor-job-options.ts). Everywhere
 * else showed them as finished saves: the Applications list read "Untitled" at
 * "Oraclecloud" next to real roles, with a status pill and a date, as if the
 * user had deliberately tracked a job with no name. That is a claim the product
 * cannot support, and it is worse than saying nothing, because the row looks
 * complete and the user has no idea anything is still owed to them.
 *
 * This module is the shared reading of that state. It is deliberately a leaf
 * with no imports so Node's own test runner can load it, and deliberately
 * separate from tailor-job-options.ts, which answers a different question
 * ("should this row be offered as a choice?") and returns a whole collapsed
 * list to do it.
 *
 * House style for the copy below: sentence case, no jargon, and it says what
 * is happening rather than what went wrong. "Still reading this posting" is a
 * state the user can wait out. "Parse incomplete" is not a sentence.
 */

/** The little a job needs for its name to be read honestly. */
export type DisplayableJob = {
  title?: string | null;
  company?: { name?: string | null } | null;
  source_url?: string | null;
  jd_parsed?: {
    parse_pending?: boolean;
    parse_incomplete?: boolean;
  } | null;
};

/** Why a row is not showing a real title yet, or null when it is. */
export type JobReadState = "reading" | "unreadable" | null;

export type JobDisplay = {
  /** What to print as the role. Never a raw placeholder. */
  title: string;
  /** What to print as the employer, or null when the row does not name one. */
  company: string | null;
  /**
   * True when `company` came from the link rather than the posting. Still
   * printed, because the guess is right more often than it is wrong and the
   * host label usually IS the employer, but a caller that wants to mark it
   * provisional can.
   */
  companyIsGuess: boolean;
  /**
   * "reading" while the import is still working, "unreadable" once it has
   * stopped and left nothing usable, null for a row that landed properly.
   */
  state: JobReadState;
  /** One line under the title, or null when there is nothing to explain. */
  note: string | null;
  /** True when this row should not be presented as a finished save. */
  incomplete: boolean;
};

/**
 * Titles that mean "this posting was never read", not "this is the role".
 *
 * Kept in step with PLACEHOLDER_TITLES in tailor-job-options.ts by hand rather
 * than shared: that module's copy is about which rows to OFFER, this one is
 * about what to PRINT, and merging them would give one list two jobs. If you
 * add a placeholder to either, add it to both.
 */
const PLACEHOLDER_TITLES = new Set([
  "untitled",
  "untitled job",
  "untitled position",
  "untitled import",
  "unknown",
  "unknown role",
  "n a",
  "na",
  "none",
  "null",
  "job",
  "job posting",
  "position",
  "careers",
]);

/** Company names that carry no more information than an empty column. */
const UNNAMED_COMPANIES = new Set([
  "unknown",
  "unknown company",
  "n a",
  "na",
  "none",
  "null",
]);

/**
 * Words that only appear in a title because a fetch landed somewhere that was
 * not a posting: an error page, a sign-in wall, or a bot check.
 */
const ERROR_TITLE =
  /(^| )(error|errors|404|403|410|500|502|503|not found|page unavailable|unavailable|forbidden|access denied|denied|just a moment|attention required|captcha|robot check|are you a robot|sign in|log in|login|redirecting|oops)( |$)/;

function normalize(value: string | null | undefined): string {
  return (value ?? "")
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, " ")
    .trim();
}

/** True when the title is a placeholder or the title of an error page. */
export function isPlaceholderTitle(title: string | null | undefined): boolean {
  const normalized = normalize(title);
  if (!normalized) return true;
  if (PLACEHOLDER_TITLES.has(normalized)) return true;
  return ERROR_TITLE.test(normalized);
}

/**
 * True when a company name came off the link rather than out of the posting.
 *
 * `company_hint_from_url` in services/jd_ingest.py fills the column from the
 * URL when the page has not been read yet: for a known ATS it takes the board
 * slug (a good name), and otherwise the registrable-looking host label, title
 * cased. That last branch is what produces a hostname wearing a company's
 * clothes -- "somethingcloud.com" becomes "Somethingcloud", and a careers
 * subdomain becomes "Somethingcareers".
 *
 * Deliberately NOT used to hide the name. The guess is right more often than
 * it is wrong (plenty of employers are their own domain), and blanking it
 * would lose real information on every row it gets right. It is here so a
 * caller can tell a name the posting stated from one the importer inferred.
 *
 * A real single-word employer is common, so the test is not "one word" -- it
 * is "one word the source URL's own HOST spells the same way". The path is
 * excluded on purpose: an ATS board URL carries the employer's real name in
 * its path, and matching there would flag every correctly-named Greenhouse
 * and Lever row.
 */
export function isGuessedCompanyName(
  name: string | null | undefined,
  sourceUrl: string | null | undefined,
): boolean {
  const folded = normalize(name).replace(/ /g, "");
  if (!folded) return false;
  const raw = (sourceUrl ?? "").trim();
  if (!raw) return false;
  let host: string;
  try {
    host = new URL(raw.includes("://") ? raw : `https://${raw}`).hostname;
  } catch {
    return false;
  }
  const squashed = host.toLowerCase().replace(/[^a-z0-9]/g, "");
  return squashed.includes(folded);
}

/** True when the row's own parse flags say the reading has not landed. */
function readState(job: DisplayableJob): JobReadState {
  const parsed = job.jd_parsed;
  if (parsed?.parse_pending) return "reading";
  if (parsed?.parse_incomplete) return "unreadable";
  return null;
}

/**
 * What to show for one job, honest about an import that has not finished.
 *
 * A row counts as incomplete when EITHER its parse flags say so or its title is
 * still a placeholder. Both are needed: a parse can finish and still not have
 * found a heading, and a row can carry a real title while the description is
 * still being fetched. `state` distinguishes the two so a caller can offer
 * "reading" (wait, or retry) rather than "unreadable" (this link did not work).
 */
export function jobDisplay(job: DisplayableJob): JobDisplay {
  const rawTitle = (job.title ?? "").trim();
  const rawCompany = (job.company?.name ?? "").trim();
  const titleMissing = isPlaceholderTitle(rawTitle);
  // Only the literal placeholders are dropped. A guessed name is kept and
  // flagged; see `isGuessedCompanyName` for why blanking it would lose more
  // than it saved.
  const companyMissing = !rawCompany || UNNAMED_COMPANIES.has(normalize(rawCompany));
  const company = companyMissing ? null : rawCompany;
  const companyIsGuess =
    !companyMissing && isGuessedCompanyName(rawCompany, job.source_url);

  const flagged = readState(job);
  // A placeholder title with no flag either way is a row whose parse never
  // reported back at all, which reads to the user exactly like one still in
  // flight, so it is offered the same wait-or-retry affordance.
  const state: JobReadState = flagged ?? (titleMissing ? "reading" : null);
  const incomplete = titleMissing || flagged !== null;

  if (!incomplete) {
    return {
      title: rawTitle,
      company,
      companyIsGuess,
      state: null,
      note: null,
      incomplete: false,
    };
  }

  const title = titleMissing ? "Still reading this posting" : rawTitle;
  const note =
    state === "unreadable"
      ? "We could not read this posting. Open the original link, or paste the description in."
      : "We are still reading it. The title and company will fill in on their own.";
  return { title, company, companyIsGuess, state, note, incomplete: true };
}

/**
 * Whether a discovery result is worth saving to the pipeline as-is.
 *
 * The finder hands the import route a title it read off the source. When that
 * title is a placeholder or an error page's heading, saving it produces
 * precisely the "Untitled at Unknown" row the Applications list should never
 * have shown. Better to say so before the row exists than to clean it up after.
 */
export function canSaveToPipeline(result: {
  title?: string | null;
  source_url?: string | null;
}): boolean {
  return !isPlaceholderTitle(result.title) && Boolean((result.source_url ?? "").trim());
}
