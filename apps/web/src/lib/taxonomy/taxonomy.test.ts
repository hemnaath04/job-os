/**
 * Taxonomy tests.
 *
 *   cd apps/web && pnpm test
 *
 * Two halves. The first checks structural invariants that the build also checks,
 * so a hand-edited generated file cannot pass. The second is the part that
 * matters: real messy titles, including a block that must come back null,
 * because in this system a confident wrong answer is worse than no answer.
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  describeSeniority,
  getAncestry,
  getFamily,
  getGroup,
  getLeaf,
  leavesForSoc,
  leavesInFamily,
  leavesInGroup,
  listFamilies,
  listGroups,
  listLeaves,
  listTaxonomy,
  normalizeTitle,
} from "./index.ts";
import { CURATED_ALIASES, ONET_ALIASES } from "./generated/aliases.ts";
import { cleanTitle, extractDecoration, stripNoise } from "./normalize.ts";
import { EXCLUDED_SOC, TAXONOMY_SPEC, allLeafSpecs } from "./spec.ts";

// ---------------------------------------------------------------------------
// Structure
// ---------------------------------------------------------------------------

describe("taxonomy structure", () => {
  it("is three levels with every link resolving both ways", () => {
    for (const family of listFamilies()) {
      assert.ok(family.groups.length > 0, `${family.id} has no groups`);
      for (const groupId of family.groups) {
        const group = getGroup(groupId);
        assert.ok(group, `${family.id} points at missing group ${groupId}`);
        assert.equal(group.familyId, family.id);
        assert.ok(group.leaves.length > 0, `${groupId} has no leaves`);
        for (const leafId of group.leaves) {
          const leaf = getLeaf(leafId);
          assert.ok(leaf, `${groupId} points at missing leaf ${leafId}`);
          assert.equal(leaf.groupId, group.id);
          assert.equal(leaf.familyId, family.id);
        }
      }
    }
  });

  it("holds 55 to 70 leaves, which is the size a person can reason about", () => {
    const n = listLeaves().length;
    assert.ok(n >= 55 && n <= 70, `expected 55-70 leaves, got ${n}`);
  });

  it("has unique ids across all three levels", () => {
    const ids = [
      ...listFamilies().map((f) => f.id),
      ...listGroups().map((g) => g.id),
      ...listLeaves().map((l) => l.id),
    ];
    assert.equal(new Set(ids).size, ids.length);
  });

  it("gives every leaf a SOC crosswalk, a note and match rules", () => {
    for (const { leaf } of allLeafSpecs()) {
      assert.ok(leaf.soc.length > 0, `${leaf.id} has no SOC code`);
      for (const soc of leaf.soc) {
        assert.match(soc, /^\d{2}-\d{4}\.\d{2}$/, `${leaf.id} has malformed SOC ${soc}`);
      }
      assert.ok(leaf.match.length > 0, `${leaf.id} has no match rules`);
      assert.ok(leaf.note.length > 20, `${leaf.id} needs a real note`);
      assert.ok(leaf.aliases.length >= 3, `${leaf.id} has too few curated aliases`);
    }
  });

  it("resolves a leaf to its ancestors", () => {
    const ancestry = getAncestry("backend-engineer");
    assert.ok(ancestry);
    assert.equal(ancestry.leaf.name, "Backend Engineer");
    assert.equal(ancestry.group.id, "application-engineering");
    assert.equal(ancestry.family.id, "software-engineering");
    assert.equal(getAncestry("no-such-leaf"), null);
  });

  it("walks down from a family or a group", () => {
    const inFamily = leavesInFamily("data-and-ai").map((l) => l.id);
    assert.ok(inFamily.includes("ml-engineer"));
    assert.ok(inFamily.includes("data-analyst"));
    const inGroup = leavesInGroup("mobile-engineering").map((l) => l.id);
    assert.deepEqual(inGroup, ["mobile-engineer", "ios-engineer", "android-engineer"]);
    assert.deepEqual(leavesInFamily("nope"), []);
  });

  it("fans one SOC code out to many leaves, which is why this layer exists", () => {
    const softwareDevelopers = leavesForSoc("15-1252.00").map((l) => l.id);
    // The exact reason O*NET alone cannot be the retrieval key.
    for (const id of [
      "backend-engineer",
      "frontend-engineer",
      "devops-engineer",
      "site-reliability-engineer",
      "embedded-engineer",
      "software-architect",
    ]) {
      assert.ok(softwareDevelopers.includes(id), `15-1252.00 should reach ${id}`);
    }
    assert.ok(softwareDevelopers.length >= 15);
    assert.deepEqual(leavesForSoc("00-0000.00"), []);
  });

  it("makes every distinction the platform depends on", () => {
    // If any of these merge, ranking a CS/AI job feed stops working.
    const required = [
      "backend-engineer",
      "frontend-engineer",
      "full-stack-engineer",
      "mobile-engineer",
      "ios-engineer",
      "android-engineer",
      "ml-engineer",
      "ai-engineer",
      "ml-research-scientist",
      "data-engineer",
      "data-scientist",
      "data-analyst",
      "analytics-engineer",
      "devops-engineer",
      "site-reliability-engineer",
      "platform-engineer",
      "infrastructure-engineer",
      "security-engineer",
      "security-analyst",
      "qa-automation-engineer",
      "embedded-engineer",
      "systems-engineer",
      "solutions-architect",
      "engineering-manager",
      "product-manager",
      "technical-product-manager",
      "product-designer",
      "ux-designer",
    ];
    for (const id of required) assert.ok(getLeaf(id), `missing required leaf ${id}`);
  });

  it("accounts for every CS and Math SOC code, by crosswalk or by exclusion", () => {
    const crosswalked = new Set(listLeaves().flatMap((l) => l.soc));
    for (const soc of Object.keys(EXCLUDED_SOC)) {
      assert.ok(
        !crosswalked.has(soc),
        `${soc} is both excluded and crosswalked; pick one`,
      );
      assert.ok(EXCLUDED_SOC[soc].length > 40, `${soc} needs a real reason for exclusion`);
    }
  });

  it("carries the O*NET version it was built from", () => {
    const t = listTaxonomy();
    assert.equal(t.onetVersion, "30.3");
    assert.match(t.version, /^\d+\.\d+\.\d+$/);
  });
});

describe("alias maps", () => {
  it("point only at real leaves", () => {
    for (const [alias, leafId] of Object.entries({ ...CURATED_ALIASES, ...ONET_ALIASES })) {
      assert.ok(getLeaf(leafId), `alias "${alias}" points at unknown leaf ${leafId}`);
    }
  });

  it("are keyed in the same normalized form the runtime produces", () => {
    for (const alias of Object.keys({ ...CURATED_ALIASES, ...ONET_ALIASES })) {
      assert.equal(stripNoise(cleanTitle(alias)), alias, `alias key "${alias}" is not normalized`);
    }
  });

  it("never let O*NET shadow a curated decision", () => {
    for (const key of Object.keys(ONET_ALIASES)) {
      assert.equal(CURATED_ALIASES[key], undefined, `"${key}" is in both maps`);
    }
  });

  it("are big enough to be useful", () => {
    assert.ok(Object.keys(CURATED_ALIASES).length > 700);
    assert.ok(Object.keys(ONET_ALIASES).length > 200);
  });

  it("keeps SRE as site reliability, not O*NET's software requirements gloss", () => {
    // O*NET 15-1252.00 ships "Software Requirements Engineer (SRE)". If the
    // build ever lets that win, every SRE posting lands on the wrong leaf.
    assert.equal(CURATED_ALIASES["sre"], "site-reliability-engineer");
  });
});

// ---------------------------------------------------------------------------
// Normalization
// ---------------------------------------------------------------------------

function leafOf(raw: string): string | null {
  const r = normalizeTitle(raw);
  return r === null ? null : r.leafId;
}

describe("normalizeTitle: the cases from the brief", () => {
  it("strips a seniority prefix and a trailing level", () => {
    const r = normalizeTitle("Sr. Backend Engineer II");
    assert.ok(r);
    assert.equal(r.leafId, "backend-engineer");
    assert.equal(r.seniority, "senior");
    assert.equal(r.level, "II");
    assert.ok(r.confidence >= 0.8, `confidence was ${r.confidence}`);
  });

  it("ignores a team suffix after a comma", () => {
    const r = normalizeTitle("Machine Learning Engineer, Search");
    assert.ok(r);
    assert.equal(r.leafId, "ml-engineer");
    assert.equal(r.seniority, null);
  });

  it("reads SDE - New Grad 2027 as a software engineer, early career", () => {
    const r = normalizeTitle("SDE - New Grad 2027");
    assert.ok(r);
    assert.equal(r.leafId, "software-engineer");
    assert.equal(r.seniority, "new_grad");
    assert.equal(r.isEarlyCareer, true);
  });

  it("drops a parenthetical qualifier", () => {
    const r = normalizeTitle("Data Scientist (Product Analytics)");
    assert.ok(r);
    assert.equal(r.leafId, "data-scientist");
    assert.equal(r.method, "curated-alias", "reached by dropping the parenthetical");
    assert.equal(r.matchedOn, "data scientist");
    assert.ok(r.confidence >= 0.9, `confidence was ${r.confidence}`);
  });
});

describe("normalizeTitle: seniority is orthogonal, never a leaf", () => {
  const sameLeaf = [
    ["Software Engineer", null],
    ["Junior Software Engineer", "junior"],
    ["Software Engineer II", "mid"],
    ["Senior Software Engineer", "senior"],
    ["Staff Software Engineer", "staff"],
    ["Principal Software Engineer", "principal"],
    ["Distinguished Software Engineer", "distinguished"],
    ["Software Engineering Intern", "intern"],
  ] as const;

  for (const [title, seniority] of sameLeaf) {
    it(`"${title}" is one leaf plus seniority ${seniority ?? "null"}`, () => {
      const r = normalizeTitle(title);
      assert.ok(r, `${title} did not normalize`);
      assert.equal(r.leafId, "software-engineer");
      assert.equal(r.seniority, seniority);
    });
  }

  it("reports intern and co-op without inventing a leaf for them", () => {
    for (const title of [
      "Backend Engineer Intern",
      "Backend Engineering Co-op",
      "Backend Engineer, Summer Internship",
    ]) {
      const r = normalizeTitle(title);
      assert.ok(r, `${title} did not normalize`);
      assert.equal(r.leafId, "backend-engineer", title);
      assert.equal(r.isEarlyCareer, true, title);
    }
  });

  it("keeps management level as leaf boundary only where the job changes", () => {
    assert.equal(leafOf("Engineering Manager"), "engineering-manager");
    assert.equal(leafOf("Senior Engineering Manager"), "engineering-manager");
    assert.equal(leafOf("Director of Engineering"), "engineering-executive");
    assert.equal(leafOf("VP of Engineering"), "engineering-executive");
    assert.equal(leafOf("CTO"), "engineering-executive");
    assert.equal(describeSeniority("Director of Engineering").seniority, "director");
    assert.equal(describeSeniority("VP of Engineering").seniority, "vp");
  });

  it("does not read Product Manager as a management seniority", () => {
    const r = normalizeTitle("Product Manager");
    assert.ok(r);
    assert.equal(r.leafId, "product-manager");
    assert.equal(r.seniority, null, "manager here is the role, not a rung");
  });
});

describe("normalizeTitle: abbreviations and modern titles O*NET lacks", () => {
  const cases: ReadonlyArray<readonly [string, string]> = [
    ["SWE", "software-engineer"],
    ["SDE", "software-engineer"],
    ["SDE II", "software-engineer"],
    ["Sr. Software Engineer", "software-engineer"],
    ["MLE", "ml-engineer"],
    ["Backend Developer", "backend-engineer"],
    ["Back-End Engineer", "backend-engineer"],
    ["Front End Developer", "frontend-engineer"],
    ["Fullstack Developer", "full-stack-engineer"],
    ["Founding Engineer", "software-engineer"],
    ["Forward Deployed Engineer", "solutions-architect"],
    ["SRE", "site-reliability-engineer"],
    ["Site Reliability Engineer (Platform)", "site-reliability-engineer"],
    ["DevOps Engineer", "devops-engineer"],
    ["Platform Engineer", "platform-engineer"],
    ["iOS Developer", "ios-engineer"],
    ["Android Engineer (Kotlin)", "android-engineer"],
    ["React Native Developer", "mobile-engineer"],
    ["LLM Engineer", "ai-engineer"],
    ["Generative AI Engineer", "ai-engineer"],
    ["Prompt Engineer", "ai-engineer"],
    ["MLOps Engineer", "mlops-engineer"],
    ["ML Platform Engineer", "mlops-engineer"],
    ["Analytics Engineer", "analytics-engineer"],
    ["dbt Developer", "analytics-engineer"],
    ["SDET", "qa-automation-engineer"],
    ["Software Development Engineer in Test", "qa-automation-engineer"],
    ["AppSec Engineer", "application-security-engineer"],
    ["Pentester", "penetration-tester"],
    ["Technical Program Manager", "technical-program-manager"],
    ["TPM", "technical-program-manager"],
    ["Scrum Master", "scrum-master"],
    ["DevRel Engineer", "developer-advocate"],
    ["UI/UX Designer", "ux-designer"],
    ["Solidity Developer", "blockchain-engineer"],
    // Two disciplines tied on specificity, so the pattern layer abstains and
    // the head of the title decides. Better than null, and better than a coin
    // flip between them.
    ["DevOps / Cloud Engineer", "devops-engineer"],
    ["FPGA Design Engineer", "asic-fpga-engineer"],
    ["Sensor Fusion Engineer", "autonomy-engineer"],
    ["Computational Biologist", "bioinformatics-scientist"],
  ];

  for (const [title, expected] of cases) {
    it(`"${title}" -> ${expected}`, () => {
      assert.equal(leafOf(title), expected);
    });
  }
});

describe("normalizeTitle: real postings with decoration", () => {
  const cases: ReadonlyArray<readonly [string, string]> = [
    ["Senior Software Engineer, Backend (Payments)", "backend-engineer"],
    ["Software Engineer - Frontend", "frontend-engineer"],
    ["Staff Software Engineer, Machine Learning", "ml-engineer"],
    ["Software Engineer III, Site Reliability", "site-reliability-engineer"],
    ["Senior Data Engineer | Remote (US)", "data-engineer"],
    ["Data Analyst, Growth — Full Time", "data-analyst"],
    ["Sr Security Engineer (Detection & Response)", "security-engineer"],
    ["Lead QA Automation Engineer [Req 48213]", "qa-automation-engineer"],
    ["Embedded Software Engineer (C/C++), Robotics", "embedded-engineer"],
    ["Engineering Manager, Machine Learning", "engineering-manager"],
    ["Technical Product Manager - API Platform", "technical-product-manager"],
    ["Product Designer (Design Systems)", "product-designer"],
    ["Principal Solutions Architect, AWS", "solutions-architect"],
    ["Research Engineer, Alignment", "research-engineer"],
    ["Applied Scientist II", "applied-scientist"],
    ["Sr. Full Stack Engineer (Remote, EMEA)", "full-stack-engineer"],
    ["Database Administrator (PostgreSQL)", "database-administrator"],
    ["IT Support Specialist, Tier 2", "it-support-specialist"],
    ["Business Intelligence Analyst II", "business-intelligence-analyst"],
    ["Computer Vision Engineer, Perception", "computer-vision-engineer"],
  ];

  for (const [title, expected] of cases) {
    it(`"${title}" -> ${expected}`, () => {
      assert.equal(leafOf(title), expected);
    });
  }

  it("survives odd whitespace, casing and typographic punctuation", () => {
    for (const title of [
      "  senior   BACKEND   engineer  ",
      "Senior Backend Engineer",
      "Senior Backend Engineer—Payments",
      "SENIOR BACK-END ENGINEER",
    ]) {
      assert.equal(leafOf(title), "backend-engineer", JSON.stringify(title));
    }
  });
});

describe("normalizeTitle: must return null rather than guess", () => {
  const mustBeNull = [
    // Explicitly required by the brief.
    "Registered Nurse",
    "Golf Course Attendant",
    // Other occupations that share vocabulary with tech titles.
    "Nurse Informaticist",
    "Clinical Informatics Nurse",
    "Security Guard",
    "Truck Driver",
    "Barista",
    "Executive Assistant",
    "Paralegal",
    "Technical Recruiter",
    "High School Teacher",
    "Warehouse Associate",
    "Mechanical Engineer",
    // Titles too generic to place, where a guess would be a real error.
    "Engineer",
    "Analyst",
    "Consultant",
    "Manager",
    "Project Manager",
    "Business Analyst",
    "Financial Analyst",
    "Marketing Manager",
    "Research Scientist",
    "Statistician",
    "Actuary",
    "Graphic Designer",
    "Sales Engineer",
    "",
    "   ",
    "12345",
  ];

  for (const title of mustBeNull) {
    it(`"${title}" -> null`, () => {
      assert.equal(normalizeTitle(title), null);
    });
  }

  it("explains why bare Research Scientist stays null", () => {
    // O*NET files "Research Scientist" under computer science (15-1221.00),
    // data science (15-2051.00) and bioinformatics (19-1029.01) at once.
    assert.equal(normalizeTitle("Research Scientist"), null);
    // Qualify it and it resolves.
    assert.equal(leafOf("Machine Learning Research Scientist"), "ml-research-scientist");
    assert.equal(leafOf("Bioinformatics Research Scientist"), "bioinformatics-scientist");
  });

  it("does not let a tech word rescue a non-tech occupation", () => {
    assert.equal(normalizeTitle("Data Entry Clerk"), null);
    assert.equal(normalizeTitle("Computer Operator"), null);
    assert.equal(normalizeTitle("Robotic Process Automation Analyst"), null);
  });
});

describe("normalizeTitle: near neighbours stay apart", () => {
  const pairs: ReadonlyArray<readonly [string, string, string, string]> = [
    ["Backend Engineer", "backend-engineer", "Frontend Engineer", "frontend-engineer"],
    ["ML Engineer", "ml-engineer", "AI Engineer", "ai-engineer"],
    ["ML Engineer", "ml-engineer", "ML Research Scientist", "ml-research-scientist"],
    ["Data Engineer", "data-engineer", "Data Scientist", "data-scientist"],
    ["Data Analyst", "data-analyst", "Analytics Engineer", "analytics-engineer"],
    ["DevOps Engineer", "devops-engineer", "Site Reliability Engineer", "site-reliability-engineer"],
    ["Platform Engineer", "platform-engineer", "Infrastructure Engineer", "infrastructure-engineer"],
    ["Security Engineer", "security-engineer", "Security Analyst", "security-analyst"],
    ["QA Automation Engineer", "qa-automation-engineer", "QA Analyst", "qa-analyst"],
    ["Systems Engineer", "systems-engineer", "Systems Administrator", "systems-administrator"],
    ["Software Architect", "software-architect", "Solutions Architect", "solutions-architect"],
    ["Product Manager", "product-manager", "Technical Program Manager", "technical-program-manager"],
    ["Game Engineer", "game-engineer", "Game Designer", "game-designer"],
    ["UX Designer", "ux-designer", "UX Researcher", "ux-researcher"],
    ["Hardware Engineer", "hardware-engineer", "Embedded Software Engineer", "embedded-engineer"],
  ];

  for (const [aTitle, aLeaf, bTitle, bLeaf] of pairs) {
    it(`"${aTitle}" is not "${bTitle}"`, () => {
      assert.equal(leafOf(aTitle), aLeaf);
      assert.equal(leafOf(bTitle), bLeaf);
    });
  }
});

describe("normalizeTitle: result shape", () => {
  it("reports how the match was made", () => {
    const curated = normalizeTitle("Backend Engineer");
    assert.ok(curated);
    assert.equal(curated.method, "curated-alias");
    assert.equal(curated.confidence, 1);
    assert.equal(curated.normalizedInput, "backend engineer");

    const pattern = normalizeTitle("Golang Backend Developer, Payments");
    assert.ok(pattern);
    assert.equal(pattern.leafId, "backend-engineer");
    assert.equal(pattern.method, "pattern");
    assert.ok(pattern.confidence < 0.75, `pattern confidence was ${pattern.confidence}`);
  });

  it("scores exact aliases above every pattern guess", () => {
    const exact = normalizeTitle("Data Engineer");
    const guess = normalizeTitle("Golang Backend Developer, Payments");
    assert.ok(exact && guess);
    assert.ok(exact.confidence > guess.confidence);
    assert.ok(exact.confidence >= 0.75);
    assert.ok(guess.confidence <= 0.74);
  });

  it("keeps the leaf reachable from the result", () => {
    const r = normalizeTitle("Sr. MLOps Engineer");
    assert.ok(r);
    const ancestry = getAncestry(r.leafId);
    assert.ok(ancestry);
    assert.equal(ancestry.group.id, "ml-and-ai-engineering");
    assert.equal(ancestry.family.name, "Data and AI");
    assert.ok(getFamily(ancestry.family.id));
  });

  it("is deterministic", () => {
    const a = normalizeTitle("Senior Software Engineer, Backend (Payments)");
    const b = normalizeTitle("Senior Software Engineer, Backend (Payments)");
    assert.deepEqual(a, b);
  });
});

describe("decoration parsing", () => {
  it("reads levels only at the tail, where job boards put them", () => {
    assert.equal(extractDecoration("software engineer iii").level, "III");
    assert.equal(extractDecoration("software engineer l5").level, "L5");
    assert.equal(extractDecoration("sde ii").level, "II");
    // Not a level: a tier inside the title.
    assert.equal(extractDecoration("tier 2 support specialist").level, null);
  });

  it("prefers the explicit word over the numeral", () => {
    const r = extractDecoration("senior software engineer ii");
    assert.equal(r.seniority, "senior");
    assert.equal(r.level, "II");
  });

  it("does not invent seniority from a bare digit mid-title", () => {
    const r = extractDecoration("soc 2 compliance analyst");
    assert.equal(r.seniority, null);
  });
});
