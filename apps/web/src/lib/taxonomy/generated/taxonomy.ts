/**
 * GENERATED FILE. Do not edit.
 * Run `pnpm taxonomy:build` in apps/web after changing spec.ts or the O*NET data.
 *
 * This product uses public information provided by the O*NET Program:
 * O*NET 30.3 Database by the U.S. Department of Labor,
 * Employment and Training Administration (USDOL/ETA). Used under the
 * CC BY 4.0 license. O*NET is a registered trademark of USDOL/ETA.
 * job.os has modified this information and USDOL/ETA has not approved,
 * endorsed or tested these modifications.
 */

import type { Taxonomy } from "../types.ts";

export const TAXONOMY: Taxonomy = {
  "version": "1.0.0",
  "onetVersion": "30.3",
  "generatedAt": "2026-08-12",
  "families": [
    {
      "id": "software-engineering",
      "name": "Software Engineering",
      "groups": [
        "application-engineering",
        "mobile-engineering",
        "systems-engineering",
        "quality-engineering",
        "software-architecture"
      ]
    },
    {
      "id": "data-and-ai",
      "name": "Data and AI",
      "groups": [
        "data-engineering",
        "data-science-and-analytics",
        "ml-and-ai-engineering",
        "research"
      ]
    },
    {
      "id": "infrastructure-and-operations",
      "name": "Infrastructure and Operations",
      "groups": [
        "platform-and-reliability",
        "network-and-systems",
        "it-operations"
      ]
    },
    {
      "id": "security",
      "name": "Security",
      "groups": [
        "security-engineering",
        "security-operations"
      ]
    },
    {
      "id": "hardware-and-robotics",
      "name": "Hardware and Robotics",
      "groups": [
        "hardware-engineering",
        "robotics-and-autonomy"
      ]
    },
    {
      "id": "product-and-program",
      "name": "Product and Program",
      "groups": [
        "product-management",
        "program-management",
        "engineering-leadership"
      ]
    },
    {
      "id": "design-and-content",
      "name": "Design and Technical Content",
      "groups": [
        "product-design",
        "interface-and-game-design",
        "technical-content"
      ]
    }
  ],
  "groups": [
    {
      "id": "application-engineering",
      "name": "Application Engineering",
      "familyId": "software-engineering",
      "leaves": [
        "software-engineer",
        "backend-engineer",
        "frontend-engineer",
        "full-stack-engineer",
        "web-developer",
        "blockchain-engineer"
      ]
    },
    {
      "id": "mobile-engineering",
      "name": "Mobile Engineering",
      "familyId": "software-engineering",
      "leaves": [
        "mobile-engineer",
        "ios-engineer",
        "android-engineer"
      ]
    },
    {
      "id": "systems-engineering",
      "name": "Systems and Embedded Engineering",
      "familyId": "software-engineering",
      "leaves": [
        "systems-engineer",
        "embedded-engineer",
        "game-engineer"
      ]
    },
    {
      "id": "quality-engineering",
      "name": "Quality Engineering",
      "familyId": "software-engineering",
      "leaves": [
        "qa-automation-engineer",
        "qa-analyst"
      ]
    },
    {
      "id": "software-architecture",
      "name": "Software Architecture",
      "familyId": "software-engineering",
      "leaves": [
        "software-architect",
        "solutions-architect"
      ]
    },
    {
      "id": "data-engineering",
      "name": "Data Engineering",
      "familyId": "data-and-ai",
      "leaves": [
        "data-engineer",
        "analytics-engineer",
        "data-architect",
        "database-administrator"
      ]
    },
    {
      "id": "data-science-and-analytics",
      "name": "Data Science and Analytics",
      "familyId": "data-and-ai",
      "leaves": [
        "data-scientist",
        "data-analyst",
        "business-intelligence-analyst"
      ]
    },
    {
      "id": "ml-and-ai-engineering",
      "name": "Machine Learning and AI Engineering",
      "familyId": "data-and-ai",
      "leaves": [
        "ml-engineer",
        "ai-engineer",
        "mlops-engineer",
        "computer-vision-engineer"
      ]
    },
    {
      "id": "research",
      "name": "Research",
      "familyId": "data-and-ai",
      "leaves": [
        "ml-research-scientist",
        "applied-scientist",
        "research-engineer",
        "bioinformatics-scientist"
      ]
    },
    {
      "id": "platform-and-reliability",
      "name": "Platform and Reliability",
      "familyId": "infrastructure-and-operations",
      "leaves": [
        "devops-engineer",
        "site-reliability-engineer",
        "platform-engineer",
        "infrastructure-engineer",
        "cloud-engineer"
      ]
    },
    {
      "id": "network-and-systems",
      "name": "Network and Systems Administration",
      "familyId": "infrastructure-and-operations",
      "leaves": [
        "network-engineer",
        "systems-administrator"
      ]
    },
    {
      "id": "it-operations",
      "name": "IT Support and Analysis",
      "familyId": "infrastructure-and-operations",
      "leaves": [
        "it-support-specialist",
        "it-systems-analyst"
      ]
    },
    {
      "id": "security-engineering",
      "name": "Security Engineering",
      "familyId": "security",
      "leaves": [
        "security-engineer",
        "application-security-engineer",
        "penetration-tester",
        "security-architect"
      ]
    },
    {
      "id": "security-operations",
      "name": "Security Operations and Assurance",
      "familyId": "security",
      "leaves": [
        "security-analyst",
        "digital-forensics-analyst",
        "security-compliance-analyst"
      ]
    },
    {
      "id": "hardware-engineering",
      "name": "Hardware and Silicon",
      "familyId": "hardware-and-robotics",
      "leaves": [
        "hardware-engineer",
        "asic-fpga-engineer"
      ]
    },
    {
      "id": "robotics-and-autonomy",
      "name": "Robotics and Autonomy",
      "familyId": "hardware-and-robotics",
      "leaves": [
        "robotics-engineer",
        "autonomy-engineer"
      ]
    },
    {
      "id": "product-management",
      "name": "Product Management",
      "familyId": "product-and-program",
      "leaves": [
        "product-manager",
        "technical-product-manager"
      ]
    },
    {
      "id": "program-management",
      "name": "Program and Project Management",
      "familyId": "product-and-program",
      "leaves": [
        "technical-program-manager",
        "it-project-manager",
        "scrum-master"
      ]
    },
    {
      "id": "engineering-leadership",
      "name": "Engineering Leadership",
      "familyId": "product-and-program",
      "leaves": [
        "engineering-manager",
        "engineering-executive",
        "it-manager"
      ]
    },
    {
      "id": "product-design",
      "name": "Product Design",
      "familyId": "design-and-content",
      "leaves": [
        "product-designer",
        "ux-designer",
        "ux-researcher"
      ]
    },
    {
      "id": "interface-and-game-design",
      "name": "Interface and Game Design",
      "familyId": "design-and-content",
      "leaves": [
        "web-designer",
        "game-designer"
      ]
    },
    {
      "id": "technical-content",
      "name": "Technical Content",
      "familyId": "design-and-content",
      "leaves": [
        "technical-writer",
        "developer-advocate"
      ]
    }
  ],
  "leaves": [
    {
      "id": "software-engineer",
      "name": "Software Engineer",
      "groupId": "application-engineering",
      "familyId": "software-engineering",
      "soc": [
        "15-1252.00",
        "15-1251.00"
      ],
      "note": "The unqualified role. Kept deliberately generic and at the lowest specificity in the family so that any title naming a discipline outranks it; a posting that only says 'Software Engineer' genuinely has not said more than that.",
      "specificity": 10
    },
    {
      "id": "backend-engineer",
      "name": "Backend Engineer",
      "groupId": "application-engineering",
      "familyId": "software-engineering",
      "soc": [
        "15-1252.00"
      ],
      "note": "Server-side application work. API, microservice and distributed-systems titles fold in because they name the same stack from a different angle.",
      "specificity": 32
    },
    {
      "id": "frontend-engineer",
      "name": "Frontend Engineer",
      "groupId": "application-engineering",
      "familyId": "software-engineering",
      "soc": [
        "15-1252.00",
        "15-1254.00"
      ],
      "note": "Component-framework product work. Framework-named titles (React, Angular, Vue) fold in; they are the clearest signal a posting is frontend.",
      "specificity": 32
    },
    {
      "id": "full-stack-engineer",
      "name": "Full Stack Engineer",
      "groupId": "application-engineering",
      "familyId": "software-engineering",
      "soc": [
        "15-1252.00",
        "15-1254.00"
      ],
      "note": "Above Backend and Frontend in specificity, so a title claiming both resolves here rather than to whichever half is mentioned first.",
      "specificity": 36
    },
    {
      "id": "web-developer",
      "name": "Web Developer",
      "groupId": "application-engineering",
      "familyId": "software-engineering",
      "soc": [
        "15-1254.00",
        "15-1299.01"
      ],
      "note": "Kept apart from Frontend Engineer on purpose: CMS, template and agency work (WordPress, Drupal, Shopify, PHP) barely shares a candidate pool with component-framework product work, and O*NET keeps 15-1254.00 separate too.",
      "specificity": 24
    },
    {
      "id": "blockchain-engineer",
      "name": "Blockchain Engineer",
      "groupId": "application-engineering",
      "familyId": "software-engineering",
      "soc": [
        "15-1299.07"
      ],
      "note": "O*NET gave this its own occupation in 15-1299.07, and the skill set really is disjoint from ordinary backend work.",
      "specificity": 40
    },
    {
      "id": "mobile-engineer",
      "name": "Mobile Engineer",
      "groupId": "mobile-engineering",
      "familyId": "software-engineering",
      "soc": [
        "15-1252.00"
      ],
      "note": "Platform-agnostic and cross-platform mobile work. Sits below the iOS and Android leaves so a title naming a platform wins.",
      "specificity": 30
    },
    {
      "id": "ios-engineer",
      "name": "iOS Engineer",
      "groupId": "mobile-engineering",
      "familyId": "software-engineering",
      "soc": [
        "15-1252.00"
      ],
      "note": "Apple-platform work. `ios` is guarded against Cisco IOS, which is a networking title.",
      "specificity": 42
    },
    {
      "id": "android-engineer",
      "name": "Android Engineer",
      "groupId": "mobile-engineering",
      "familyId": "software-engineering",
      "soc": [
        "15-1252.00"
      ],
      "note": "Android and Kotlin work.",
      "specificity": 42
    },
    {
      "id": "systems-engineer",
      "name": "Systems Engineer",
      "groupId": "systems-engineering",
      "familyId": "software-engineering",
      "soc": [
        "15-1252.00",
        "15-1299.08"
      ],
      "note": "Writing the layer underneath: kernels, operating systems, compilers, runtimes, storage engines. Distinct from Infrastructure Engineer, which runs fleets rather than building the thing they run. The deny list on this rule is long because 'systems' is the single most overloaded word in tech titles.",
      "specificity": 22
    },
    {
      "id": "embedded-engineer",
      "name": "Embedded Software Engineer",
      "groupId": "systems-engineering",
      "familyId": "software-engineering",
      "soc": [
        "15-1252.00",
        "17-2061.00"
      ],
      "note": "Firmware folds in. 'Embedded Software Engineer' and 'Firmware Engineer' describe the same C-on-a-microcontroller job and splitting them would only split the aliases. 'Embedded Hardware Engineer' is excluded so it lands on Hardware Engineer, where it belongs.",
      "specificity": 44
    },
    {
      "id": "game-engineer",
      "name": "Game Engineer",
      "groupId": "systems-engineering",
      "familyId": "software-engineering",
      "soc": [
        "15-1252.00",
        "15-1255.01"
      ],
      "note": "Engine, gameplay and rendering code. Graphics and shader titles fold in here rather than getting a leaf of their own; almost all of them are games or real-time rendering. Game Designer is a separate leaf in the Design family because it is not a programming job.",
      "specificity": 40
    },
    {
      "id": "qa-automation-engineer",
      "name": "QA Automation Engineer",
      "groupId": "quality-engineering",
      "familyId": "software-engineering",
      "soc": [
        "15-1253.00"
      ],
      "note": "Writes test code. Bare 'Automation Engineer' is deliberately not an alias: in O*NET it sits under Robotics Engineers and in the wild it is usually industrial, not software. Hardware and manufacturing test titles are excluded so they fall to the hardware leaves.",
      "specificity": 46
    },
    {
      "id": "qa-analyst",
      "name": "QA Analyst",
      "groupId": "quality-engineering",
      "familyId": "software-engineering",
      "soc": [
        "15-1253.00"
      ],
      "note": "Manual and exploratory testing. Shares SOC 15-1253.00 with QA Automation but is a different hiring track, and conflating them would tell an automation candidate to apply for click-through work.",
      "specificity": 44
    },
    {
      "id": "software-architect",
      "name": "Software Architect",
      "groupId": "software-architecture",
      "familyId": "software-engineering",
      "soc": [
        "15-1252.00",
        "15-1299.08"
      ],
      "note": "Internal system design. Bare 'Architect' is never an alias, for the obvious reason that most of them design buildings.",
      "specificity": 50
    },
    {
      "id": "solutions-architect",
      "name": "Solutions Architect",
      "groupId": "software-architecture",
      "familyId": "software-engineering",
      "soc": [
        "15-1299.08",
        "15-1211.00"
      ],
      "note": "The customer-facing build-and-integrate role. Cloud Architect and Enterprise Architect fold in, and so does the Palantir-style Forward Deployed Engineer: what these share is that the work is scoped by somebody else's environment, which matters more than the stack. Bare 'Sales Engineer' stays out; it is a quota role.",
      "specificity": 52
    },
    {
      "id": "data-engineer",
      "name": "Data Engineer",
      "groupId": "data-engineering",
      "familyId": "data-and-ai",
      "soc": [
        "15-2051.00",
        "15-1243.00",
        "15-1243.01"
      ],
      "note": "Pipelines and warehouses. Data Platform Engineer and Data Warehouse Engineer fold in; O*NET keeps 15-1243.01 Data Warehousing Specialists separate, but the 2026 market does not and neither should a retrieval key.",
      "specificity": 40
    },
    {
      "id": "analytics-engineer",
      "name": "Analytics Engineer",
      "groupId": "data-engineering",
      "familyId": "data-and-ai",
      "soc": [
        "15-2051.00",
        "15-2051.01"
      ],
      "note": "The dbt-and-warehouse-modelling role between Data Engineer and Data Analyst. Business Intelligence Engineer and Developer fold in; BI Analyst stays separate because that job is reporting and stakeholder work, not modelling.",
      "specificity": 42
    },
    {
      "id": "data-architect",
      "name": "Data Architect",
      "groupId": "data-engineering",
      "familyId": "data-and-ai",
      "soc": [
        "15-1243.00"
      ],
      "note": "O*NET's 15-1243.00 Database Architects. Modelling and governance rather than pipeline building, which is why it is not folded into Data Engineer.",
      "specificity": 48
    },
    {
      "id": "database-administrator",
      "name": "Database Administrator",
      "groupId": "data-engineering",
      "familyId": "data-and-ai",
      "soc": [
        "15-1242.00"
      ],
      "note": "Running the database rather than modelling or querying it. 'Database Engineer' and 'Database Reliability Engineer' fold in, since in practice they are DBA postings with a nicer title.",
      "specificity": 46
    },
    {
      "id": "data-scientist",
      "name": "Data Scientist",
      "groupId": "data-science-and-analytics",
      "familyId": "data-and-ai",
      "soc": [
        "15-2051.00"
      ],
      "note": "O*NET's 15-2051.00 as intended, minus the ML-engineering and analyst titles its alternate-title list also files there.",
      "specificity": 40
    },
    {
      "id": "data-analyst",
      "name": "Data Analyst",
      "groupId": "data-science-and-analytics",
      "familyId": "data-and-ai",
      "soc": [
        "15-2051.00",
        "15-2051.01"
      ],
      "note": "Product Analyst and Insights Analyst fold in: same reporting-and-experimentation job under different org names. Business, financial, credit, risk and clinical analysts are excluded because most of those postings contain no software at all.",
      "specificity": 34
    },
    {
      "id": "business-intelligence-analyst",
      "name": "Business Intelligence Analyst",
      "groupId": "data-science-and-analytics",
      "familyId": "data-and-ai",
      "soc": [
        "15-2051.01"
      ],
      "note": "Its own O*NET occupation and its own hiring track: dashboards and stakeholder reporting, above Data Analyst in specificity so the BI qualifier wins.",
      "specificity": 44
    },
    {
      "id": "ml-engineer",
      "name": "Machine Learning Engineer",
      "groupId": "ml-and-ai-engineering",
      "familyId": "data-and-ai",
      "soc": [
        "15-2051.00",
        "15-1299.08"
      ],
      "note": "Training and serving models. NLP, speech, ranking and recommendation engineers fold in: same toolchain, and splitting them would split a thin alias set for no filtering gain.",
      "specificity": 46
    },
    {
      "id": "ai-engineer",
      "name": "AI Engineer",
      "groupId": "ml-and-ai-engineering",
      "familyId": "data-and-ai",
      "soc": [
        "15-1252.00",
        "15-2051.00"
      ],
      "note": "The LLM-application role: prompting, retrieval, tools and evals on top of hosted models. Kept apart from ML Engineer because in 2026 the two share almost no day-to-day stack, which is precisely the distinction O*NET cannot make.",
      "specificity": 46
    },
    {
      "id": "mlops-engineer",
      "name": "MLOps Engineer",
      "groupId": "ml-and-ai-engineering",
      "familyId": "data-and-ai",
      "soc": [
        "15-1299.08",
        "15-2051.00"
      ],
      "note": "Training infrastructure and model serving. Above both ML Engineer and Platform Engineer in specificity, so 'ML Platform Engineer' lands here rather than being pulled apart by whichever half matched.",
      "specificity": 50
    },
    {
      "id": "computer-vision-engineer",
      "name": "Computer Vision Engineer",
      "groupId": "ml-and-ai-engineering",
      "familyId": "data-and-ai",
      "soc": [
        "17-2061.00",
        "15-2051.00"
      ],
      "note": "Vision and perception. `cv` is only ever matched as the full phrase 'cv engineer', because on its own it means curriculum vitae.",
      "specificity": 50
    },
    {
      "id": "ml-research-scientist",
      "name": "Machine Learning Research Scientist",
      "groupId": "research",
      "familyId": "data-and-ai",
      "soc": [
        "15-1221.00",
        "15-2051.00"
      ],
      "note": "Publishes. Bare 'Research Scientist' is deliberately left unmapped: O*NET files it under 15-1221.00, 15-2051.00 and 19-1029.01 at once, so guessing between computer science, data science and biology is exactly the wrong-answer-worse-than-none case.",
      "specificity": 54
    },
    {
      "id": "applied-scientist",
      "name": "Applied Scientist",
      "groupId": "research",
      "familyId": "data-and-ai",
      "soc": [
        "15-2051.00",
        "15-1221.00"
      ],
      "note": "The Amazon-and-friends title: research training, product deliverables. Kept separate from both Data Scientist and ML Research Scientist because candidates and hiring loops treat it as its own thing.",
      "specificity": 52
    },
    {
      "id": "research-engineer",
      "name": "Research Engineer",
      "groupId": "research",
      "familyId": "data-and-ai",
      "soc": [
        "15-1221.00",
        "17-2199.08"
      ],
      "note": "Builds the research, does not necessarily author it. The frontier-lab title. Excludes market, user and clinical research, which are different professions entirely.",
      "specificity": 48
    },
    {
      "id": "bioinformatics-scientist",
      "name": "Bioinformatics Scientist",
      "groupId": "research",
      "familyId": "data-and-ai",
      "soc": [
        "19-1029.01",
        "15-2099.01"
      ],
      "note": "The one health-adjacent leaf kept in scope, because computational biology hires CS and stats candidates directly. Everything else in O*NET's health informatics branch is excluded; see EXCLUDED_SOC.",
      "specificity": 54
    },
    {
      "id": "devops-engineer",
      "name": "DevOps Engineer",
      "groupId": "platform-and-reliability",
      "familyId": "infrastructure-and-operations",
      "soc": [
        "15-1252.00",
        "15-1299.08"
      ],
      "note": "CI/CD and delivery automation. Build and Release Engineer fold in. Set to the same specificity as SRE on purpose: a posting titled 'DevOps / SRE' is genuinely both, and the tie rule sends it to the alias path rather than to a coin flip.",
      "specificity": 45
    },
    {
      "id": "site-reliability-engineer",
      "name": "Site Reliability Engineer",
      "groupId": "platform-and-reliability",
      "familyId": "infrastructure-and-operations",
      "soc": [
        "15-1252.00",
        "15-1299.08"
      ],
      "note": "The curated alias `sre` deliberately overrides O*NET, whose 15-1252.00 list glosses SRE as 'Software Requirements Engineer'. Production Engineer folds in (Meta's name for the same job); manufacturing production roles are excluded.",
      "specificity": 45
    },
    {
      "id": "platform-engineer",
      "name": "Platform Engineer",
      "groupId": "platform-and-reliability",
      "familyId": "infrastructure-and-operations",
      "soc": [
        "15-1299.08"
      ],
      "note": "Internal developer platform work. Developer Experience and Developer Productivity fold in. Data and ML platform titles are excluded so they land on Data Engineer and MLOps.",
      "specificity": 43
    },
    {
      "id": "infrastructure-engineer",
      "name": "Infrastructure Engineer",
      "groupId": "platform-and-reliability",
      "familyId": "infrastructure-and-operations",
      "soc": [
        "15-1299.08",
        "15-1252.00"
      ],
      "note": "Runs the fleet: hosts, networks-as-code, virtualization, storage. The counterpart to Systems Engineer, which builds the layer being run.",
      "specificity": 43
    },
    {
      "id": "cloud-engineer",
      "name": "Cloud Engineer",
      "groupId": "platform-and-reliability",
      "familyId": "infrastructure-and-operations",
      "soc": [
        "15-1299.08",
        "15-1299.05"
      ],
      "note": "Cloud-provider-shaped infrastructure work. Cloud Architect is not here; it folds into Solutions Architect, because those postings are overwhelmingly customer-facing.",
      "specificity": 45
    },
    {
      "id": "network-engineer",
      "name": "Network Engineer",
      "groupId": "network-and-systems",
      "familyId": "infrastructure-and-operations",
      "soc": [
        "15-1241.00",
        "15-1241.01",
        "15-1299.08"
      ],
      "note": "Network Architect and Telecommunications Engineer fold in. O*NET splits 15-1241.00 Computer Network Architects from 15-1241.01 Telecommunications Engineering Specialists, but the candidate pool is one pool. Network Security Engineer is excluded; it belongs to Security Engineer.",
      "specificity": 44
    },
    {
      "id": "systems-administrator",
      "name": "Systems Administrator",
      "groupId": "network-and-systems",
      "familyId": "infrastructure-and-operations",
      "soc": [
        "15-1244.00",
        "15-1299.01"
      ],
      "note": "O*NET's 15-1244.00 is literally 'Network and Computer Systems Administrators', so Network Administrator lives here rather than under Network Engineer. Web Administrator (15-1299.01) folds in too.",
      "specificity": 42
    },
    {
      "id": "it-support-specialist",
      "name": "IT Support Specialist",
      "groupId": "it-operations",
      "familyId": "infrastructure-and-operations",
      "soc": [
        "15-1232.00",
        "15-1231.00"
      ],
      "note": "Covers both of O*NET's support occupations, user support (15-1232.00) and network support (15-1231.00). One leaf because the postings are interchangeable at the level a job seeker cares about.",
      "specificity": 40
    },
    {
      "id": "it-systems-analyst",
      "name": "IT Systems Analyst",
      "groupId": "it-operations",
      "familyId": "infrastructure-and-operations",
      "soc": [
        "15-1211.00"
      ],
      "note": "O*NET's 15-1211.00 Computer Systems Analysts. Bare 'Business Analyst' is deliberately not an alias: without a technical qualifier plenty of those are finance or operations jobs, and this taxonomy would rather return nothing.",
      "specificity": 40
    },
    {
      "id": "security-engineer",
      "name": "Security Engineer",
      "groupId": "security-engineering",
      "familyId": "security",
      "soc": [
        "15-1299.05",
        "15-1212.00"
      ],
      "note": "Cloud Security, Network Security, Detection and IAM Engineer all fold in. They differ by surface area rather than by craft, and somebody filtering for 'Security Engineer' wants all of them. Application Security is the one split kept, because appsec hires from the software side.",
      "specificity": 46
    },
    {
      "id": "application-security-engineer",
      "name": "Application Security Engineer",
      "groupId": "security-engineering",
      "familyId": "security",
      "soc": [
        "15-1299.05",
        "15-1252.00"
      ],
      "note": "Above Security Engineer in specificity because appsec is the one security specialism whose candidate pool is software engineers rather than security operators.",
      "specificity": 50
    },
    {
      "id": "penetration-tester",
      "name": "Penetration Tester",
      "groupId": "security-engineering",
      "familyId": "security",
      "soc": [
        "15-1299.04"
      ],
      "note": "Its own O*NET occupation since 15-1299.04. Red team, offensive security and exploit development fold in; so does vulnerability research, which is the same skill pointed at a product instead of a client.",
      "specificity": 52
    },
    {
      "id": "security-architect",
      "name": "Security Architect",
      "groupId": "security-engineering",
      "familyId": "security",
      "soc": [
        "15-1299.05",
        "15-1241.00"
      ],
      "note": "Highest specificity in the security group so the architect qualifier always wins over the engineer and analyst rules.",
      "specificity": 54
    },
    {
      "id": "security-analyst",
      "name": "Information Security Analyst",
      "groupId": "security-operations",
      "familyId": "security",
      "soc": [
        "15-1212.00"
      ],
      "note": "SOC Analyst folds in: at the rungs where those postings exist it is the same job description, and separating them would split a thin alias set. Compliance and audit titles are excluded; they are the next leaf.",
      "specificity": 44
    },
    {
      "id": "digital-forensics-analyst",
      "name": "Digital Forensics Analyst",
      "groupId": "security-operations",
      "familyId": "security",
      "soc": [
        "15-1299.06"
      ],
      "note": "Its own O*NET occupation. Malware analysis and reverse engineering fold in; they share the tooling and the postings often share the title.",
      "specificity": 56
    },
    {
      "id": "security-compliance-analyst",
      "name": "Security Compliance Analyst",
      "groupId": "security-operations",
      "familyId": "security",
      "soc": [
        "15-1212.00",
        "15-1211.00"
      ],
      "note": "GRC, IT audit and privacy. Kept apart from Information Security Analyst because the work is evidence and control mapping rather than detection, and the two rarely swap candidates.",
      "specificity": 52
    },
    {
      "id": "hardware-engineer",
      "name": "Hardware Engineer",
      "groupId": "hardware-engineering",
      "familyId": "hardware-and-robotics",
      "soc": [
        "17-2061.00"
      ],
      "note": "Board, analog and system-level hardware. 'Embedded Hardware Engineer' resolves here rather than to Embedded Software Engineer.",
      "specificity": 44
    },
    {
      "id": "asic-fpga-engineer",
      "name": "ASIC and FPGA Engineer",
      "groupId": "hardware-engineering",
      "familyId": "hardware-and-robotics",
      "soc": [
        "17-2061.00"
      ],
      "note": "RTL, verification and physical design. Design Verification folds in: DV is a distinct career track but shares the whole language and tool stack with RTL design, and postings use the titles interchangeably. `soc` here means system-on-chip, so security-operations titles are excluded explicitly.",
      "specificity": 56
    },
    {
      "id": "robotics-engineer",
      "name": "Robotics Engineer",
      "groupId": "robotics-and-autonomy",
      "familyId": "hardware-and-robotics",
      "soc": [
        "17-2199.08"
      ],
      "note": "Mechatronics and motion planning fold in. RPA is explicitly excluded: 'Robotic Process Automation' is business-process software and matching it here would be a confident wrong answer, which is the failure mode this taxonomy cares most about.",
      "specificity": 50
    },
    {
      "id": "autonomy-engineer",
      "name": "Autonomy Engineer",
      "groupId": "robotics-and-autonomy",
      "familyId": "hardware-and-robotics",
      "soc": [
        "17-2199.08",
        "15-1252.00"
      ],
      "note": "Self-driving, drones and the perception-to-planning stack. Above Robotics Engineer in specificity because an autonomy title is the more informative of the two.",
      "specificity": 54
    },
    {
      "id": "product-manager",
      "name": "Product Manager",
      "groupId": "product-management",
      "familyId": "product-and-program",
      "soc": [
        "11-2021.00"
      ],
      "note": "O*NET has no Product Manager occupation. Its alternate-title list files 'Product Manager', 'Digital Product Manager' and 'Technical Product Manager' under 11-2021.00 Marketing Managers, so that is the honest crosswalk even though it reads oddly. Product Owner folds in, and so do domain flavours like AI PM and Data PM: the craft is the same, only the subject changes, which is not what earns a leaf here.",
      "specificity": 74
    },
    {
      "id": "technical-product-manager",
      "name": "Technical Product Manager",
      "groupId": "product-management",
      "familyId": "product-and-program",
      "soc": [
        "11-2021.00",
        "15-1299.09"
      ],
      "note": "Split from Product Manager because the screening bar is different: these postings expect the candidate to read code. Bare 'TPM' is not an alias here, since in practice it almost always means Technical Program Manager.",
      "specificity": 78
    },
    {
      "id": "technical-program-manager",
      "name": "Technical Program Manager",
      "groupId": "program-management",
      "familyId": "product-and-program",
      "soc": [
        "15-1299.09",
        "13-1082.00"
      ],
      "note": "The big-tech TPM role. `tpm` resolves here rather than to Technical Product Manager, which is the way the acronym is actually used.",
      "specificity": 78
    },
    {
      "id": "it-project-manager",
      "name": "IT Project Manager",
      "groupId": "program-management",
      "familyId": "product-and-program",
      "soc": [
        "15-1299.09",
        "13-1082.00"
      ],
      "note": "Bare 'Project Manager' is deliberately unmapped: without a technical qualifier it is as likely to be construction, marketing or events. Release Manager and Delivery Manager fold in.",
      "specificity": 74
    },
    {
      "id": "scrum-master",
      "name": "Scrum Master",
      "groupId": "program-management",
      "familyId": "product-and-program",
      "soc": [
        "15-1299.09"
      ],
      "note": "Agile Coach and Release Train Engineer fold in. Kept separate from project management because the postings screen for certifications rather than delivery ownership.",
      "specificity": 76
    },
    {
      "id": "engineering-manager",
      "name": "Engineering Manager",
      "groupId": "engineering-leadership",
      "familyId": "product-and-program",
      "soc": [
        "11-3021.00",
        "11-9041.00"
      ],
      "note": "Line management of engineers. Discipline-flavoured variants (Data Engineering Manager, QA Manager, ML Manager) fold in, and the high specificity means 'Engineering Manager, Machine Learning' resolves here rather than to ML Engineer, because it is a management job before it is an ML job. 'Information Security Manager' is excluded because it is a security-operations role, not an engineering one.",
      "specificity": 84
    },
    {
      "id": "engineering-executive",
      "name": "Engineering Executive",
      "groupId": "engineering-leadership",
      "familyId": "product-and-program",
      "soc": [
        "11-3021.00"
      ],
      "note": "Director, VP and C-level grouped as one leaf. This is the one place where a rung is allowed to define a boundary, because running an organisation is a different job from running a team; splitting Director from VP would be encoding a rung, which the seniority field already carries.",
      "specificity": 88
    },
    {
      "id": "it-manager",
      "name": "IT Manager",
      "groupId": "engineering-leadership",
      "familyId": "product-and-program",
      "soc": [
        "11-3021.00"
      ],
      "note": "The operations side of 11-3021.00: service desk, endpoints, internal systems. Kept apart from Engineering Manager because the two almost never swap candidates.",
      "specificity": 82
    },
    {
      "id": "product-designer",
      "name": "Product Designer",
      "groupId": "product-design",
      "familyId": "design-and-content",
      "soc": [
        "15-1255.00",
        "27-1021.00"
      ],
      "note": "Interaction and service designers fold in. The industrial-design senses of 27-1021.00 (toys, furniture, packaging) are excluded, which is the whole reason that SOC needs a rule rather than a bulk import.",
      "specificity": 60
    },
    {
      "id": "ux-designer",
      "name": "UX Designer",
      "groupId": "product-design",
      "familyId": "design-and-content",
      "soc": [
        "15-1255.00"
      ],
      "note": "UI Designer folds in. The market treats 'UI Designer', 'UX Designer' and 'UI/UX Designer' as one posting with three names. 'UX Engineer' is excluded and goes to Frontend Engineer, because that posting wants code.",
      "specificity": 58
    },
    {
      "id": "ux-researcher",
      "name": "UX Researcher",
      "groupId": "product-design",
      "familyId": "design-and-content",
      "soc": [
        "15-1255.00"
      ],
      "note": "Above UX Designer in specificity so the research qualifier wins. Usability Engineer folds in despite the name; the work is research.",
      "specificity": 62
    },
    {
      "id": "web-designer",
      "name": "Web Designer",
      "groupId": "interface-and-game-design",
      "familyId": "design-and-content",
      "soc": [
        "15-1255.00",
        "15-1254.00"
      ],
      "note": "Site-level visual design. Out-of-scope neighbours, Graphic Designer and Visual Designer, are deliberately left unmapped: 27-1024.00 is a print and brand occupation, not a tech one, and folding them in here would be a confident wrong answer.",
      "specificity": 50
    },
    {
      "id": "game-designer",
      "name": "Game Designer",
      "groupId": "interface-and-game-design",
      "familyId": "design-and-content",
      "soc": [
        "15-1255.01"
      ],
      "note": "O*NET's 15-1255.01 Video Game Designers. Level and narrative design fold in. Not a programming job, which is why Game Engineer lives in the Software Engineering family instead.",
      "specificity": 56
    },
    {
      "id": "technical-writer",
      "name": "Technical Writer",
      "groupId": "technical-content",
      "familyId": "design-and-content",
      "soc": [
        "27-3042.00"
      ],
      "note": "Scoped to software documentation. The medical, regulatory, grant and proposal writers that share 27-3042.00 are excluded by rule, since a CS candidate searching this taxonomy is not looking for them.",
      "specificity": 60
    },
    {
      "id": "developer-advocate",
      "name": "Developer Advocate",
      "groupId": "technical-content",
      "familyId": "design-and-content",
      "soc": [
        "27-3042.00",
        "15-1252.00"
      ],
      "note": "DevRel. Kept in this family rather than in Software Engineering because the deliverable is content and community, even though the candidate is usually an engineer.",
      "specificity": 64
    }
  ]
};
