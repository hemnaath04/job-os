/**
 * Eligibility flags read off a posting's own words.
 *
 * A fit score answers "could I do this job". It says nothing about whether the
 * employer is allowed to hire you, and that is the cheaper question to answer
 * first: no amount of skill overlap makes a cleared or ITAR-restricted role
 * winnable for someone on a student visa, and tailoring a resume for one is
 * time spent on an application that cannot succeed.
 *
 * Deliberately conservative. Every pattern here is quoted employer language, not
 * an inference, because the cost of a false positive is hiding a job the user
 * could have had. Where a phrase is ambiguous it is left out.
 *
 * These are prompts to go read the posting, not verdicts. The flag says what the
 * posting says; the decision stays with the user.
 */

export type EligibilityFlagKind =
  | "no-sponsorship"
  | "citizenship"
  | "clearance"
  | "export-control";

export interface EligibilityFlag {
  kind: EligibilityFlagKind;
  /** Short enough for a card badge. */
  label: string;
  /** Why this was raised, for the tooltip. */
  detail: string;
}

interface FlagRule {
  kind: EligibilityFlagKind;
  label: string;
  detail: string;
  patterns: RegExp[];
}

const RULES: FlagRule[] = [
  {
    kind: "no-sponsorship",
    label: "No sponsorship",
    detail:
      "The posting says it will not sponsor a visa, or asks for authorization without restriction. That normally rules out an F-1 candidate for a full-time role, though internships on CPT are often still fine.",
    patterns: [
      // "we do not sponsor", "unable to provide sponsorship", "no visa sponsorship"
      /\b(?:do(?:es)?\s+not|will\s+not|cannot|can(?:no|')t|unable\s+to|not\s+able\s+to)\b[^.]{0,60}\bsponsor/i,
      /\bno\b[^.]{0,20}\bvisa\s+sponsorship/i,
      /\bwithout\s+(?:the\s+need\s+for\s+)?(?:visa\s+)?sponsorship/i,
      // "authorized to work in the US without restriction"
      /\bauthoriz(?:ed|ation)\b[^.]{0,60}\bwithout\s+restriction/i,
      /\bnot\s+(?:be\s+)?(?:eligible|considered)\b[^.]{0,40}\bsponsorship/i,
    ],
  },
  {
    kind: "citizenship",
    label: "Citizenship required",
    detail:
      "The posting requires US citizenship or permanent residency, which is a hard bar rather than a preference.",
    patterns: [
      /\bmust\s+be\s+a\b[^.]{0,30}\b(?:u\.?s\.?|united\s+states)\s+citizen/i,
      /\b(?:u\.?s\.?|united\s+states)\s+citizenship\s+(?:is\s+)?(?:required|mandatory)/i,
      /\bu\.?s\.?\s+citizens?\s+only\b/i,
      /\b(?:citizen|permanent\s+resident)\s+status\s+(?:is\s+)?required/i,
      /\bgreen\s+card\s+holder\b[^.]{0,30}\b(?:required|only)/i,
    ],
  },
  {
    kind: "clearance",
    label: "Clearance required",
    detail:
      "The posting asks for a US security clearance. Clearances generally require citizenship, so this is usually a hard bar.",
    patterns: [
      /\b(?:active|current|existing)\b[^.]{0,30}\bsecurity\s+clearance/i,
      /\bsecurity\s+clearance\b[^.]{0,40}\b(?:required|is\s+a\s+must|mandatory)/i,
      /\b(?:ts\/sci|top\s+secret|secret\s+clearance|public\s+trust)\b/i,
      /\bability\s+to\s+obtain\b[^.]{0,30}\bclearance/i,
    ],
  },
  {
    kind: "export-control",
    label: "ITAR / export control",
    detail:
      "The posting cites ITAR or export-control rules, which restrict the role to US persons. Common in defense, aerospace and some hardware work.",
    patterns: [
      /\bITAR\b/,
      /\bexport\s+control(?:led|s)?\b/i,
      /\bEAR\s+(?:regulations|controlled)\b/,
      /\bU\.?S\.?\s+person\b[^.]{0,40}\b(?:as\s+defined|requirement|required)/i,
    ],
  },
];

/**
 * Read eligibility flags from a posting.
 *
 * Title and description together: some boards put "(US Citizens Only)" in the
 * title and nowhere else.
 */
export function detectEligibilityFlags(input: {
  title?: string | null;
  description?: string | null;
}): EligibilityFlag[] {
  const text = `${input.title ?? ""}\n${input.description ?? ""}`;
  if (!text.trim()) return [];

  const found: EligibilityFlag[] = [];
  for (const rule of RULES) {
    if (rule.patterns.some((p) => p.test(text))) {
      found.push({ kind: rule.kind, label: rule.label, detail: rule.detail });
    }
  }
  return found;
}
