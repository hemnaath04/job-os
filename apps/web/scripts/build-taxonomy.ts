/**
 * Builds the job title taxonomy artifacts from the hand-authored spec plus the
 * committed O*NET CSVs.
 *
 *   cd apps/web && pnpm taxonomy:build
 *
 * Inputs
 *   src/lib/taxonomy/spec.ts        hand-authored families, groups, leaves
 *   data/onet/job_titles.csv.gz     O*NET alternate titles (57,543 rows)
 *   data/onet/sample_of_reported_titles.csv.gz  O*NET reported titles (7,953)
 *
 * Outputs
 *   src/lib/taxonomy/generated/taxonomy.ts    flat hierarchy, typed
 *   src/lib/taxonomy/generated/aliases.ts     alias -> leaf id, two maps
 *   src/lib/taxonomy/generated/taxonomy.json  same hierarchy for non-TS readers
 *
 * The O*NET side is alias raw material only. A row is imported when, and only
 * when, one of the leaves crosswalked to that row's SOC code claims it by
 * pattern. Everything else is reported as unmapped so a human can decide
 * whether it deserves a rule; nothing is imported on the strength of its SOC
 * code alone, because a SOC code fans out to as many as ten leaves and several
 * of O*NET's occupations carry titles from outside this taxonomy entirely.
 *
 * Re-runnable against a new O*NET release. See data/onet/NOTICE.md.
 *
 * Contains modified O*NET data. This product uses public information provided
 * by the O*NET Program: O*NET 30.3 Database by the U.S. Department of Labor,
 * Employment and Training Administration (USDOL/ETA), used under CC BY 4.0.
 * USDOL/ETA has not approved, endorsed or tested these modifications.
 */

import { gunzipSync } from "node:zlib";
import { existsSync, readFileSync, mkdirSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { bestMatch, allMatches } from "../src/lib/taxonomy/match.ts";
import type { PatternCandidate } from "../src/lib/taxonomy/match.ts";
import {
  cleanTitle,
  isDeniedOccupation,
  stripNoise,
  stripSeniority,
} from "../src/lib/taxonomy/normalize.ts";
import {
  EXCLUDED_SOC,
  ONET_VERSION,
  TAXONOMY_SPEC,
  TAXONOMY_VERSION,
} from "../src/lib/taxonomy/spec.ts";
import type {
  SocCode,
  Taxonomy,
  TaxonomyFamily,
  TaxonomyGroup,
  TaxonomyLeaf,
} from "../src/lib/taxonomy/types.ts";

const HERE = dirname(fileURLToPath(import.meta.url));
const WEB_ROOT = resolve(HERE, "..");
const DATA_DIR = process.env.ONET_DATA_DIR ?? join(WEB_ROOT, "data", "onet");
const OUT_DIR = join(WEB_ROOT, "src", "lib", "taxonomy", "generated");

const ATTRIBUTION = [
  "This product uses public information provided by the O*NET Program:",
  `O*NET ${ONET_VERSION} Database by the U.S. Department of Labor,`,
  "Employment and Training Administration (USDOL/ETA). Used under the",
  "CC BY 4.0 license. O*NET is a registered trademark of USDOL/ETA.",
  "job.os has modified this information and USDOL/ETA has not approved,",
  "endorsed or tested these modifications.",
];

// ---------------------------------------------------------------------------
// CSV
// ---------------------------------------------------------------------------

/** Minimal RFC 4180 reader. O*NET quotes any field containing a comma. */
function parseCsv(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let inQuotes = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (inQuotes) {
      if (ch === '"') {
        if (text[i + 1] === '"') {
          field += '"';
          i++;
        } else inQuotes = false;
      } else field += ch;
      continue;
    }
    if (ch === '"') inQuotes = true;
    else if (ch === ",") {
      row.push(field);
      field = "";
    } else if (ch === "\n") {
      row.push(field);
      field = "";
      rows.push(row);
      row = [];
    } else if (ch !== "\r") field += ch;
  }
  if (field.length > 0 || row.length > 0) {
    row.push(field);
    rows.push(row);
  }
  return rows;
}

function readOnetCsv(basename: string): Array<Record<string, string>> {
  const gz = join(DATA_DIR, `${basename}.gz`);
  const plain = join(DATA_DIR, basename);
  let text: string;
  if (existsSync(gz)) text = gunzipSync(readFileSync(gz)).toString("utf8");
  else if (existsSync(plain)) text = readFileSync(plain, "utf8");
  else {
    throw new Error(
      `Missing ${basename} in ${DATA_DIR}. See data/onet/NOTICE.md for the download URL, ` +
        `or set ONET_DATA_DIR.`,
    );
  }
  const rows = parseCsv(text);
  const header = rows[0];
  const out: Array<Record<string, string>> = [];
  for (let i = 1; i < rows.length; i++) {
    if (rows[i].length === 1 && rows[i][0] === "") continue;
    const rec: Record<string, string> = {};
    for (let c = 0; c < header.length; c++) rec[header[c]] = rows[i][c] ?? "";
    out.push(rec);
  }
  return out;
}

// ---------------------------------------------------------------------------
// Flatten and validate the spec
// ---------------------------------------------------------------------------

interface BuildContext {
  taxonomy: Taxonomy;
  /** Leaf id -> pattern candidate, for the matcher. */
  candidates: Map<string, PatternCandidate>;
  /** SOC code -> leaf ids crosswalked to it. */
  socToLeaves: Map<SocCode, string[]>;
  /** Cleaned curated alias -> leaf id. */
  curated: Map<string, string>;
}

function fail(message: string): never {
  console.error(`\nbuild-taxonomy: ${message}\n`);
  process.exit(1);
}

function buildContext(): BuildContext {
  const families: TaxonomyFamily[] = [];
  const groups: TaxonomyGroup[] = [];
  const leaves: TaxonomyLeaf[] = [];
  const candidates = new Map<string, PatternCandidate>();
  const socToLeaves = new Map<SocCode, string[]>();
  const curated = new Map<string, string>();

  const seenIds = new Set<string>();
  const requireUniqueId = (id: string, kind: string): void => {
    if (seenIds.has(id)) fail(`duplicate ${kind} id "${id}"`);
    seenIds.add(id);
    if (!/^[a-z0-9-]+$/.test(id)) fail(`${kind} id "${id}" must be kebab-case`);
  };

  for (const familySpec of TAXONOMY_SPEC) {
    requireUniqueId(familySpec.id, "family");
    families.push({
      id: familySpec.id,
      name: familySpec.name,
      groups: familySpec.groups.map((g) => g.id),
    });
    for (const groupSpec of familySpec.groups) {
      requireUniqueId(groupSpec.id, "group");
      groups.push({
        id: groupSpec.id,
        name: groupSpec.name,
        familyId: familySpec.id,
        leaves: groupSpec.leaves.map((l) => l.id),
      });
      for (const leafSpec of groupSpec.leaves) {
        requireUniqueId(leafSpec.id, "leaf");
        if (leafSpec.soc.length === 0) fail(`leaf "${leafSpec.id}" has no SOC crosswalk`);
        if (leafSpec.match.length === 0) fail(`leaf "${leafSpec.id}" has no match rules`);
        if (leafSpec.note.trim().length === 0) fail(`leaf "${leafSpec.id}" has no note`);
        leaves.push({
          id: leafSpec.id,
          name: leafSpec.name,
          groupId: groupSpec.id,
          familyId: familySpec.id,
          soc: leafSpec.soc,
          note: leafSpec.note,
          specificity: leafSpec.specificity,
        });
        candidates.set(leafSpec.id, {
          leafId: leafSpec.id,
          specificity: leafSpec.specificity,
          rules: leafSpec.match,
        });
        for (const soc of leafSpec.soc) {
          const list = socToLeaves.get(soc) ?? [];
          list.push(leafSpec.id);
          socToLeaves.set(soc, list);
        }
        for (const raw of leafSpec.aliases) {
          const key = stripNoise(cleanTitle(raw));
          if (key.length === 0) fail(`leaf "${leafSpec.id}" has an alias that cleans to nothing: "${raw}"`);
          const existing = curated.get(key);
          if (existing !== undefined && existing !== leafSpec.id) {
            fail(
              `curated alias "${key}" is claimed by both "${existing}" and "${leafSpec.id}". ` +
                `Pick one; a duplicate would make normalization order-dependent.`,
            );
          }
          curated.set(key, leafSpec.id);
        }
      }
    }
  }

  return {
    taxonomy: {
      version: TAXONOMY_VERSION,
      onetVersion: ONET_VERSION,
      generatedAt: new Date().toISOString().slice(0, 10),
      families,
      groups,
      leaves,
    },
    candidates,
    socToLeaves,
    curated,
  };
}

// ---------------------------------------------------------------------------
// Harvest O*NET titles
// ---------------------------------------------------------------------------

/**
 * Alias candidates from one O*NET row.
 *
 * O*NET writes acronym titles as "AI Specialist (Artificial Intelligence
 * Specialist)" with `Short Title` holding the shorter of the two. Both readings
 * are usable aliases, but the flattened whole ("ai specialist artificial
 * intelligence specialist") is not, so the parenthetical is pulled apart rather
 * than stripped.
 */
function onetAliasCandidates(jobTitle: string, shortTitle: string): string[] {
  const inside = [...jobTitle.matchAll(/\(([^)]*)\)/g)].map((m) => m[1]);
  const outside = jobTitle.replace(/\([^)]*\)/g, " ");
  return [outside, ...inside, shortTitle].filter((s) => s.trim().length > 0);
}

interface HarvestResult {
  onetAliases: Map<string, string>;
  /** Alias keys two or more leaves claimed at equal specificity. */
  ambiguous: Array<{ key: string; soc: SocCode; leaves: string[] }>;
  /** Alias keys where a curated entry overrode an O*NET reading. */
  overridden: Array<{ key: string; curated: string; onet: string }>;
  /** O*NET titles no leaf claimed, by SOC. */
  unmapped: Map<SocCode, string[]>;
  /** Titles blocked by the occupation deny list. */
  denied: string[];
  rowsConsidered: number;
}

function harvest(ctx: BuildContext, rows: Array<Record<string, string>>, titleColumn: string): HarvestResult {
  const onetAliases = new Map<string, string>();
  const onetSpecificity = new Map<string, number>();
  const ambiguous: HarvestResult["ambiguous"] = [];
  const overridden: HarvestResult["overridden"] = [];
  const unmapped = new Map<SocCode, string[]>();
  const denied: string[] = [];
  let rowsConsidered = 0;

  for (const row of rows) {
    const soc = row["O*NET-SOC Code"];
    const leafIds = ctx.socToLeaves.get(soc);
    if (leafIds === undefined) continue;
    rowsConsidered++;

    const socCandidates: PatternCandidate[] = leafIds.map((id) => {
      const c = ctx.candidates.get(id);
      if (c === undefined) fail(`internal: no pattern candidate for leaf "${id}"`);
      return c;
    });

    const jobTitle = row[titleColumn] ?? "";
    const shortTitle = row["Short Title"] ?? "";
    let claimedAny = false;

    for (const candidate of onetAliasCandidates(jobTitle, shortTitle)) {
      const base = stripNoise(cleanTitle(candidate));
      if (base.length === 0) continue;
      if (isDeniedOccupation(base)) {
        denied.push(`${soc}  ${candidate.trim()}`);
        continue;
      }

      const keys = [base];
      const stripped = stripSeniority(base);
      if (stripped.length >= 4 && stripped !== base) keys.push(stripped);

      for (const key of keys) {
        const curatedLeaf = ctx.curated.get(key);
        // A title already hand-written into the spec counts as covered even if
        // no pattern claims it, otherwise the unmapped report is mostly noise.
        if (curatedLeaf !== undefined) claimedAny = true;

        const hit = bestMatch(socCandidates, key);
        if (hit === null) {
          const hits = allMatches(socCandidates, key);
          if (hits.length > 1) {
            ambiguous.push({ key, soc, leaves: hits.map((h) => h.leafId) });
          }
          continue;
        }
        claimedAny = true;

        if (curatedLeaf !== undefined) {
          if (curatedLeaf !== hit.leafId) {
            overridden.push({ key, curated: curatedLeaf, onet: hit.leafId });
          }
          continue;
        }

        const priorLeaf = onetAliases.get(key);
        if (priorLeaf === undefined) {
          onetAliases.set(key, hit.leafId);
          onetSpecificity.set(key, hit.specificity);
          continue;
        }
        if (priorLeaf === hit.leafId) continue;
        const priorSpecificity = onetSpecificity.get(key) ?? 0;
        if (hit.specificity > priorSpecificity) {
          onetAliases.set(key, hit.leafId);
          onetSpecificity.set(key, hit.specificity);
        } else if (hit.specificity === priorSpecificity) {
          // Two SOC codes point the same string at two equally specific
          // leaves. Neither reading wins, so drop it entirely.
          onetAliases.delete(key);
          onetSpecificity.delete(key);
          ambiguous.push({ key, soc, leaves: [priorLeaf, hit.leafId] });
        }
      }
    }

    if (!claimedAny) {
      const list = unmapped.get(soc) ?? [];
      list.push(jobTitle);
      unmapped.set(soc, list);
    }
  }

  return { onetAliases, ambiguous, overridden, unmapped, denied, rowsConsidered };
}

// ---------------------------------------------------------------------------
// Emit
// ---------------------------------------------------------------------------

function banner(): string {
  return [
    "/**",
    " * GENERATED FILE. Do not edit.",
    " * Run `pnpm taxonomy:build` in apps/web after changing spec.ts or the O*NET data.",
    " *",
    ...ATTRIBUTION.map((l) => ` * ${l}`),
    " */",
    "",
  ].join("\n");
}

function emitTaxonomyModule(taxonomy: Taxonomy): string {
  return [
    banner(),
    'import type { Taxonomy } from "../types.ts";',
    "",
    `export const TAXONOMY: Taxonomy = ${JSON.stringify(taxonomy, null, 2)};`,
    "",
  ].join("\n");
}

function emitAliasModule(curated: Map<string, string>, onet: Map<string, string>): string {
  const asRecord = (m: Map<string, string>): string => {
    const keys = [...m.keys()].sort();
    const lines = keys.map((k) => `  ${JSON.stringify(k)}: ${JSON.stringify(m.get(k))},`);
    return `{\n${lines.join("\n")}\n}`;
  };
  return [
    banner(),
    "/**",
    " * Hand-written aliases. Trusted, and they win over anything derived from",
    " * O*NET; `sre` is the load-bearing example, since O*NET reads it as Software",
    " * Requirements Engineer.",
    " */",
    `export const CURATED_ALIASES: Readonly<Record<string, string>> = ${asRecord(curated)};`,
    "",
    "/**",
    " * Aliases harvested from O*NET's alternate and reported title lists, kept in a",
    " * separate map so the runtime can score them slightly lower.",
    " */",
    `export const ONET_ALIASES: Readonly<Record<string, string>> = ${asRecord(onet)};`,
    "",
  ].join("\n");
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

function main(): void {
  const ctx = buildContext();

  const alternate = readOnetCsv("job_titles.csv");
  const reported = readOnetCsv("sample_of_reported_titles.csv");

  const socInRelease = new Set(alternate.map((r) => r["O*NET-SOC Code"]));
  const missing = [...ctx.socToLeaves.keys()].filter((s) => !socInRelease.has(s));
  if (missing.length > 0) {
    fail(
      `these SOC codes are referenced by spec.ts but absent from O*NET ${ONET_VERSION}: ` +
        `${missing.join(", ")}. Re-check the crosswalk against the new release.`,
    );
  }

  // Every CS/Math occupation must be either crosswalked or explicitly excluded.
  const csMath = [...socInRelease].filter((s) => s.startsWith("15-1") || s.startsWith("15-2")).sort();
  const unaccounted = csMath.filter(
    (s) => !ctx.socToLeaves.has(s) && !(s in EXCLUDED_SOC),
  );
  if (unaccounted.length > 0) {
    fail(
      `these CS/Math SOC codes are neither crosswalked nor listed in EXCLUDED_SOC: ` +
        `${unaccounted.join(", ")}. Add a leaf or a documented exclusion.`,
    );
  }

  const fromAlternate = harvest(ctx, alternate, "Job Title");
  const fromReported = harvest(ctx, reported, "Reported Job Title");

  const onetAliases = new Map(fromAlternate.onetAliases);
  for (const [key, leafId] of fromReported.onetAliases) {
    if (ctx.curated.has(key)) continue;
    if (!onetAliases.has(key)) onetAliases.set(key, leafId);
  }

  mkdirSync(OUT_DIR, { recursive: true });
  writeFileSync(join(OUT_DIR, "taxonomy.ts"), emitTaxonomyModule(ctx.taxonomy), "utf8");
  writeFileSync(join(OUT_DIR, "aliases.ts"), emitAliasModule(ctx.curated, onetAliases), "utf8");
  writeFileSync(
    join(OUT_DIR, "taxonomy.json"),
    `${JSON.stringify({ notice: ATTRIBUTION.join(" "), ...ctx.taxonomy }, null, 2)}\n`,
    "utf8",
  );

  report(ctx, csMath, onetAliases, fromAlternate, fromReported);
}

function report(
  ctx: BuildContext,
  csMath: SocCode[],
  onetAliases: Map<string, string>,
  fromAlternate: HarvestResult,
  fromReported: HarvestResult,
): void {
  const t = ctx.taxonomy;
  const line = (s = ""): void => console.log(s);

  line(`taxonomy ${t.version}  O*NET ${t.onetVersion}`);
  line(`  families ${t.families.length}  groups ${t.groups.length}  leaves ${t.leaves.length}`);
  line(
    `  aliases   curated ${ctx.curated.size}  from O*NET ${onetAliases.size}  ` +
      `total ${ctx.curated.size + onetAliases.size}`,
  );
  line();

  const covered = csMath.filter((s) => ctx.socToLeaves.has(s));
  line(
    `SOC coverage: ${covered.length}/${csMath.length} CS and Math occupations crosswalked, ` +
      `${Object.keys(EXCLUDED_SOC).length} excluded by design`,
  );
  const adjacent = [...ctx.socToLeaves.keys()]
    .filter((s) => !s.startsWith("15-1") && !s.startsWith("15-2"))
    .sort();
  line(`  plus ${adjacent.length} adjacent codes: ${adjacent.join(", ")}`);
  line();

  const fanOut = [...ctx.socToLeaves.entries()]
    .map(([soc, leaves]) => ({ soc, n: leaves.length, leaves }))
    .sort((a, b) => b.n - a.n)
    .slice(0, 6);
  line("Widest SOC fan-out (the reason this layer exists):");
  for (const f of fanOut) line(`  ${f.soc} -> ${f.n} leaves: ${f.leaves.join(", ")}`);
  line();

  const rows = fromAlternate.rowsConsidered + fromReported.rowsConsidered;
  const unmappedAll = new Map<SocCode, string[]>();
  for (const src of [fromAlternate.unmapped, fromReported.unmapped]) {
    for (const [soc, titles] of src) {
      unmappedAll.set(soc, [...(unmappedAll.get(soc) ?? []), ...titles]);
    }
  }
  const unmappedCount = [...unmappedAll.values()].reduce((a, b) => a + b.length, 0);
  line(
    `O*NET rows in scope: ${rows}. Claimed by a leaf: ${rows - unmappedCount}. ` +
      `Unclaimed: ${unmappedCount}.`,
  );
  const denied = [...fromAlternate.denied, ...fromReported.denied];
  line(`  blocked by the occupation deny list: ${denied.length}`);
  if (denied.length > 0) line(`    e.g. ${denied.slice(0, 4).join(" | ")}`);
  line();

  const overridden = new Map<string, { curated: string; onet: string }>();
  for (const o of [...fromAlternate.overridden, ...fromReported.overridden]) {
    overridden.set(o.key, { curated: o.curated, onet: o.onet });
  }
  if (overridden.size > 0) {
    line(`Curated aliases that overrode an O*NET reading (${overridden.size}):`);
    for (const [key, o] of [...overridden].slice(0, 14)) {
      line(`  "${key}"  curated -> ${o.curated}   O*NET would say -> ${o.onet}`);
    }
    line();
  }

  const ambiguous = [...fromAlternate.ambiguous, ...fromReported.ambiguous];
  const ambiguousUnique = new Map<string, string[]>();
  for (const a of ambiguous) ambiguousUnique.set(a.key, a.leaves);
  if (ambiguousUnique.size > 0) {
    line(`Dropped as ambiguous, tied on specificity (${ambiguousUnique.size}):`);
    for (const [key, leaves] of [...ambiguousUnique].slice(0, 12)) {
      line(`  "${key}"  <- ${leaves.join(" / ")}`);
    }
    line();
  }

  const worstUnmapped = [...unmappedAll.entries()]
    .map(([soc, titles]) => ({ soc, titles }))
    .sort((a, b) => b.titles.length - a.titles.length)
    .slice(0, 8);
  line("Unclaimed O*NET titles by SOC (candidates for a new rule or a shrug):");
  for (const u of worstUnmapped) {
    line(`  ${u.soc}  ${u.titles.length}`);
    line(`    ${u.titles.slice(0, 8).join(" | ")}`);
  }
  line();

  const perLeaf = new Map<string, number>();
  for (const leafId of [...ctx.curated.values(), ...onetAliases.values()]) {
    perLeaf.set(leafId, (perLeaf.get(leafId) ?? 0) + 1);
  }
  const thin = t.leaves
    .map((l) => ({ id: l.id, n: perLeaf.get(l.id) ?? 0 }))
    .sort((a, b) => a.n - b.n)
    .slice(0, 8);
  line("Leaves with the fewest aliases (thin retrieval keys):");
  for (const l of thin) line(`  ${l.n.toString().padStart(3)}  ${l.id}`);
  line();
  line(`Wrote ${OUT_DIR}`);
}

main();
