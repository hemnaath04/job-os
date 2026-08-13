/**
 * The hand-authored leaf layer. This file is the source of truth for the
 * taxonomy; everything under `generated/` is derived from it plus the O*NET
 * CSVs by `scripts/build-taxonomy.ts`.
 *
 * Why hand-authored at all: O*NET is an occupational classification, not a
 * retrieval key. 15-1252.00 Software Developers carries 93 alternate titles and
 * swallows Backend, Frontend, Full Stack, Mobile, DevOps, SRE, Embedded,
 * Systems and Architect work in one node. A CS/AI job platform that cannot tell
 * a Backend Engineer from an ML Engineer from an SRE cannot rank, filter or
 * tailor anything. So the leaf layer is refined by hand and crosswalked back to
 * SOC, which buys provenance and, more usefully, O*NET's alternate titles as
 * free alias data.
 *
 * Rules for anyone editing this file:
 *
 *  - Seniority is not a taxonomy node. "Senior Backend Engineer" is the Backend
 *    Engineer leaf plus seniority `senior`. Never add a leaf whose only
 *    difference from another is a rung.
 *  - Nor is intern / new grad. Same reason, separate field.
 *  - A qualifier earns its own leaf when it changes the stack enough that a job
 *    seeker filters on it (Backend vs Frontend), not when it only changes the
 *    subject matter (AI PM vs PM).
 *  - Precision beats recall. A leaf with no confident rule is better left
 *    unmatched than given a loose one; the deny list in `normalize.ts` and the
 *    deliberately unmapped SOC codes below both exist for that reason.
 *
 * Contains modified O*NET data. See apps/web/data/onet/NOTICE.md.
 */

import type { MatchRule } from "./match.ts";
import type { SocCode } from "./types.ts";

/** Bumped by hand when the leaf layer changes shape. */
export const TAXONOMY_VERSION = "1.0.0";

/** O*NET release the committed CSVs came from. */
export const ONET_VERSION = "30.3";

// ---------------------------------------------------------------------------
// Shared vocabulary. Small, anchored, reused, so that "role noun" means the
// same thing in forty rules instead of forty slightly different things.
// ---------------------------------------------------------------------------

/**
 * Individual-contributor engineering role nouns. "engineering" is in here on
 * purpose: `\bengineer\b` does not match "Software Engineering Intern", and
 * leaving it out silently drops every "... Engineering Intern" and
 * "... Engineering Co-op" posting.
 */
const ENG = /\b(engineer|engineering|developer|dev|programmer|swe|sde|sdet|coder)\b/;
const ARCHITECT = /\barchitect\b/;
const ANALYST = /\banalyst\b/;
const SCIENTIST = /\b(scientist|researcher|research)\b/;
const DESIGNER = /\bdesign(er)?\b/;
const MANAGER = /\b(manager|mgr)\b/;

/**
 * Any technical role noun. Used to anchor rules whose discipline word is a
 * domain rather than a job ("robotics", "blockchain", "genomics"), so that
 * "Lab Technician, Robotics" and "Sourcer, Blockchain" do not become engineers.
 */
const TECH_ROLE =
  /\b(engineer|engineering|developer|dev|programmer|swe|sde|scientist|researcher|research|architect|analyst|specialist|designer|intern)\b/;

export interface LeafSpec {
  id: string;
  name: string;
  /** Most representative O*NET-SOC code first. */
  soc: SocCode[];
  /** Where this leaf's boundary sits and why. Ships in the artifact. */
  note: string;
  /** Tie-break weight. See TaxonomyLeaf.specificity. */
  specificity: number;
  /** Hand-written aliases, in human form; the build cleans and keys them. */
  aliases: string[];
  /** Fallback rules, also used to decide which O*NET titles this leaf claims. */
  match: MatchRule[];
}

export interface GroupSpec {
  id: string;
  name: string;
  leaves: LeafSpec[];
}

export interface FamilySpec {
  id: string;
  name: string;
  groups: GroupSpec[];
}

export const TAXONOMY_SPEC: readonly FamilySpec[] = [
  // =========================================================================
  {
    id: "software-engineering",
    name: "Software Engineering",
    groups: [
      {
        id: "application-engineering",
        name: "Application Engineering",
        leaves: [
          {
            id: "software-engineer",
            name: "Software Engineer",
            soc: ["15-1252.00", "15-1251.00"],
            note: "The unqualified role. Kept deliberately generic and at the lowest specificity in the family so that any title naming a discipline outranks it; a posting that only says 'Software Engineer' genuinely has not said more than that.",
            specificity: 10,
            aliases: [
              "software engineer",
              "software developer",
              "software development engineer",
              "software dev engineer",
              "software design engineer",
              "software systems developer",
              "computer software engineer",
              "application developer",
              "applications developer",
              "application engineer",
              "application programmer",
              "computer application developer",
              "business application developer",
              "swe",
              "sde",
              "developer",
              "programmer",
              "computer programmer",
              "software programmer",
              "coder",
              "founding engineer",
              "member of technical staff",
              "mts",
              "product engineer",
              "generalist engineer",
              "polyglot engineer",
            ],
            match: [
              { all: [/\b(software|applications?)\b/, ENG] },
              {
                all: [/\bprogrammer\b/],
                none: [/\b(analyst|numerical control|nc programmer)\b/],
              },
              {
                all: [
                  /\b(java|python|c\+\+|c#|\.net|dotnet|golang|scala|rust|cobol|perl|object oriented)\b/,
                  ENG,
                ],
              },
            ],
          },
          {
            id: "backend-engineer",
            name: "Backend Engineer",
            soc: ["15-1252.00"],
            note: "Server-side application work. API, microservice and distributed-systems titles fold in because they name the same stack from a different angle.",
            specificity: 32,
            aliases: [
              "backend engineer",
              "back end engineer",
              "backend developer",
              "back end developer",
              "backend software engineer",
              "backend swe",
              "backend sde",
              "server side engineer",
              "api engineer",
              "api developer",
              "distributed systems engineer",
              "microservices developer",
              "backend web developer",
              "rails developer",
              "ruby on rails developer",
              "node js developer",
              "django developer",
              "spring boot developer",
            ],
            match: [
              { all: [/\bback ?end\b/, ENG] },
              { all: [/\b(server ?side|serverside)\b/, ENG] },
              {
                all: [/\b(api|microservices?|distributed systems)\b/, ENG],
                none: [/\b(architect|test|qa|manager|design)\b/],
              },
            ],
          },
          {
            id: "frontend-engineer",
            name: "Frontend Engineer",
            soc: ["15-1252.00", "15-1254.00"],
            note: "Component-framework product work. Framework-named titles (React, Angular, Vue) fold in; they are the clearest signal a posting is frontend.",
            specificity: 32,
            aliases: [
              "frontend engineer",
              "front end engineer",
              "frontend developer",
              "front end developer",
              "frontend software engineer",
              "front end software engineer",
              "ui engineer",
              "ux engineer",
              "client side engineer",
              "javascript engineer",
              "javascript developer",
              "typescript engineer",
              "react developer",
              "react engineer",
              "reactjs developer",
              "angular developer",
              "vue developer",
              "frontend web developer",
              "web frontend engineer",
            ],
            match: [
              { all: [/\bfront ?end\b/, ENG] },
              { all: [/\b(client ?side|clientside)\b/, ENG] },
              {
                all: [/\b(react|reactjs|angular|vue|svelte|javascript|typescript)\b/, ENG],
                none: [/\b(full ?stack|back ?end|node|native)\b/],
              },
              { all: [/\bu[ix] engineer\b/] },
            ],
          },
          {
            id: "full-stack-engineer",
            name: "Full Stack Engineer",
            soc: ["15-1252.00", "15-1254.00"],
            note: "Above Backend and Frontend in specificity, so a title claiming both resolves here rather than to whichever half is mentioned first.",
            specificity: 36,
            aliases: [
              "full stack engineer",
              "fullstack engineer",
              "full stack developer",
              "fullstack developer",
              "full stack software engineer",
              "full stack web developer",
              "full stack javascript developer",
              "mern stack developer",
              "mean stack developer",
            ],
            match: [
              { all: [/\bfull ?stack\b/, ENG] },
              { all: [/\b(mern|mean) stack\b/] },
            ],
          },
          {
            id: "web-developer",
            name: "Web Developer",
            soc: ["15-1254.00", "15-1299.01"],
            note: "Kept apart from Frontend Engineer on purpose: CMS, template and agency work (WordPress, Drupal, Shopify, PHP) barely shares a candidate pool with component-framework product work, and O*NET keeps 15-1254.00 separate too.",
            specificity: 24,
            aliases: [
              "web developer",
              "web programmer",
              "website developer",
              "website programmer",
              "web application developer",
              "web applications programmer",
              "web engineer",
              "webmaster",
              "wordpress developer",
              "drupal developer",
              "shopify developer",
              "magento developer",
              "php developer",
              "web content developer",
              "internet application developer",
              "internet programmer",
            ],
            match: [
              {
                all: [/\b(web|website|wordpress|drupal|shopify|magento|php)\b/, ENG],
                none: [/\b(front ?end|full ?stack|back ?end|architect|design|designer|security|project)\b/],
              },
            ],
          },
          {
            id: "blockchain-engineer",
            name: "Blockchain Engineer",
            soc: ["15-1299.07"],
            note: "O*NET gave this its own occupation in 15-1299.07, and the skill set really is disjoint from ordinary backend work.",
            specificity: 40,
            aliases: [
              "blockchain engineer",
              "blockchain developer",
              "smart contract engineer",
              "smart contract developer",
              "solidity developer",
              "solidity engineer",
              "web3 engineer",
              "web3 developer",
              "defi engineer",
              "distributed ledger engineer",
              "cryptocurrency developer",
            ],
            match: [
              {
                all: [
                  /\b(blockchain|smart contract|solidity|web3|defi|distributed ledger|cryptocurrency)\b/,
                  TECH_ROLE,
                ],
              },
            ],
          },
        ],
      },
      {
        id: "mobile-engineering",
        name: "Mobile Engineering",
        leaves: [
          {
            id: "mobile-engineer",
            name: "Mobile Engineer",
            soc: ["15-1252.00"],
            note: "Platform-agnostic and cross-platform mobile work. Sits below the iOS and Android leaves so a title naming a platform wins.",
            specificity: 30,
            aliases: [
              "mobile engineer",
              "mobile developer",
              "mobile software engineer",
              "mobile application developer",
              "mobile app developer",
              "mobile device developer",
              "react native developer",
              "react native engineer",
              "flutter developer",
              "flutter engineer",
              "cross platform mobile engineer",
            ],
            match: [
              {
                all: [/\b(mobile|react native|flutter|xamarin|ionic)\b/, ENG],
                none: [/\b(ios|android|swift|kotlin|game|web)\b/],
              },
            ],
          },
          {
            id: "ios-engineer",
            name: "iOS Engineer",
            soc: ["15-1252.00"],
            note: "Apple-platform work. `ios` is guarded against Cisco IOS, which is a networking title.",
            specificity: 42,
            aliases: [
              "ios engineer",
              "ios developer",
              "ios software engineer",
              "ios application developer",
              "ios mobile engineer",
              "iphone developer",
              "swift developer",
              "swift engineer",
              "swiftui developer",
              "apple platforms engineer",
              "objective c developer",
            ],
            match: [
              {
                all: [/\b(ios|iphone|ipad|swift|swiftui|objective c)\b/, ENG],
                none: [/\b(cisco|network|routing)\b/],
              },
              { all: [/\bapple platforms?\b/, ENG] },
            ],
          },
          {
            id: "android-engineer",
            name: "Android Engineer",
            soc: ["15-1252.00"],
            note: "Android and Kotlin work.",
            specificity: 42,
            aliases: [
              "android engineer",
              "android developer",
              "android software engineer",
              "android application developer",
              "android mobile engineer",
              "kotlin developer",
              "kotlin engineer",
            ],
            match: [{ all: [/\b(android|kotlin)\b/, ENG] }],
          },
        ],
      },
      {
        id: "systems-engineering",
        name: "Systems and Embedded Engineering",
        leaves: [
          {
            id: "systems-engineer",
            name: "Systems Engineer",
            soc: ["15-1252.00", "15-1299.08"],
            note: "Writing the layer underneath: kernels, operating systems, compilers, runtimes, storage engines. Distinct from Infrastructure Engineer, which runs fleets rather than building the thing they run. The deny list on this rule is long because 'systems' is the single most overloaded word in tech titles.",
            specificity: 22,
            aliases: [
              "systems engineer",
              "system engineer",
              "systems software engineer",
              "systems software developer",
              "systems programmer",
              "systems developer",
              "kernel engineer",
              "kernel developer",
              "operating systems engineer",
              "os engineer",
              "low level engineer",
              "compiler engineer",
              "compiler developer",
              "runtime engineer",
              "storage engineer",
              "systems integration engineer",
              "enterprise systems engineer",
              "mainframe systems programmer",
            ],
            match: [
              {
                all: [/\b(systems?|kernel|compiler|runtime|operating system|os)\b/, ENG],
                none: [
                  /\b(administrator|admin|analyst|architect|manager|security|network|information|embedded|data|hardware|robotic|business|payment|test|qa|cloud|platform|reliability|devops)\b/,
                ],
              },
            ],
          },
          {
            id: "embedded-engineer",
            name: "Embedded Software Engineer",
            soc: ["15-1252.00", "17-2061.00"],
            note: "Firmware folds in. 'Embedded Software Engineer' and 'Firmware Engineer' describe the same C-on-a-microcontroller job and splitting them would only split the aliases. 'Embedded Hardware Engineer' is excluded so it lands on Hardware Engineer, where it belongs.",
            specificity: 44,
            aliases: [
              "embedded engineer",
              "embedded software engineer",
              "embedded systems engineer",
              "embedded developer",
              "embedded systems software developer",
              "embedded c developer",
              "firmware engineer",
              "firmware developer",
              "embedded firmware engineer",
              "rtos engineer",
              "bare metal engineer",
              "device driver engineer",
              "driver developer",
              "bsp engineer",
              "microcontroller engineer",
            ],
            match: [
              {
                all: [/\b(embedded|firmware|rtos|microcontroller|bare metal|device driver)\b/, TECH_ROLE],
                none: [/\bembedded hardware\b/],
              },
            ],
          },
          {
            id: "game-engineer",
            name: "Game Engineer",
            soc: ["15-1252.00", "15-1255.01"],
            note: "Engine, gameplay and rendering code. Graphics and shader titles fold in here rather than getting a leaf of their own; almost all of them are games or real-time rendering. Game Designer is a separate leaf in the Design family because it is not a programming job.",
            specificity: 40,
            aliases: [
              "game engineer",
              "game developer",
              "game programmer",
              "gameplay engineer",
              "gameplay programmer",
              "video game engineer",
              "video game developer",
              "video game programmer",
              "computer game programmer",
              "game engine engineer",
              "engine programmer",
              "unreal engineer",
              "unreal developer",
              "unity developer",
              "unity engineer",
              "graphics engineer",
              "rendering engineer",
              "shader programmer",
              "game client engineer",
            ],
            match: [
              { all: [/\b(game|gameplay|unreal|unity)\b/, ENG] },
              { all: [/\b(graphics|rendering|shader)\b/, ENG] },
            ],
          },
        ],
      },
      {
        id: "quality-engineering",
        name: "Quality Engineering",
        leaves: [
          {
            id: "qa-automation-engineer",
            name: "QA Automation Engineer",
            soc: ["15-1253.00"],
            note: "Writes test code. Bare 'Automation Engineer' is deliberately not an alias: in O*NET it sits under Robotics Engineers and in the wild it is usually industrial, not software. Hardware and manufacturing test titles are excluded so they fall to the hardware leaves.",
            specificity: 46,
            aliases: [
              "qa automation engineer",
              "automation qa engineer",
              "test automation engineer",
              "automation test engineer",
              "sdet",
              "software development engineer in test",
              "software engineer in test",
              "qa engineer",
              "quality assurance engineer",
              "software quality assurance engineer",
              "sqa engineer",
              "software quality engineer",
              "sqe",
              "software test engineer",
              "test engineer",
              "performance test engineer",
              "mobile automation engineer",
            ],
            match: [
              {
                all: [/\b(qa|quality assurance|sdet|test|testing)\b/, ENG],
                none: [
                  /\b(hardware|manufacturing|mechanical|electrical|asic|fpga|silicon|chip|validation lab|penetration|pen)\b/,
                ],
              },
              { all: [/\bengineer in test\b/] },
              { all: [/\bautomation\b/, /\b(qa|test|quality)\b/, ENG] },
            ],
          },
          {
            id: "qa-analyst",
            name: "QA Analyst",
            soc: ["15-1253.00"],
            note: "Manual and exploratory testing. Shares SOC 15-1253.00 with QA Automation but is a different hiring track, and conflating them would tell an automation candidate to apply for click-through work.",
            specificity: 44,
            aliases: [
              "qa analyst",
              "quality assurance analyst",
              "software quality assurance analyst",
              "sqa analyst",
              "quality analyst",
              "qa tester",
              "qa specialist",
              "manual tester",
              "manual qa",
              "manual qa engineer",
              "software tester",
              "test analyst",
              "quality assurance tester",
              "beta tester",
              "game tester",
              "usability tester",
            ],
            match: [
              { all: [/\b(qa|quality assurance)\b/, /\b(analyst|tester|specialist)\b/] },
              { all: [/\bmanual\b/, /\b(qa|test|testing)\b/] },
              { all: [/\b(software|game|manual|beta) tester\b/] },
            ],
          },
        ],
      },
      {
        id: "software-architecture",
        name: "Software Architecture",
        leaves: [
          {
            id: "software-architect",
            name: "Software Architect",
            soc: ["15-1252.00", "15-1299.08"],
            note: "Internal system design. Bare 'Architect' is never an alias, for the obvious reason that most of them design buildings.",
            specificity: 50,
            aliases: [
              "software architect",
              "application architect",
              "applications architect",
              "software application architect",
              "application systems architect",
              "software systems architect",
              "computer systems software architect",
              "systems architect",
              "computer systems architect",
              "technical architect",
              "platform architect",
              "principal software architect",
            ],
            match: [
              {
                all: [/\b(software|applications?|technical|systems?|platform)\b/, ARCHITECT],
                none: [
                  /\b(data|network|security|cloud|solutions?|enterprise|information|web|hardware|java|building|landscape)\b/,
                ],
              },
            ],
          },
          {
            id: "solutions-architect",
            name: "Solutions Architect",
            soc: ["15-1299.08", "15-1211.00"],
            note: "The customer-facing build-and-integrate role. Cloud Architect and Enterprise Architect fold in, and so does the Palantir-style Forward Deployed Engineer: what these share is that the work is scoped by somebody else's environment, which matters more than the stack. Bare 'Sales Engineer' stays out; it is a quota role.",
            specificity: 52,
            aliases: [
              "solutions architect",
              "solution architect",
              "solutions engineer",
              "solution engineer",
              "technical solutions engineer",
              "cloud architect",
              "cloud solutions architect",
              "cloud solution architect",
              "enterprise architect",
              "forward deployed engineer",
              "forward deployed software engineer",
              "implementation engineer",
              "integration engineer",
              "application integration engineer",
              "technical consultant",
              "it consultant",
              "information technology consultant",
              "technology consultant",
              "systems consultant",
              "presales engineer",
              "pre sales engineer",
            ],
            match: [
              { all: [/\b(solutions?|enterprise|cloud)\b/, ARCHITECT] },
              { all: [/\bsolutions?\b/, ENG], none: [/\b(manager|director|analyst)\b/] },
              { all: [/\bforward deployed\b/] },
            ],
          },
        ],
      },
    ],
  },

  // =========================================================================
  {
    id: "data-and-ai",
    name: "Data and AI",
    groups: [
      {
        id: "data-engineering",
        name: "Data Engineering",
        leaves: [
          {
            id: "data-engineer",
            name: "Data Engineer",
            soc: ["15-2051.00", "15-1243.00", "15-1243.01"],
            note: "Pipelines and warehouses. Data Platform Engineer and Data Warehouse Engineer fold in; O*NET keeps 15-1243.01 Data Warehousing Specialists separate, but the 2026 market does not and neither should a retrieval key.",
            specificity: 40,
            aliases: [
              "data engineer",
              "big data engineer",
              "data pipeline engineer",
              "data infrastructure engineer",
              "data platform engineer",
              "data warehouse engineer",
              "data warehouse developer",
              "data warehousing specialist",
              "etl developer",
              "etl engineer",
              "elt developer",
              "extract transform load developer",
              "dataops engineer",
              "spark engineer",
              "hadoop engineer",
              "snowflake engineer",
              "databricks engineer",
              "streaming data engineer",
              "database developer",
              "database programmer",
            ],
            match: [
              {
                all: [/\b(data|etl|elt|data warehouse|datawarehouse|pipeline)\b/, ENG],
                // Prefixes, not whole words: "scien" has to knock out both
                // "science" and "scientist", "analy" both "analyst" and
                // "analytics". A trailing \b here would silently match neither.
                none: [
                  /\b(scien|analy|architect|manager|cent(er|re)|governance|privacy|annotat|label|quality|product|visuali|reporting|entry)/,
                ],
              },
            ],
          },
          {
            id: "analytics-engineer",
            name: "Analytics Engineer",
            soc: ["15-2051.00", "15-2051.01"],
            note: "The dbt-and-warehouse-modelling role between Data Engineer and Data Analyst. Business Intelligence Engineer and Developer fold in; BI Analyst stays separate because that job is reporting and stakeholder work, not modelling.",
            specificity: 42,
            aliases: [
              "analytics engineer",
              "data analytics engineer",
              "analytics developer",
              "dbt developer",
              "dbt engineer",
              "bi engineer",
              "bi developer",
              "business intelligence engineer",
              "business intelligence developer",
              "reporting engineer",
              "reporting developer",
              "tableau developer",
              "power bi developer",
              "looker developer",
              "data visualization developer",
            ],
            match: [
              {
                all: [
                  /\b(analytics|business intelligence|bi|dbt|tableau|power bi|looker|data visualization|reporting)\b/,
                  ENG,
                ],
                none: [/\b(manager|director|marketing|machine learning|ml)\b/],
              },
            ],
          },
          {
            id: "data-architect",
            name: "Data Architect",
            soc: ["15-1243.00"],
            note: "O*NET's 15-1243.00 Database Architects. Modelling and governance rather than pipeline building, which is why it is not folded into Data Engineer.",
            specificity: 48,
            aliases: [
              "data architect",
              "database architect",
              "enterprise data architect",
              "data warehouse architect",
              "master data architect",
              "data modeler",
              "data model architect",
              "information architect",
            ],
            match: [
              { all: [/\b(data|database|information)\b/, ARCHITECT] },
              { all: [/\bdata modell?er\b/] },
            ],
          },
          {
            id: "database-administrator",
            name: "Database Administrator",
            soc: ["15-1242.00"],
            note: "Running the database rather than modelling or querying it. 'Database Engineer' and 'Database Reliability Engineer' fold in, since in practice they are DBA postings with a nicer title.",
            specificity: 46,
            aliases: [
              "database administrator",
              "database admin",
              "dba",
              "sql dba",
              "oracle dba",
              "sql server dba",
              "postgres dba",
              "mysql dba",
              "database engineer",
              "database reliability engineer",
              "database specialist",
              "database operations engineer",
            ],
            match: [
              { all: [/\b(database|db)\b/, /\b(administrator|admin)\b/] },
              { all: [/\bdba\b/] },
              { all: [/\bdatabase\b/, /\b(reliability|operations)?\s*engineer\b/], none: [/\b(architect|developer|programmer|analyst|data engineer)\b/] },
            ],
          },
        ],
      },
      {
        id: "data-science-and-analytics",
        name: "Data Science and Analytics",
        leaves: [
          {
            id: "data-scientist",
            name: "Data Scientist",
            soc: ["15-2051.00"],
            note: "O*NET's 15-2051.00 as intended, minus the ML-engineering and analyst titles its alternate-title list also files there.",
            specificity: 40,
            aliases: [
              "data scientist",
              "data science",
              "data analytics scientist",
              "data analytic scientist",
              "data science engineer",
              "data management scientist",
              "product data scientist",
              "marketing data scientist",
              "decision scientist",
            ],
            match: [
              { all: [/\bdata scien(ce|tist)\b/] },
              { all: [/\bdecision scientist\b/] },
            ],
          },
          {
            id: "data-analyst",
            name: "Data Analyst",
            soc: ["15-2051.00", "15-2051.01"],
            note: "Product Analyst and Insights Analyst fold in: same reporting-and-experimentation job under different org names. Business, financial, credit, risk and clinical analysts are excluded because most of those postings contain no software at all.",
            specificity: 34,
            aliases: [
              "data analyst",
              "data analytics analyst",
              "data analytics specialist",
              "data quality analyst",
              "data mining analyst",
              "data specialist",
              "analytics analyst",
              "analytics specialist",
              "statistical analyst",
              "product analyst",
              "growth analyst",
              "insights analyst",
              "reporting analyst",
              "data operations analyst",
              "research analyst",
            ],
            match: [
              {
                all: [/\b(data|analytics|product|insights|reporting)\b/, ANALYST],
                none: [
                  /\b(business|financial|finance|security|market|marketing|hr|credit|risk|compliance|clinical|health|quality assurance|qa|systems?|network)\b/,
                ],
              },
            ],
          },
          {
            id: "business-intelligence-analyst",
            name: "Business Intelligence Analyst",
            soc: ["15-2051.01"],
            note: "Its own O*NET occupation and its own hiring track: dashboards and stakeholder reporting, above Data Analyst in specificity so the BI qualifier wins.",
            specificity: 44,
            aliases: [
              "business intelligence analyst",
              "bi analyst",
              "business intelligence specialist",
              "bi specialist",
              "business intelligence consultant",
              "business analytics analyst",
              "business intelligence reporting analyst",
            ],
            match: [
              {
                all: [/\b(business intelligence|bi)\b/, /\b(analyst|specialist|consultant)\b/],
              },
            ],
          },
        ],
      },
      {
        id: "ml-and-ai-engineering",
        name: "Machine Learning and AI Engineering",
        leaves: [
          {
            id: "ml-engineer",
            name: "Machine Learning Engineer",
            soc: ["15-2051.00", "15-1299.08"],
            note: "Training and serving models. NLP, speech, ranking and recommendation engineers fold in: same toolchain, and splitting them would split a thin alias set for no filtering gain.",
            specificity: 46,
            aliases: [
              "machine learning engineer",
              "ml engineer",
              "mle",
              "machine learning software engineer",
              "machine learning developer",
              "ml software engineer",
              "ai ml engineer",
              "deep learning engineer",
              "nlp engineer",
              "natural language processing engineer",
              "speech engineer",
              "speech recognition engineer",
              "recommendation systems engineer",
              "ranking engineer",
              "search relevance engineer",
              "personalization engineer",
            ],
            match: [
              {
                all: [/\b(machine learning|ml|deep learning|nlp|natural language)\b/, ENG],
                none: [
                  /\b(ops|platform|infrastructure|infra|manager|research|scientist|product|architect)\b/,
                ],
              },
            ],
          },
          {
            id: "ai-engineer",
            name: "AI Engineer",
            soc: ["15-1252.00", "15-2051.00"],
            note: "The LLM-application role: prompting, retrieval, tools and evals on top of hosted models. Kept apart from ML Engineer because in 2026 the two share almost no day-to-day stack, which is precisely the distinction O*NET cannot make.",
            specificity: 46,
            aliases: [
              "ai engineer",
              "artificial intelligence engineer",
              "ai software engineer",
              "ai developer",
              "ai specialist",
              "artificial intelligence specialist",
              "applied ai engineer",
              "llm engineer",
              "genai engineer",
              "gen ai engineer",
              "generative ai engineer",
              "generative ai developer",
              "prompt engineer",
              "ai agent engineer",
              "agent engineer",
              "rag engineer",
              "conversational ai engineer",
              "ai application engineer",
            ],
            match: [
              {
                all: [/\b(ai|artificial intelligence|llm|genai|gen ai|generative ai)\b/, ENG],
                none: [
                  /\b(security|ops|platform|infrastructure|infra|manager|product|research|scientist|hardware|chip|silicon|architect)\b/,
                ],
              },
              { all: [/\bprompt engineer\b/] },
            ],
          },
          {
            id: "mlops-engineer",
            name: "MLOps Engineer",
            soc: ["15-1299.08", "15-2051.00"],
            note: "Training infrastructure and model serving. Above both ML Engineer and Platform Engineer in specificity, so 'ML Platform Engineer' lands here rather than being pulled apart by whichever half matched.",
            specificity: 50,
            aliases: [
              "mlops engineer",
              "ml ops engineer",
              "machine learning operations engineer",
              "ml platform engineer",
              "machine learning platform engineer",
              "ml infrastructure engineer",
              "machine learning infrastructure engineer",
              "ai platform engineer",
              "ai infrastructure engineer",
              "llmops engineer",
              "model deployment engineer",
              "ml systems engineer",
              "gpu infrastructure engineer",
            ],
            match: [
              { all: [/\b(mlops|ml ops|llmops|aiops)\b/] },
              {
                all: [
                  /\b(ml|machine learning|ai|model|gpu)\b/,
                  /\b(ops|platform|infrastructure|infra|deployment|serving)\b/,
                  ENG,
                ],
              },
            ],
          },
          {
            id: "computer-vision-engineer",
            name: "Computer Vision Engineer",
            soc: ["17-2061.00", "15-2051.00"],
            note: "Vision and perception. `cv` is only ever matched as the full phrase 'cv engineer', because on its own it means curriculum vitae.",
            specificity: 50,
            aliases: [
              "computer vision engineer",
              "cv engineer",
              "computer vision scientist",
              "computer vision researcher",
              "image processing engineer",
              "vision engineer",
              "perception engineer",
              "3d vision engineer",
            ],
            match: [{ all: [/\b(computer vision|image processing|perception)\b/, TECH_ROLE] }],
          },
        ],
      },
      {
        id: "research",
        name: "Research",
        leaves: [
          {
            id: "ml-research-scientist",
            name: "Machine Learning Research Scientist",
            soc: ["15-1221.00", "15-2051.00"],
            note: "Publishes. Bare 'Research Scientist' is deliberately left unmapped: O*NET files it under 15-1221.00, 15-2051.00 and 19-1029.01 at once, so guessing between computer science, data science and biology is exactly the wrong-answer-worse-than-none case.",
            specificity: 54,
            aliases: [
              "machine learning research scientist",
              "ml research scientist",
              "machine learning scientist",
              "ml scientist",
              "machine learning researcher",
              "ai research scientist",
              "ai researcher",
              "artificial intelligence researcher",
              "deep learning researcher",
              "computer scientist",
              "computer and information research scientist",
              "research scientist machine learning",
            ],
            match: [
              {
                all: [/\b(machine learning|ml|deep learning|ai|artificial intelligence)\b/, SCIENTIST],
                none: [/\b(engineer|manager|director|product|data scientist|applied)\b/],
              },
              { all: [/\bcomputer scientist\b/] },
            ],
          },
          {
            id: "applied-scientist",
            name: "Applied Scientist",
            soc: ["15-2051.00", "15-1221.00"],
            note: "The Amazon-and-friends title: research training, product deliverables. Kept separate from both Data Scientist and ML Research Scientist because candidates and hiring loops treat it as its own thing.",
            specificity: 52,
            aliases: [
              "applied scientist",
              "applied research scientist",
              "applied machine learning scientist",
              "applied science manager",
              "applied ai scientist",
            ],
            match: [{ all: [/\bapplied (research |machine learning |ai )?scientist\b/] }],
          },
          {
            id: "research-engineer",
            name: "Research Engineer",
            soc: ["15-1221.00", "17-2199.08"],
            note: "Builds the research, does not necessarily author it. The frontier-lab title. Excludes market, user and clinical research, which are different professions entirely.",
            specificity: 48,
            aliases: [
              "research engineer",
              "ai research engineer",
              "machine learning research engineer",
              "research software engineer",
              "research programmer",
              "scientific programmer",
              "research developer",
            ],
            match: [
              {
                all: [/\bresearch\b/, ENG],
                none: [/\b(manager|director|market|user|ux|clinical|operations|analyst)\b/],
              },
            ],
          },
          {
            id: "bioinformatics-scientist",
            name: "Bioinformatics Scientist",
            soc: ["19-1029.01", "15-2099.01"],
            note: "The one health-adjacent leaf kept in scope, because computational biology hires CS and stats candidates directly. Everything else in O*NET's health informatics branch is excluded; see EXCLUDED_SOC.",
            specificity: 54,
            aliases: [
              "bioinformatics scientist",
              "bioinformatician",
              "bioinformaticist",
              "bioinformatics engineer",
              "bioinformatics analyst",
              "bioinformatics developer",
              "bioinformatics specialist",
              "bioinformatics technician",
              "computational biologist",
              "computational biology scientist",
              "genomics data scientist",
            ],
            match: [{ all: [/\b(bioinformatic|computational biolog|genomic)/, TECH_ROLE] }],
          },
        ],
      },
    ],
  },

  // =========================================================================
  {
    id: "infrastructure-and-operations",
    name: "Infrastructure and Operations",
    groups: [
      {
        id: "platform-and-reliability",
        name: "Platform and Reliability",
        leaves: [
          {
            id: "devops-engineer",
            name: "DevOps Engineer",
            soc: ["15-1252.00", "15-1299.08"],
            note: "CI/CD and delivery automation. Build and Release Engineer fold in. Set to the same specificity as SRE on purpose: a posting titled 'DevOps / SRE' is genuinely both, and the tie rule sends it to the alias path rather than to a coin flip.",
            specificity: 45,
            aliases: [
              // Bare "DevOps" is a real posting title, and it also rescues
              // "DevOps / Cloud Engineer", where the two disciplines tie on
              // specificity and the pattern layer refuses to guess.
              "devops",
              "dev ops",
              "devops engineer",
              "dev ops engineer",
              "development operations engineer",
              "devops developer",
              "devops architect",
              "devops specialist",
              "ci cd engineer",
              "cicd engineer",
              "build engineer",
              "release engineer",
              "build and release engineer",
              "gitops engineer",
              "platform devops engineer",
            ],
            match: [
              { all: [/\b(devops|dev ops|gitops|ci cd|cicd)\b/] },
              {
                all: [/\b(build|release)\b/, ENG],
                none: [/\b(hardware|product|manager|train)\b/],
              },
            ],
          },
          {
            id: "site-reliability-engineer",
            name: "Site Reliability Engineer",
            soc: ["15-1252.00", "15-1299.08"],
            note: "The curated alias `sre` deliberately overrides O*NET, whose 15-1252.00 list glosses SRE as 'Software Requirements Engineer'. Production Engineer folds in (Meta's name for the same job); manufacturing production roles are excluded.",
            specificity: 45,
            aliases: [
              "site reliability engineer",
              "sre",
              "site reliability engineering",
              "reliability engineer",
              "systems reliability engineer",
              "production engineer",
              "observability engineer",
              "sre devops engineer",
            ],
            match: [
              { all: [/\b(site reliability|sre)\b/] },
              {
                all: [/\b(reliability|observability|production)\b/, ENG],
                none: [/\b(database|hardware|manufacturing|quality|product|manager|software requirements)\b/],
              },
            ],
          },
          {
            id: "platform-engineer",
            name: "Platform Engineer",
            soc: ["15-1299.08"],
            note: "Internal developer platform work. Developer Experience and Developer Productivity fold in. Data and ML platform titles are excluded so they land on Data Engineer and MLOps.",
            specificity: 43,
            aliases: [
              "platform engineer",
              "platform software engineer",
              "internal platform engineer",
              "developer platform engineer",
              "developer experience engineer",
              "devex engineer",
              "developer productivity engineer",
              "developer tools engineer",
              "kubernetes engineer",
              "container platform engineer",
            ],
            match: [
              {
                all: [/\bplatform\b/, ENG],
                none: [
                  /\b(data|ml|machine learning|ai|architect|manager|security|product|devops)\b/,
                ],
              },
              { all: [/\b(developer experience|devex|developer productivity|developer tools?)\b/] },
            ],
          },
          {
            id: "infrastructure-engineer",
            name: "Infrastructure Engineer",
            soc: ["15-1299.08", "15-1252.00"],
            note: "Runs the fleet: hosts, networks-as-code, virtualization, storage. The counterpart to Systems Engineer, which builds the layer being run.",
            specificity: 43,
            aliases: [
              "infrastructure engineer",
              "infra engineer",
              "systems infrastructure engineer",
              "network and infrastructure engineer",
              "virtualization engineer",
              "server engineer",
              "linux engineer",
              "linux systems engineer",
              "unix engineer",
              "storage infrastructure engineer",
              "datacenter engineer",
              "data center engineer",
              "capacity engineer",
            ],
            match: [
              {
                all: [/\b(infrastructure|infra)\b/, ENG],
                none: [
                  /\b(data|ml|machine learning|ai|security|architect|manager|project|cloud|network)\b/,
                ],
              },
              { all: [/\b(linux|unix|virtualization|datacenter|data center)\b/, ENG] },
            ],
          },
          {
            id: "cloud-engineer",
            name: "Cloud Engineer",
            soc: ["15-1299.08", "15-1299.05"],
            note: "Cloud-provider-shaped infrastructure work. Cloud Architect is not here; it folds into Solutions Architect, because those postings are overwhelmingly customer-facing.",
            specificity: 45,
            aliases: [
              "cloud engineer",
              "cloud infrastructure engineer",
              "cloud platform engineer",
              "cloud software engineer",
              "cloud systems engineer",
              "cloud operations engineer",
              "cloud devops engineer",
              "aws engineer",
              "azure engineer",
              "gcp engineer",
            ],
            match: [
              {
                all: [/\b(cloud|aws|azure|gcp)\b/, ENG],
                none: [/\b(architect|security|manager|data|ml|machine learning)\b/],
              },
            ],
          },
        ],
      },
      {
        id: "network-and-systems",
        name: "Network and Systems Administration",
        leaves: [
          {
            id: "network-engineer",
            name: "Network Engineer",
            soc: ["15-1241.00", "15-1241.01", "15-1299.08"],
            note: "Network Architect and Telecommunications Engineer fold in. O*NET splits 15-1241.00 Computer Network Architects from 15-1241.01 Telecommunications Engineering Specialists, but the candidate pool is one pool. Network Security Engineer is excluded; it belongs to Security Engineer.",
            specificity: 44,
            aliases: [
              "network engineer",
              "network architect",
              "computer network architect",
              "network operations engineer",
              "noc engineer",
              "network analyst",
              "network specialist",
              "network control analyst",
              "telecommunications engineer",
              "telecom engineer",
              "telecommunications engineering specialist",
              "voip engineer",
              "wireless network engineer",
              "network automation engineer",
            ],
            match: [
              {
                all: [
                  /\b(network|networking|telecom|telecommunications|voip|wan|lan)\b/,
                  /\b(engineer|architect|analyst|specialist)\b/,
                ],
                none: [/\b(security|social|neural|marketing|provider|support specialist)\b/],
              },
            ],
          },
          {
            id: "systems-administrator",
            name: "Systems Administrator",
            soc: ["15-1244.00", "15-1299.01"],
            note: "O*NET's 15-1244.00 is literally 'Network and Computer Systems Administrators', so Network Administrator lives here rather than under Network Engineer. Web Administrator (15-1299.01) folds in too.",
            specificity: 42,
            aliases: [
              "systems administrator",
              "system administrator",
              "systems admin",
              "system admin",
              "sysadmin",
              "network administrator",
              "network systems administrator",
              "networking administrator",
              "linux administrator",
              "linux system administrator",
              "windows administrator",
              "server administrator",
              "web administrator",
              "website administrator",
              "web site administrator",
              "internet systems administrator",
              "active directory administrator",
              "it administrator",
              "it systems administrator",
              "lan administrator",
              "wan administrator",
              "virtualization administrator",
              "enterprise systems administrator",
            ],
            match: [
              {
                all: [
                  /\b(system|systems|network|networking|server|linux|windows|unix|web|website|it|enterprise|mainframe)\b/,
                  /\b(administrator|admin)\b/,
                ],
                none: [/\b(database|security|salesforce|workday|hr|contract|sharepoint)\b/],
              },
              { all: [/\bsysadmin\b/] },
            ],
          },
        ],
      },
      {
        id: "it-operations",
        name: "IT Support and Analysis",
        leaves: [
          {
            id: "it-support-specialist",
            name: "IT Support Specialist",
            soc: ["15-1232.00", "15-1231.00"],
            note: "Covers both of O*NET's support occupations, user support (15-1232.00) and network support (15-1231.00). One leaf because the postings are interchangeable at the level a job seeker cares about.",
            specificity: 40,
            aliases: [
              "it support specialist",
              "it support technician",
              "it technician",
              "help desk technician",
              "helpdesk technician",
              "help desk analyst",
              "help desk specialist",
              "service desk analyst",
              "technical support specialist",
              "technical support engineer",
              "technical support analyst",
              "desktop support technician",
              "desktop support specialist",
              "computer user support specialist",
              "computer network support specialist",
              "network support specialist",
              "end user support specialist",
              "application support engineer",
              "application support analyst",
              "production support analyst",
              "support engineer",
            ],
            match: [
              { all: [/\b(help ?desk|service desk|desktop support|deskside)\b/] },
              {
                all: [
                  /\b(technical|it|user|end user|application|production|customer|computer|network|systems?|tier [123])\b/,
                  /\bsupport\b/,
                  /\b(specialist|technician|analyst|engineer|representative|agent|consultant)\b/,
                ],
                none: [/\b(manager|director|sales|security)\b/],
              },
            ],
          },
          {
            id: "it-systems-analyst",
            name: "IT Systems Analyst",
            soc: ["15-1211.00"],
            note: "O*NET's 15-1211.00 Computer Systems Analysts. Bare 'Business Analyst' is deliberately not an alias: without a technical qualifier plenty of those are finance or operations jobs, and this taxonomy would rather return nothing.",
            specificity: 40,
            aliases: [
              "it systems analyst",
              "computer systems analyst",
              "systems analyst",
              "system analyst",
              "information systems analyst",
              "business systems analyst",
              "it business analyst",
              "it business systems analyst",
              "technical business analyst",
              "it analyst",
              "information technology analyst",
              "technology analyst",
              "technical analyst",
              "applications analyst",
              "application systems analyst",
              "programmer analyst",
              "functional analyst",
              "erp analyst",
              "erp consultant",
              "erp specialist",
              "systems analysis specialist",
              "configuration analyst",
            ],
            match: [
              {
                all: [
                  /\b(it|information technology|information systems|computer|business|technical|technology|applications?|erp)\b/,
                  /\bsystems? analyst\b/,
                ],
              },
              { all: [/\b(it|information technology)\b/, /\b(business )?analyst\b/], none: [/\b(security|data|network|compliance|audit|risk|support)\b/] },
              { all: [/\bprogrammer analyst\b/] },
            ],
          },
        ],
      },
    ],
  },

  // =========================================================================
  {
    id: "security",
    name: "Security",
    groups: [
      {
        id: "security-engineering",
        name: "Security Engineering",
        leaves: [
          {
            id: "security-engineer",
            name: "Security Engineer",
            soc: ["15-1299.05", "15-1212.00"],
            note: "Cloud Security, Network Security, Detection and IAM Engineer all fold in. They differ by surface area rather than by craft, and somebody filtering for 'Security Engineer' wants all of them. Application Security is the one split kept, because appsec hires from the software side.",
            specificity: 46,
            aliases: [
              "security engineer",
              "information security engineer",
              "information systems security engineer",
              "is security engineer",
              "cyber security engineer",
              "cybersecurity engineer",
              "infosec engineer",
              "network security engineer",
              "cloud security engineer",
              "infrastructure security engineer",
              "security automation engineer",
              "security operations engineer",
              "detection engineer",
              "detection and response engineer",
              "devsecops",
              "devsecops engineer",
              "iam engineer",
              "identity and access management engineer",
              "identity engineer",
              "cryptography engineer",
            ],
            match: [
              {
                all: [/\b(security|infosec|cyber|cybersecurity|devsecops|cryptography)\b/, ENG],
                none: [
                  /\b(architect|analyst|product|manager|guard|appsec|application|software security|penetration|pen)\b/,
                ],
              },
            ],
          },
          {
            id: "application-security-engineer",
            name: "Application Security Engineer",
            soc: ["15-1299.05", "15-1252.00"],
            note: "Above Security Engineer in specificity because appsec is the one security specialism whose candidate pool is software engineers rather than security operators.",
            specificity: 50,
            aliases: [
              "application security engineer",
              "appsec engineer",
              "application security specialist",
              "product security engineer",
              "software security engineer",
              "secure software assessor",
              "code security engineer",
              "security software developer",
            ],
            match: [
              {
                all: [/\b(applications?|appsec|product|software|code)\b/, /\b(security|appsec)\b/, ENG],
              },
              { all: [/\bappsec\b/] },
              { all: [/\bsecure software\b/] },
            ],
          },
          {
            id: "penetration-tester",
            name: "Penetration Tester",
            soc: ["15-1299.04"],
            note: "Its own O*NET occupation since 15-1299.04. Red team, offensive security and exploit development fold in; so does vulnerability research, which is the same skill pointed at a product instead of a client.",
            specificity: 52,
            aliases: [
              "penetration tester",
              "pen tester",
              "pentester",
              "penetration testing engineer",
              "ethical hacker",
              "certified ethical hacker",
              "white hat hacker",
              "red team engineer",
              "red team operator",
              "red teamer",
              "offensive security engineer",
              "exploit developer",
              "vulnerability researcher",
              "vulnerability assessment analyst",
              "vulnerability assessor",
              "security researcher",
              "bug bounty hunter",
            ],
            match: [
              {
                all: [
                  /\b(penetration test|pen test|pentest|ethical hack|white hat|red team|offensive security|exploit develop|bug bounty)\b/,
                ],
              },
              { all: [/\bvulnerability\b/, /\b(researcher|analyst|assessor|assessment)\b/] },
            ],
          },
          {
            id: "security-architect",
            name: "Security Architect",
            soc: ["15-1299.05", "15-1241.00"],
            note: "Highest specificity in the security group so the architect qualifier always wins over the engineer and analyst rules.",
            specificity: 54,
            aliases: [
              "security architect",
              "information security architect",
              "cyber security architect",
              "cybersecurity architect",
              "network security architect",
              "cloud security architect",
              "enterprise security architect",
              "identity architect",
            ],
            match: [{ all: [/\b(security|infosec|cyber|cybersecurity)\b/, ARCHITECT] }],
          },
        ],
      },
      {
        id: "security-operations",
        name: "Security Operations and Assurance",
        leaves: [
          {
            id: "security-analyst",
            name: "Information Security Analyst",
            soc: ["15-1212.00"],
            note: "SOC Analyst folds in: at the rungs where those postings exist it is the same job description, and separating them would split a thin alias set. Compliance and audit titles are excluded; they are the next leaf.",
            specificity: 44,
            aliases: [
              "security analyst",
              "information security analyst",
              "information systems security analyst",
              "it security analyst",
              "information technology security analyst",
              "cyber security analyst",
              "cybersecurity analyst",
              "network security analyst",
              "soc analyst",
              "security operations center analyst",
              "siem analyst",
              "threat intelligence analyst",
              "threat analyst",
              "incident response analyst",
              "cyber defense analyst",
              "security specialist",
              "information security specialist",
              "information security officer",
              "information systems security officer",
              "isso",
            ],
            match: [
              {
                all: [
                  /\b(security|infosec|cyber|cybersecurity|soc|threat|incident response|siem)\b/,
                  /\b(analyst|specialist|officer|operator)\b/,
                ],
                none: [
                  /\b(guard|compliance|grc|governance|audit|auditor|risk|privacy|architect|business|sales)\b/,
                ],
              },
            ],
          },
          {
            id: "digital-forensics-analyst",
            name: "Digital Forensics Analyst",
            soc: ["15-1299.06"],
            note: "Its own O*NET occupation. Malware analysis and reverse engineering fold in; they share the tooling and the postings often share the title.",
            specificity: 56,
            aliases: [
              "digital forensics analyst",
              "digital forensic analyst",
              "computer forensics analyst",
              "computer forensic examiner",
              "digital forensic examiner",
              "forensic analyst",
              "incident response and forensics analyst",
              "malware analyst",
              "malware reverse engineer",
              "reverse engineer",
            ],
            match: [
              { all: [/\b(forensic|forensics|malware)\b/, TECH_ROLE], none: [/\b(accounting|audit)\b/] },
              { all: [/\breverse engineer(ing)?\b/] },
            ],
          },
          {
            id: "security-compliance-analyst",
            name: "Security Compliance Analyst",
            soc: ["15-1212.00", "15-1211.00"],
            note: "GRC, IT audit and privacy. Kept apart from Information Security Analyst because the work is evidence and control mapping rather than detection, and the two rarely swap candidates.",
            specificity: 52,
            aliases: [
              "security compliance analyst",
              "grc analyst",
              "governance risk and compliance analyst",
              "it compliance analyst",
              "it auditor",
              "information systems auditor",
              "is auditor",
              "information security auditor",
              "security risk analyst",
              "cyber risk analyst",
              "security governance analyst",
              "privacy analyst",
              "information assurance analyst",
              "security control assessor",
              "soc 2 analyst",
            ],
            match: [
              {
                all: [
                  /\b(security|cyber|cybersecurity|it|information|privacy|information assurance)\b/,
                  /\b(compliance|grc|governance|audit|auditor|risk)\b/,
                ],
                none: [/\b(financial|finance|credit|market|clinical)\b/],
              },
            ],
          },
        ],
      },
    ],
  },

  // =========================================================================
  {
    id: "hardware-and-robotics",
    name: "Hardware and Robotics",
    groups: [
      {
        id: "hardware-engineering",
        name: "Hardware and Silicon",
        leaves: [
          {
            id: "hardware-engineer",
            name: "Hardware Engineer",
            soc: ["17-2061.00"],
            note: "Board, analog and system-level hardware. 'Embedded Hardware Engineer' resolves here rather than to Embedded Software Engineer.",
            specificity: 44,
            aliases: [
              "hardware engineer",
              "computer hardware engineer",
              "hardware design engineer",
              "hardware development engineer",
              "hardware systems engineer",
              "hardware architect",
              "hardware test engineer",
              "hardware validation engineer",
              "electrical hardware engineer",
              "embedded hardware engineer",
              "digital design engineer",
              "digital hardware engineer",
              "analog design engineer",
              "analog ic design engineer",
              "board design engineer",
              "pcb design engineer",
              "signal integrity engineer",
            ],
            match: [
              {
                all: [/\b(hardware|pcb|analog|board|signal integrity)\b/, /\b(engineer|architect|design|designer)\b/],
                none: [/\b(software|firmware|embedded software)\b/],
              },
            ],
          },
          {
            id: "asic-fpga-engineer",
            name: "ASIC and FPGA Engineer",
            soc: ["17-2061.00"],
            note: "RTL, verification and physical design. Design Verification folds in: DV is a distinct career track but shares the whole language and tool stack with RTL design, and postings use the titles interchangeably. `soc` here means system-on-chip, so security-operations titles are excluded explicitly.",
            specificity: 56,
            aliases: [
              "asic design engineer",
              "asic engineer",
              "asic design verification engineer",
              "fpga engineer",
              "fpga design engineer",
              "rtl design engineer",
              "rtl engineer",
              "design verification engineer",
              "dv engineer",
              "digital verification engineer",
              "physical design engineer",
              "soc design engineer",
              "chip design engineer",
              "silicon design engineer",
              "vlsi engineer",
              "vlsi design engineer",
            ],
            match: [
              {
                all: [
                  /\b(asic|fpga|rtl|vlsi|soc|chip|silicon|physical design)\b/,
                  /\b(engineer|design|designer|verification)\b/,
                ],
                none: [/\b(soc analyst|security|operations center)\b/],
              },
              { all: [/\bdesign verification\b/] },
            ],
          },
        ],
      },
      {
        id: "robotics-and-autonomy",
        name: "Robotics and Autonomy",
        leaves: [
          {
            id: "robotics-engineer",
            name: "Robotics Engineer",
            soc: ["17-2199.08"],
            note: "Mechatronics and motion planning fold in. RPA is explicitly excluded: 'Robotic Process Automation' is business-process software and matching it here would be a confident wrong answer, which is the failure mode this taxonomy cares most about.",
            specificity: 50,
            aliases: [
              "robotics engineer",
              "robotics software engineer",
              "robotic systems engineer",
              "robotics systems engineer",
              "robotics application engineer",
              "robotics controls engineer",
              "robotics researcher",
              "robotics specialist",
              "mechatronics engineer",
              "motion planning engineer",
              "ros engineer",
            ],
            match: [
              {
                all: [/\b(robotic|robotics|mechatronic|mechatronics)\b/, TECH_ROLE],
                none: [/\b(process automation|rpa|operator|technician)\b/],
              },
              { all: [/\bmotion planning\b/, TECH_ROLE] },
            ],
          },
          {
            id: "autonomy-engineer",
            name: "Autonomy Engineer",
            soc: ["17-2199.08", "15-1252.00"],
            note: "Self-driving, drones and the perception-to-planning stack. Above Robotics Engineer in specificity because an autonomy title is the more informative of the two.",
            specificity: 54,
            aliases: [
              "autonomy engineer",
              "autonomous systems engineer",
              "autonomous vehicle engineer",
              "autonomous vehicle design engineer",
              "self driving engineer",
              "adas engineer",
              "sensor fusion engineer",
              "slam engineer",
              "localization engineer",
              "path planning engineer",
              "drone software engineer",
              "drone software development engineer",
              "uav engineer",
            ],
            match: [
              {
                all: [/\b(autonomy|autonomous|self driving|adas|sensor fusion|slam|uav|drone)\b/, TECH_ROLE],
                none: [/\b(operator|pilot|technician)\b/],
              },
            ],
          },
        ],
      },
    ],
  },

  // =========================================================================
  {
    id: "product-and-program",
    name: "Product and Program",
    groups: [
      {
        id: "product-management",
        name: "Product Management",
        leaves: [
          {
            id: "product-manager",
            name: "Product Manager",
            soc: ["11-2021.00"],
            note: "O*NET has no Product Manager occupation. Its alternate-title list files 'Product Manager', 'Digital Product Manager' and 'Technical Product Manager' under 11-2021.00 Marketing Managers, so that is the honest crosswalk even though it reads oddly. Product Owner folds in, and so do domain flavours like AI PM and Data PM: the craft is the same, only the subject changes, which is not what earns a leaf here.",
            specificity: 74,
            aliases: [
              "product manager",
              "product owner",
              "product management",
              "digital product manager",
              "group product manager",
              "growth product manager",
              "ai product manager",
              "data product manager",
              "platform product manager",
              "product lead",
            ],
            match: [
              {
                all: [/\bproduct\b/, /\b(manager|owner|management)\b/],
                none: [
                  /\b(marketing|technical|support|solutions?|line|sustaining|assistant|associate director)\b/,
                ],
              },
            ],
          },
          {
            id: "technical-product-manager",
            name: "Technical Product Manager",
            soc: ["11-2021.00", "15-1299.09"],
            note: "Split from Product Manager because the screening bar is different: these postings expect the candidate to read code. Bare 'TPM' is not an alias here, since in practice it almost always means Technical Program Manager.",
            specificity: 78,
            aliases: [
              "technical product manager",
              "technical product owner",
              "api product manager",
              "developer product manager",
              "infrastructure product manager",
              "technical program product manager",
            ],
            match: [
              { all: [/\btechnical product (manager|owner)\b/] },
              { all: [/\b(api|developer|infrastructure) product manager\b/] },
            ],
          },
        ],
      },
      {
        id: "program-management",
        name: "Program and Project Management",
        leaves: [
          {
            id: "technical-program-manager",
            name: "Technical Program Manager",
            soc: ["15-1299.09", "13-1082.00"],
            note: "The big-tech TPM role. `tpm` resolves here rather than to Technical Product Manager, which is the way the acronym is actually used.",
            specificity: 78,
            aliases: [
              "technical program manager",
              "technical program management",
              "tpm",
              "engineering program manager",
              "software program manager",
              "infrastructure program manager",
              "data program manager",
            ],
            match: [
              {
                all: [
                  /\b(technical|engineering|software|infrastructure|platform|data|security) program manager\b/,
                ],
              },
              { all: [/\btpm\b/] },
            ],
          },
          {
            id: "it-project-manager",
            name: "IT Project Manager",
            soc: ["15-1299.09", "13-1082.00"],
            note: "Bare 'Project Manager' is deliberately unmapped: without a technical qualifier it is as likely to be construction, marketing or events. Release Manager and Delivery Manager fold in.",
            specificity: 74,
            aliases: [
              "it project manager",
              "information technology project manager",
              "technical project manager",
              "technology project manager",
              "software project manager",
              "software development project manager",
              "engineering project manager",
              "digital project manager",
              "infrastructure project manager",
              "web project manager",
              "it program manager",
              "delivery manager",
              "release manager",
            ],
            match: [
              {
                all: [
                  /\b(it|information technology|technical|technology|software|engineering|digital|infrastructure|data|cybersecurity|security|network|web) project manager\b/,
                ],
              },
              { all: [/\b(it|information technology) program manager\b/] },
            ],
          },
          {
            id: "scrum-master",
            name: "Scrum Master",
            soc: ["15-1299.09"],
            note: "Agile Coach and Release Train Engineer fold in. Kept separate from project management because the postings screen for certifications rather than delivery ownership.",
            specificity: 76,
            aliases: [
              "scrum master",
              "agile coach",
              "agile delivery manager",
              "agile project manager",
              "release train engineer",
              "cybersecurity scrum master",
            ],
            match: [{ all: [/\b(scrum master|agile coach|release train engineer)\b/] }],
          },
        ],
      },
      {
        id: "engineering-leadership",
        name: "Engineering Leadership",
        leaves: [
          {
            id: "engineering-manager",
            name: "Engineering Manager",
            soc: ["11-3021.00", "11-9041.00"],
            note: "Line management of engineers. Discipline-flavoured variants (Data Engineering Manager, QA Manager, ML Manager) fold in, and the high specificity means 'Engineering Manager, Machine Learning' resolves here rather than to ML Engineer, because it is a management job before it is an ML job. 'Information Security Manager' is excluded because it is a security-operations role, not an engineering one.",
            specificity: 84,
            aliases: [
              "engineering manager",
              "software engineering manager",
              "software development manager",
              "development manager",
              "dev manager",
              "software manager",
              "engineering team lead",
              "data engineering manager",
              "data science manager",
              "analytics manager",
              "machine learning manager",
              "platform engineering manager",
              "infrastructure manager",
              "systems engineering manager",
              "security engineering manager",
              "qa manager",
              "quality assurance manager",
              "technical manager",
              "programming manager",
            ],
            match: [
              {
                all: [
                  /\b(engineering|software|development|dev|programming|data|analytics|platform|infrastructure|qa|quality|machine learning|ml|technical|systems)\b/,
                  MANAGER,
                ],
                none: [
                  /\b(product|program|programme|project|account|marketing|brand|category|channel|portfolio|release|case|office|network|provider|configuration|knowledge|records|data center|information security|computer security|it manager)\b/,
                ],
              },
            ],
          },
          {
            id: "engineering-executive",
            name: "Engineering Executive",
            soc: ["11-3021.00"],
            note: "Director, VP and C-level grouped as one leaf. This is the one place where a rung is allowed to define a boundary, because running an organisation is a different job from running a team; splitting Director from VP would be encoding a rung, which the seniority field already carries.",
            specificity: 88,
            aliases: [
              "cto",
              "chief technology officer",
              "chief technical officer",
              "chief information officer",
              "cio",
              "chief data officer",
              "vp of engineering",
              "vp engineering",
              "vice president of engineering",
              "svp engineering",
              "vp of technology",
              "director of engineering",
              "engineering director",
              "software engineering director",
              "director of software engineering",
              "application development director",
              "software development director",
              "head of engineering",
              "head of technology",
              "head of platform",
              "head of data",
              "data engineering director",
            ],
            match: [
              {
                all: [
                  /\b(cto|cio|chief technology officer|chief technical officer|chief information officer|chief data officer)\b/,
                ],
              },
              {
                all: [
                  /\b(vp|vice president|svp|evp|director|head)\b/,
                  /\b(engineering|technology|software|platform|data|infrastructure|architecture)\b/,
                ],
                none: [/\b(product|marketing|sales|program|project|hr|finance|operations)\b/],
              },
            ],
          },
          {
            id: "it-manager",
            name: "IT Manager",
            soc: ["11-3021.00"],
            note: "The operations side of 11-3021.00: service desk, endpoints, internal systems. Kept apart from Engineering Manager because the two almost never swap candidates.",
            specificity: 82,
            aliases: [
              "it manager",
              "information technology manager",
              "information systems manager",
              "is manager",
              "mis manager",
              "computer and information systems manager",
              "it director",
              "information technology director",
              "information systems director",
              "mis director",
              "it operations manager",
              "help desk manager",
              "service desk manager",
              "data center manager",
              "technical services manager",
              "it infrastructure director",
            ],
            match: [
              {
                all: [
                  /\b(it|information technology|information systems|mis|help ?desk|service desk|data ?center)\b/,
                  /\b(manager|director|mgr)\b/,
                ],
                none: [/\b(project|program|product|security)\b/],
              },
            ],
          },
        ],
      },
    ],
  },

  // =========================================================================
  {
    id: "design-and-content",
    name: "Design and Technical Content",
    groups: [
      {
        id: "product-design",
        name: "Product Design",
        leaves: [
          {
            id: "product-designer",
            name: "Product Designer",
            soc: ["15-1255.00", "27-1021.00"],
            note: "Interaction and service designers fold in. The industrial-design senses of 27-1021.00 (toys, furniture, packaging) are excluded, which is the whole reason that SOC needs a rule rather than a bulk import.",
            specificity: 60,
            aliases: [
              "product designer",
              "digital product designer",
              "product design",
              "ui product designer",
              "ux product designer",
              "interaction designer",
              "service designer",
            ],
            match: [
              {
                all: [/\bproduct design(er)?\b/],
                none: [
                  /\b(engineer|industrial|mechanical|packaging|toy|apparel|textile|furniture|manager)\b/,
                ],
              },
              { all: [/\b(interaction|service) designer\b/] },
            ],
          },
          {
            id: "ux-designer",
            name: "UX Designer",
            soc: ["15-1255.00"],
            note: "UI Designer folds in. The market treats 'UI Designer', 'UX Designer' and 'UI/UX Designer' as one posting with three names. 'UX Engineer' is excluded and goes to Frontend Engineer, because that posting wants code.",
            specificity: 58,
            aliases: [
              "ux designer",
              "ui designer",
              "ui ux designer",
              "ux ui designer",
              "user experience designer",
              "user interface designer",
              "digital interface designer",
              "web and digital interface designer",
              "interface designer",
            ],
            match: [
              {
                all: [/\b(ux|ui|user experience|user interface)\b/, DESIGNER],
                none: [/\b(engineer|developer|research|researcher|product design)\b/],
              },
            ],
          },
          {
            id: "ux-researcher",
            name: "UX Researcher",
            soc: ["15-1255.00"],
            note: "Above UX Designer in specificity so the research qualifier wins. Usability Engineer folds in despite the name; the work is research.",
            specificity: 62,
            aliases: [
              "ux researcher",
              "user experience researcher",
              "user researcher",
              "ux research",
              "ux research scientist",
              "usability researcher",
              "usability engineer",
              "design researcher",
            ],
            match: [
              {
                all: [/\b(ux|user experience|user|usability|design)\b/, /\b(research|researcher)\b/],
                none: [/\b(market|clinical|operations)\b/],
              },
              { all: [/\busability engineer\b/] },
            ],
          },
        ],
      },
      {
        id: "interface-and-game-design",
        name: "Interface and Game Design",
        leaves: [
          {
            id: "web-designer",
            name: "Web Designer",
            soc: ["15-1255.00", "15-1254.00"],
            note: "Site-level visual design. Out-of-scope neighbours, Graphic Designer and Visual Designer, are deliberately left unmapped: 27-1024.00 is a print and brand occupation, not a tech one, and folding them in here would be a confident wrong answer.",
            specificity: 50,
            aliases: [
              "web designer",
              "website designer",
              "web design specialist",
              "web ui designer",
              "digital designer",
            ],
            match: [
              {
                all: [/\bweb(site)?\b/, DESIGNER],
                none: [/\b(engineer|developer|architect|system|game)\b/],
              },
            ],
          },
          {
            id: "game-designer",
            name: "Game Designer",
            soc: ["15-1255.01"],
            note: "O*NET's 15-1255.01 Video Game Designers. Level and narrative design fold in. Not a programming job, which is why Game Engineer lives in the Software Engineering family instead.",
            specificity: 56,
            aliases: [
              "game designer",
              "video game designer",
              "computer game designer",
              "game design",
              "gameplay designer",
              "level designer",
              "narrative designer",
              "world designer",
            ],
            match: [
              {
                all: [/\b(game|gameplay|level|narrative|world)\b/, DESIGNER],
                none: [/\b(engineer|programmer|developer|manager)\b/],
              },
            ],
          },
        ],
      },
      {
        id: "technical-content",
        name: "Technical Content",
        leaves: [
          {
            id: "technical-writer",
            name: "Technical Writer",
            soc: ["27-3042.00"],
            note: "Scoped to software documentation. The medical, regulatory, grant and proposal writers that share 27-3042.00 are excluded by rule, since a CS candidate searching this taxonomy is not looking for them.",
            specificity: 60,
            aliases: [
              "technical writer",
              "technical writing",
              "technical author",
              "technical editor",
              "technical communicator",
              "technical content writer",
              "documentation specialist",
              "documentation writer",
              "documentation engineer",
              "docs engineer",
              "api documentation writer",
              "information developer",
              "ux writer",
            ],
            match: [
              { all: [/\btechnical (writer|writing|author|editor|communicator)\b/] },
              {
                all: [/\b(documentation|docs)\b/, /\b(writer|specialist|engineer|manager|lead)\b/],
                none: [/\b(medical|clinical|regulatory|grant|proposal|contracts)\b/],
              },
            ],
          },
          {
            id: "developer-advocate",
            name: "Developer Advocate",
            soc: ["27-3042.00", "15-1252.00"],
            note: "DevRel. Kept in this family rather than in Software Engineering because the deliverable is content and community, even though the candidate is usually an engineer.",
            specificity: 64,
            aliases: [
              "developer advocate",
              "developer relations engineer",
              "developer relations manager",
              "devrel engineer",
              "developer evangelist",
              "technical evangelist",
              "developer experience advocate",
              "community engineer",
            ],
            match: [
              {
                all: [
                  /\b(developer|technical|technology)\b/,
                  /\b(advocate|advocacy|evangelist|relations)\b/,
                ],
              },
              { all: [/\bdevrel\b/] },
            ],
          },
        ],
      },
    ],
  },
];

/**
 * CS and Math SOC codes (15-1xxx, 15-2xxx) left out of the crosswalk on
 * purpose, with the reason. The build asserts that covered + excluded accounts
 * for all 38, so a new O*NET release cannot quietly add an occupation that
 * nobody looked at.
 */
export const EXCLUDED_SOC: Readonly<Record<SocCode, string>> = {
  "15-1211.01":
    "Health Informatics Specialists. Its alternate-title list is mostly nursing (Clinical Informatics Nurse, Nurse Informaticist, Utilization Review Nurse), so importing it would put nursing titles into a CS taxonomy. Bioinformatics Scientist covers the computational-biology side that is genuinely in scope.",
  "15-1299.00":
    "Computer Occupations, All Other. A grab bag: Computer Operator, Data Center Technician, Business Consultant, Content Manager. No coherent leaf to hang it on.",
  "15-1299.02":
    "Geographic Information Systems Technologists. Real tech work, but GIS is a vertical this platform does not cover yet. First candidate if that changes.",
  "15-1299.03":
    "Document Management Specialists. Records and imaging operations rather than software.",
  "15-2011.00": "Actuaries. A licensed profession with its own exam track, not a CS/AI role.",
  "15-2021.00": "Mathematicians. Academic; no matching leaf.",
  "15-2031.00":
    "Operations Research Analysts. Adjacent to data science but a distinct discipline, and folding it into Data Analyst would be a wrong answer rather than a coarse one.",
  "15-2041.00":
    "Statisticians. Same reasoning: leaving 'Statistician' unmapped is better than mapping it to Data Scientist.",
  "15-2041.01": "Biostatisticians. As above, plus a clinical-trials context that is out of scope.",
  "15-2051.02":
    "Clinical Data Managers. Clinical-trial data operations, which shares vocabulary with data engineering but not the job.",
  "15-2099.00": "Mathematical Science Occupations, All Other. Residual bucket, nothing to map.",
};

/** Flattened view of every leaf in declaration order. */
export function allLeafSpecs(): Array<{
  leaf: LeafSpec;
  groupId: string;
  familyId: string;
}> {
  const out: Array<{ leaf: LeafSpec; groupId: string; familyId: string }> = [];
  for (const family of TAXONOMY_SPEC) {
    for (const group of family.groups) {
      for (const leaf of group.leaves) {
        out.push({ leaf, groupId: group.id, familyId: family.id });
      }
    }
  }
  return out;
}
