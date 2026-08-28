/**
 * What /tailor says to someone who has never used it, and to anyone reading a
 * score for the first time.
 *
 * The page was written for the person who built it. It opened on a four step
 * aside about how the pass works ("Ground", "Compose", "a traceable draft"),
 * named the posting "the JD", called the run "the agent", and put two numbers
 * next to each other with nothing saying what either measured or which one the
 * user could do anything about. None of that is wrong, and all of it assumes
 * the reader already knows the system.
 *
 * The copy lives here rather than inline in the page for one reason: it is the
 * part with rules. Which of the two scores is worth acting on depends on
 * whether the coverage number hit its ceiling, and the walkthrough only belongs
 * on screen while the vault cannot actually complete a run. Both are decisions,
 * so both are tested.
 *
 * House style, which is also the reason this reads flatter than marketing copy:
 * no em dashes, sentence case, and no word the user has not already seen in
 * their own vault. "Requirement" is fine. "ATS ceiling" is not.
 */

/** What the page knows about the vault when it decides whether to teach. */
export type VaultSnapshot = {
  /** True while the resume or job queries are still in flight. */
  loading: boolean;
  /** A master resume is the baseline every run starts from. */
  hasMasterResume: boolean;
  /** Saved roles that survived the picker's own filtering. */
  savedJobCount: number;
};

export type WalkthroughStep = {
  /** Shown as the step heading. */
  title: string;
  /** One line under it. */
  body: string;
};

/**
 * Three steps, in the order the user does them.
 *
 * Three because the fourth thing the old aside listed ("Verify: independent AI
 * review and one-page PDF checks") is something the product does, not something
 * the user does, and a first time reader cannot act on it. It still happens; it
 * is just not a step they take.
 */
export const WALKTHROUGH_STEPS: readonly WalkthroughStep[] = [
  {
    title: "Pick a job",
    body: "Choose a role you have saved. We read what the posting asks for.",
  },
  {
    title: "We write one page",
    body:
      "Built only from what your profile says you have done. We never add a " +
      "skill, a number or an employer you did not put there.",
  },
  {
    title: "You review the gaps",
    body:
      "We list what the job wants that your profile does not cover yet, so " +
      "you can add it or decide it does not apply to you.",
  },
];

/**
 * Whether to show the walkthrough.
 *
 * Shown when the vault cannot complete a run yet, which is the honest reading
 * of "first visit": no master resume to start from, or no saved job to tailor
 * against. Not driven by a stored "has seen it" flag, because the state it
 * describes is the state the user is actually stuck in, and a returning user
 * who deleted their last job needs the same three lines as a new one.
 *
 * Never shown while loading. A vault mid fetch looks identical to an empty one,
 * and teaching someone with fifty saved roles how to pick their first job is
 * the same wrong claim as telling them they have none.
 */
export function shouldShowWalkthrough(vault: VaultSnapshot): boolean {
  if (vault.loading) return false;
  return !vault.hasMasterResume || vault.savedJobCount === 0;
}

/** Plain heading for the walkthrough, sized to what the vault is missing. */
export function walkthroughTitle(vault: VaultSnapshot): string {
  if (!vault.hasMasterResume) return "Start here";
  return "How this works";
}

/**
 * The one sentence that tells the two numbers apart, plus what to do about it.
 *
 * `atCeiling` is the case that made this necessary. A run scored 27 on keyword
 * match and 98 on the review, and the two together read as a broken page. They
 * were both right: the profile did not contain the remaining requirements, so
 * no amount of rewriting could raise the first number, and the page was as good
 * as that profile gets. Saying so is the difference between a user re-running
 * the tailor five times and a user going to add a fact.
 */
export function scoreExplainer(atCeiling: boolean): string {
  const base =
    "Keyword match counts how many of this job's must-have terms your page " +
    "actually contains, and the writing review scores how well the page reads.";
  if (atCeiling) {
    return (
      base +
      " This page already covers everything your profile can support, so a low " +
      "keyword number here means the job asks for things you have not added " +
      "yet, not that the writing is weak."
    );
  }
  return (
    base +
    " A low keyword number usually means the job asks for things your profile " +
    "does not cover yet, which is worth adding on Profile rather than rewriting."
  );
}

/**
 * What each number is called on screen.
 *
 * The walkthrough only shows while the vault cannot complete a run, which is
 * right: nobody with forty saved roles needs telling how to pick one. But the
 * two scores are read by EVERY user on every run, including the returning ones
 * the walkthrough deliberately skips, and they were labelled "Keyword Match"
 * and "Review 87/100". The first is title case for no reason and the second
 * does not say what it reviewed. So the labels live here with the rest of the
 * copy that has rules, and `scoreExplainer` below is rendered next to them for
 * everyone rather than only on a first visit.
 */
export const KEYWORD_SCORE_LABEL = "Keyword match";
export const REVIEW_SCORE_LABEL = "Writing review";

/**
 * The sentence for a posting the keyword scorer could not check.
 *
 * A posting can name nothing scoreable: the import never read it, or the
 * employer wrote a paragraph of culture copy and no requirements. The review
 * score still appears, so the page shows one number and one blank, and without
 * this the blank reads as the tool having broken rather than as the posting
 * having said nothing to measure.
 */
export const NO_KEYWORDS_EXPLAINER =
  "This posting did not list requirements we could check word by word, so " +
  "there is no keyword number for it. The writing review below still scores " +
  "how well the page reads.";

/** One line each, so neither button needs prior knowledge to press. */
export const TAILOR_AGAIN_HELP =
  "Writes a fresh draft of this page from the same job and profile. Your saved version is left alone.";

export const FINALIZE_HELP =
  "Saves this draft as a finished version and renders the PDF you can send.";

/**
 * The gap panel heading, in the user's words.
 *
 * Was "Gaps the agent surfaced: N requirements the JD asks for that your
 * profile doesn't cover", which names two things ("the agent", "the JD") that
 * exist only inside this codebase.
 */
export function gapHeading(count: number): string {
  return `${count} thing${count === 1 ? "" : "s"} this job asks for that your profile does not cover yet`;
}

/** What the user should actually do with a gap row. */
export const GAP_ACTION_HELP =
  "Add this as a fact if you actually did it. If you did not, leave it: a shorter honest page beats a padded one.";
