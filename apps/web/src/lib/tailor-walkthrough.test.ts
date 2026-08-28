/**
 * The tailor page, read by someone who has not built it.
 *
 * These lock the two things that are decisions rather than wording: when the
 * walkthrough is on screen, and which of the two scores the explainer tells the
 * user to act on. The jargon check is here too, because the failure it catches
 * is a real one that came back twice.
 */
import { test, describe } from "node:test";
import assert from "node:assert/strict";

import {
  FINALIZE_HELP,
  GAP_ACTION_HELP,
  KEYWORD_SCORE_LABEL,
  NO_KEYWORDS_EXPLAINER,
  REVIEW_SCORE_LABEL,
  TAILOR_AGAIN_HELP,
  WALKTHROUGH_STEPS,
  gapHeading,
  scoreExplainer,
  shouldShowWalkthrough,
  walkthroughTitle,
} from "./tailor-walkthrough.ts";

const LOADED = { loading: false, hasMasterResume: true, savedJobCount: 4 };

describe("when the walkthrough is shown", () => {
  test("a vault that can already run a tailor does not get taught", () => {
    assert.equal(shouldShowWalkthrough(LOADED), false);
  });

  test("a vault with no master resume gets the walkthrough", () => {
    assert.equal(
      shouldShowWalkthrough({ ...LOADED, hasMasterResume: false }),
      true,
    );
  });

  test("a vault with no saved jobs gets the walkthrough", () => {
    assert.equal(shouldShowWalkthrough({ ...LOADED, savedJobCount: 0 }), true);
  });

  test("a vault still loading is never called empty", () => {
    // The case that matters: a full vault mid fetch looks exactly like a new
    // one, and flashing "Start here" at someone with fifty saved roles is the
    // same wrong claim as showing them a zero count.
    assert.equal(
      shouldShowWalkthrough({
        loading: true,
        hasMasterResume: false,
        savedJobCount: 0,
      }),
      false,
    );
  });

  test("the heading names whichever thing is missing", () => {
    assert.equal(walkthroughTitle({ ...LOADED, hasMasterResume: false }), "Start here");
    assert.equal(walkthroughTitle({ ...LOADED, savedJobCount: 0 }), "How this works");
  });
});

describe("the walkthrough itself", () => {
  test("is three steps at most", () => {
    assert.ok(WALKTHROUGH_STEPS.length <= 3, "more than three steps to read");
    assert.equal(WALKTHROUGH_STEPS.length, 3);
  });

  test("promises that nothing is invented", () => {
    const all = WALKTHROUGH_STEPS.map((s) => s.body).join(" ");
    assert.match(all, /never add|only from what your profile/i);
  });
});

describe("telling the two scores apart", () => {
  test("names both numbers and what each measures", () => {
    const text = scoreExplainer(false);
    assert.match(text, /keyword match/i);
    assert.match(text, /writing review/i);
  });

  test("a page at its ceiling is not blamed on the writing", () => {
    // The run that prompted this scored 27 on coverage and 98 on the review.
    // The user's next move is to add a fact, not to press Tailor again.
    const text = scoreExplainer(true);
    assert.match(text, /everything your profile can support/i);
    assert.match(text, /not that the writing is weak/i);
  });

  test("an ordinary low score points at the profile, not a rewrite", () => {
    assert.match(scoreExplainer(false), /does not cover yet/i);
  });
});

describe("the two scores are labelled for everyone, not just first-timers", () => {
  // The walkthrough is deliberately gated on a vault that cannot run yet, so a
  // returning user never sees it. They still see both numbers on every run,
  // and those used to be labelled "Keyword Match" and "Review 87/100" -- one
  // in title case for no reason, the other not saying what it reviewed.

  test("a returning user is not shown the walkthrough", () => {
    assert.equal(shouldShowWalkthrough(LOADED), false);
  });

  test("but the score labels exist independently of it", () => {
    // Neither label is a function of the vault, which is what makes them
    // reachable by the user the walkthrough skips.
    assert.equal(KEYWORD_SCORE_LABEL, "Keyword match");
    assert.equal(REVIEW_SCORE_LABEL, "Writing review");
  });

  test("each label says which of the two numbers it names", () => {
    // The failure was two bare scores side by side reading as one grade
    // contradicting another, so the labels have to be distinguishable at a
    // glance rather than both saying "score".
    assert.notEqual(KEYWORD_SCORE_LABEL, REVIEW_SCORE_LABEL);
    assert.match(KEYWORD_SCORE_LABEL, /keyword/i);
    assert.match(REVIEW_SCORE_LABEL, /writing/i);
  });

  test("both labels are sentence case", () => {
    for (const label of [KEYWORD_SCORE_LABEL, REVIEW_SCORE_LABEL]) {
      const rest = label.split(" ").slice(1);
      assert.deepEqual(
        rest.filter((word) => /^[A-Z]/.test(word)),
        [],
        `title case in "${label}"`,
      );
    }
  });

  test("the explainer names the same two things the labels do", () => {
    // If a label is reworded and the sentence is not, the page is back to two
    // numbers nobody can connect to their explanation.
    const text = scoreExplainer(false).toLowerCase();
    assert.ok(text.includes(KEYWORD_SCORE_LABEL.toLowerCase()));
    assert.ok(text.includes(REVIEW_SCORE_LABEL.toLowerCase()));
  });

  test("a posting with nothing to score still gets a sentence", () => {
    // Not silence. One number and one blank, with no explanation, reads as the
    // tool having broken rather than the posting having said nothing.
    assert.match(NO_KEYWORDS_EXPLAINER, /did not list requirements/i);
    assert.match(NO_KEYWORDS_EXPLAINER, /writing review/i);
  });
});

describe("gap wording", () => {
  test("counts in plain words and agrees with itself", () => {
    assert.equal(
      gapHeading(1),
      "1 thing this job asks for that your profile does not cover yet",
    );
    assert.match(gapHeading(3), /^3 things this job asks for/);
  });

  test("tells the user what to do, including when to do nothing", () => {
    assert.match(GAP_ACTION_HELP, /add this as a fact if you actually did it/i);
    assert.match(GAP_ACTION_HELP, /if you did not, leave it/i);
  });
});

describe("no jargon reaches the reader", () => {
  // Every string this module hands to the page. A term that only exists inside
  // the codebase is a bug in copy the same way a wrong number is a bug in code.
  const COPY = [
    ...WALKTHROUGH_STEPS.flatMap((s) => [s.title, s.body]),
    scoreExplainer(true),
    scoreExplainer(false),
    walkthroughTitle({ loading: false, hasMasterResume: false, savedJobCount: 0 }),
    gapHeading(2),
    GAP_ACTION_HELP,
    TAILOR_AGAIN_HELP,
    FINALIZE_HELP,
    KEYWORD_SCORE_LABEL,
    REVIEW_SCORE_LABEL,
    NO_KEYWORDS_EXPLAINER,
  ];

  const BANNED = [
    "ats",
    "jd",
    "jd_parsed",
    "provenance",
    "ceiling",
    "the agent",
    "vault",
    "corpus",
    "embedding",
    "token",
  ];

  for (const term of BANNED) {
    test(`never says "${term}"`, () => {
      const pattern = new RegExp(`\\b${term.replace(/_/g, "_")}\\b`, "i");
      for (const line of COPY) {
        assert.ok(
          !pattern.test(line),
          `internal term "${term}" reached the user in: ${line}`,
        );
      }
    });
  }

  test("no em dashes anywhere in the copy", () => {
    for (const line of COPY) {
      assert.ok(!line.includes("—"), `em dash in: ${line}`);
    }
  });

  test("both buttons explain themselves without prior knowledge", () => {
    assert.match(TAILOR_AGAIN_HELP, /fresh draft/i);
    assert.match(TAILOR_AGAIN_HELP, /saved version is left alone/i);
    assert.match(FINALIZE_HELP, /pdf/i);
  });
});
