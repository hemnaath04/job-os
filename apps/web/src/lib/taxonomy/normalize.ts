/**
 * String hygiene for job titles, shared by the build script and the runtime
 * lookup so that an alias baked in at build time is keyed exactly the way a
 * live title will be keyed.
 *
 * Nothing here touches the taxonomy. It only cleans, strips decoration, and
 * reads seniority off to the side.
 */

import type { Seniority, TitleDecoration } from "./types.ts";

/**
 * Characters worth keeping inside a token: `+` and `#` for C++ and C#, `.` for
 * .NET and Node.js. Everything else non-alphanumeric becomes a space.
 */
const KEEP_IN_TOKEN = "+#.";

const COMBINING_MARKS = /[̀-ͯ]/g;
const DASHES = /[‐‑‒–—―−]/g;
const SINGLE_QUOTES = /[‘’ʼ]/g;
const DOUBLE_QUOTES = /[“”]/g;
const NBSP = /[   ]/g;

/** Lowercase, de-accent, and reduce punctuation to single spaces. */
export function cleanTitle(raw: string): string {
  let s = raw.normalize("NFKD").replace(COMBINING_MARKS, "");
  s = s.toLowerCase();
  s = s.replace(DASHES, "-").replace(NBSP, " ");
  s = s.replace(SINGLE_QUOTES, "'").replace(DOUBLE_QUOTES, '"');
  // Drop possessive and contraction apostrophes rather than splitting the word.
  s = s.replace(/'/g, "");
  let out = "";
  for (const ch of s) {
    if (/[a-z0-9]/.test(ch) || KEEP_IN_TOKEN.includes(ch)) out += ch;
    else out += " ";
  }
  // A trailing `.` is abbreviation punctuation ("sr.", "jr."), not part of the
  // token, but the `.` in ".net" and "node.js" is.
  out = out.replace(/\.+(?=\s|$)/g, " ").replace(/(?<=\s|^)\.+/g, " ");
  // Same for `+` and `#` that are not attached to a letter, which is how
  // "C++/C#" survives but "Engineer +" does not keep a stray token.
  out = out.replace(/(?<![a-z0-9])[+#]+/g, " ");
  return out.replace(/\s+/g, " ").trim();
}

/**
 * Requisition noise that carries no role information: req ids, years, employment
 * type, location and remote markers, and the "(m/f/d)" style gender notices.
 */
const NOISE_PATTERNS: RegExp[] = [
  /\breq(uisition)? ?(id|no|number)? ?[a-z]?\d{3,}\b/g,
  /\bjob ?(id|no|number|code) ?[a-z]?\d{2,}\b/g,
  /\b20\d\d\b/g,
  /\bfy ?\d\d\b/g,
  /\b(full|part) time\b/g,
  /\b(remote|hybrid|onsite|on site|wfh)\b/g,
  /\b(us|usa|uk|emea|apac|latam|noram)\b(?= ?$)/g,
  /\b[fmdwx] [fmdwx](?: [fmdwx])?\b(?= ?$)/g,
  /\b(h1b|opt|cpt|visa sponsorship|no c2c|w2|1099)\b/g,
  /\b(urgent|now hiring|immediate joiner|walk in)\b/g,
];

export function stripNoise(cleaned: string): string {
  let s = cleaned;
  for (const p of NOISE_PATTERNS) s = s.replace(p, " ");
  return s.replace(/\s+/g, " ").trim();
}

interface SeniorityMarker {
  re: RegExp;
  band: Seniority;
  /** When this matches, the marker does not apply. */
  not?: RegExp;
}

/**
 * Seniority markers, most specific band first, so that "senior staff" reads as
 * principal and "Director of Engineering" never falls through to `lead`.
 *
 * A bare roman numeral or digit is only a seniority signal when it also parsed
 * as a trailing level; "Tier 2 Support" must not come out as mid-level.
 */
const SENIORITY_MARKERS: readonly SeniorityMarker[] = [
  {
    re: /\b(intern|internship|co ?op|coop|trainee|apprentice|summer analyst)\b/,
    band: "intern",
  },
  {
    re: /\b(new grad(uate)?|university grad(uate)?|campus hire|recent grad(uate)?|early career|entry level|fresher|graduate (engineer|developer|programme|program|scheme|trainee|analyst|scientist))\b/,
    band: "new_grad",
  },
  { re: /\b(chief|cto|ciso|cio|cdo|c level)\b/, band: "executive" },
  { re: /\b(vice president|vp|svp|evp)\b/, band: "vp" },
  { re: /\b(director|head of)\b/, band: "director" },
  { re: /\b(distinguished|fellow)\b/, band: "distinguished" },
  { re: /\bsenior staff\b/, band: "principal" },
  { re: /\bprincipal\b/, band: "principal" },
  { re: /\bstaff\b/, band: "staff" },
  {
    re: /\b(senior|sr|snr|experienced|iii|iv|3|4|l[5-8]|e[5-8]|t[5-8]|ic[5-8]|level [345678])\b/,
    band: "senior",
  },
  { re: /\b(team lead|tech lead|technical lead|lead)\b/, band: "lead" },
  {
    // "Product Manager" is a role, not a seniority band. Only a manager-of-people
    // phrasing counts here.
    re: /\b(manager|mgr|supervisor)\b/,
    band: "manager",
    not: /\b(product|program|programme|project|account|community|marketing|brand|category|channel|portfolio|release|content|case|office|hiring|configuration|knowledge|records|utilization|provider|network|data center) (manager|mgr)\b/,
  },
  {
    re: /\b(junior|jr|jnr|associate|graduate|i|1|l[12]|e[12]|level [12])\b/,
    band: "junior",
  },
  { re: /\b(ii|2|mid level|mid|intermediate|l[34]|e[34])\b/, band: "mid" },
];

const BARE_LEVEL_TOKEN = /^(i{1,3}|iv|vi{0,3}|[1-9])$/;

/** Explicit level markers, reported verbatim rather than folded into a band. */
const LEVEL_STRIP_PATTERN =
  /\b(i{1,3}|iv|vi{0,3}|[1-9]|l[1-9]|e[1-9]|t[1-9]|ic[1-9]|p[1-9]|level ?[1-9]|grade ?[1-9])\b/g;

/**
 * Read seniority off a cleaned title without deciding anything about the role.
 */
export function extractDecoration(cleaned: string): TitleDecoration {
  let level: string | null = null;

  // A level only counts at the tail of the title. Job boards put it there, and
  // anywhere else the token is almost always part of a word or a product name.
  const tailLevel = cleaned.match(
    /\b(i{1,3}|iv|vi{1,3}|l[1-9]|e[1-9]|t[1-9]|ic[1-9]|p[1-9]|level ?[1-9]|grade ?[1-9])\s*$/,
  );
  if (tailLevel) level = tailLevel[1].toUpperCase().replace(/\s+/g, "");
  if (level === null) {
    const inline = cleaned.match(/\b(?:sde|swe|sdet|mts)\s*(i{1,3}|iv|[1-4])\b/);
    if (inline) level = inline[1].toUpperCase();
  }

  let seniority: Seniority | null = null;
  let seniorityMarker: string | null = null;
  for (const marker of SENIORITY_MARKERS) {
    if (marker.not && marker.not.test(cleaned)) continue;
    const m = cleaned.match(marker.re);
    if (!m) continue;
    const token = (m[1] ?? m[0]).trim();
    if (BARE_LEVEL_TOKEN.test(token)) {
      if (level === null || token.toUpperCase() !== level) continue;
    }
    seniority = marker.band;
    seniorityMarker = token;
    break;
  }

  const isEarlyCareer =
    seniority === "intern" ||
    seniority === "new_grad" ||
    /\b(intern|internship|co ?op|coop|apprentice|new grad|fresher)\b/.test(cleaned);

  return { seniority, seniorityMarker, level, isEarlyCareer };
}

const FILLER_EDGE = /^\s*\b(of|the|and|for|in|at|to)\b|\b(of|the|and|for|in|at|to)\b\s*$/g;

/** Remove seniority and level words so an exact alias lookup can hit. */
export function stripSeniority(cleaned: string): string {
  let s = cleaned;
  for (const marker of SENIORITY_MARKERS) {
    if (marker.not && marker.not.test(cleaned)) continue;
    s = s.replace(new RegExp(marker.re.source, "g"), " ");
  }
  s = s.replace(LEVEL_STRIP_PATTERN, " ").replace(/\s+/g, " ").trim();
  let previous = "";
  while (previous !== s) {
    previous = s;
    s = s.replace(FILLER_EDGE, " ").replace(/\s+/g, " ").trim();
  }
  return s;
}

const PARENTHETICAL = /[([{][^)\]}]*[)\]}]/g;
const HARD_SEPARATOR = /\s*[,|•]\s*|\s+[-–—/@]\s+/;

export interface TitleForms {
  /**
   * Views that lose nothing about the role: the whole string, the string
   * without parentheticals, and both with seniority words removed. Seniority
   * comes back as its own field, so dropping it costs nothing.
   */
  primary: string[];
  /**
   * Views that cut the title at a separator. These do lose information, because
   * the discipline often lives in the tail ("Software Engineer - Backend"), so
   * they are worth less than a pattern match on the whole string.
   */
  reduced: string[];
}

/**
 * Progressively less decorated views of one raw title, most complete first.
 *
 * The split between `primary` and `reduced` is what stops a generic head from
 * eating a specific tail. "Software Engineer - Backend" has "software engineer"
 * as its head, which is a real alias for the generic leaf; if that were tried
 * before the pattern rules saw the whole string, every such posting would
 * normalize to plain Software Engineer.
 */
export function candidateForms(raw: string): TitleForms {
  const withoutParens = raw.replace(PARENTHETICAL, " ");
  const cleaned = stripNoise(cleanTitle(raw));
  const noParens = stripNoise(cleanTitle(withoutParens));
  const headOnly = stripNoise(cleanTitle(raw.split(HARD_SEPARATOR)[0]));
  const headNoParens = stripNoise(cleanTitle(withoutParens.split(HARD_SEPARATOR)[0]));

  const seen = new Set<string>();
  const take = (forms: string[]): string[] => {
    const out: string[] = [];
    for (const f of forms) {
      if (f.length === 0 || seen.has(f)) continue;
      seen.add(f);
      out.push(f);
    }
    return out;
  };

  return {
    primary: take([cleaned, noParens, stripSeniority(cleaned), stripSeniority(noParens)]),
    reduced: take([headOnly, headNoParens, stripSeniority(headNoParens)]),
  };
}

/**
 * Occupations that share vocabulary with tech titles and would otherwise get a
 * confident wrong answer. A hit here blocks the pattern matcher outright, and
 * blocks an O*NET alternate title from entering the alias map at all.
 *
 * The words are chosen to be unambiguous in isolation. "server" is absent on
 * purpose (Server Engineer), as are "principal" (a seniority band), "architect"
 * (Solutions Architect) and "designer" (Product Designer).
 */
const OCCUPATION_DENY = [
  "nurse",
  "nursing",
  "informaticist",
  "physician",
  "surgeon",
  "dentist",
  "pharmacist",
  "therapist",
  "phlebotom",
  "radiolog",
  "sonograph",
  "paramedic",
  "midwife",
  "veterinar",
  "caregiver",
  "attendant",
  "janitor",
  "custodian",
  "housekeep",
  "groundskeep",
  "landscap",
  "golf",
  "barista",
  "bartender",
  "waiter",
  "waitress",
  "cashier",
  "chef",
  "cook",
  "butcher",
  "baker",
  "welder",
  "plumber",
  "electrician",
  "carpenter",
  "roofer",
  "machinist",
  "millwright",
  "boilermaker",
  "mechanic",
  "truck driver",
  "delivery driver",
  "forklift",
  // Not bare "warehouse": that would block Data Warehouse Engineer, which is a
  // real leaf. The deny list matches on prefix, so it has to stay specific.
  "warehouse associate",
  "warehouse worker",
  "warehouse operative",
  "flight attendant",
  "firefighter",
  "police",
  "correctional",
  "security guard",
  "paralegal",
  "attorney",
  "lawyer",
  "notary",
  "bookkeeper",
  "accountant",
  "teller",
  "loan officer",
  "underwriter",
  "claims adjuster",
  "leasing agent",
  "realtor",
  "teacher",
  "professor",
  "lecturer",
  "preschool",
  "kindergarten",
  "librarian",
  "social worker",
  "recruiter",
  "talent acquisition",
  "payroll",
  "receptionist",
  "medical assistant",
  "dental",
  "clinical trial",
  "utilization review",
  "coding auditor",
] as const;

const OCCUPATION_DENY_RE = new RegExp(
  `\\b(${OCCUPATION_DENY.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`,
);

/** True when a cleaned title names an occupation outside this taxonomy. */
export function isDeniedOccupation(cleaned: string): boolean {
  return OCCUPATION_DENY_RE.test(cleaned);
}
