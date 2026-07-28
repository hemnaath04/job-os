// Throwaway smoke test: hits the real, key-free endpoints and prints what the
// orchestrator normalizes them into.
//
//   cd apps/web && npx tsx scripts/test-discover.ts
//
// Not part of the build or the typecheck target for the app runtime; it just
// proves the sources are alive and the DiscoveryResult mapping is sane.

import {
  discoverNoKey,
  fetchAshby,
  fetchGreenhouse,
  fetchLever,
  fetchRemoteOK,
  fetchRemotive,
} from "../src/lib/discover/no-key-sources";
import { findCompany } from "../src/lib/discover/ats-companies";
import type { DiscoveryResult } from "../src/lib/types";

function line(): void {
  console.log("-".repeat(78));
}

function preview(r: DiscoveryResult): void {
  console.log(`  ${r.title}`);
  console.log(
    `    source=${r.source} label=${r.source_label} id=${r.source_id}`,
  );
  console.log(
    `    company=${r.company_name} domain=${r.company_domain} ` +
      `location=${r.location} country=${r.country_code}`,
  );
  console.log(`    posted_at=${r.posted_at}  url=${r.source_url}`);
  console.log(
    `    tech=[${r.technologies.join(", ")}] imported=${r.already_imported}`,
  );
  console.log(
    `    description(${r.description.length} chars): ` +
      `${r.description.slice(0, 140).replace(/\n/g, " ")}...`,
  );
}

async function probeSingleSources(): Promise<void> {
  line();
  console.log("SINGLE-SOURCE PROBES");
  line();

  const gh = findCompany("vercel")!;
  const ghJobs = await fetchGreenhouse(gh, { content: true });
  console.log(`fetchGreenhouse(vercel, content=true): ${ghJobs.length} jobs`);
  if (ghJobs[0]) preview(ghJobs[0]);

  const ashby = findCompany("linear")!;
  const ashbyJobs = await fetchAshby(ashby);
  console.log(`\nfetchAshby(linear): ${ashbyJobs.length} jobs`);
  if (ashbyJobs[0]) preview(ashbyJobs[0]);

  // No Lever companies are in the curated dataset yet, so exercise the
  // function against a live Lever board directly.
  const leverJobs = await fetchLever({
    slug: "palantir",
    name: "Palantir",
    domain: "palantir.com",
    ats: "lever",
  });
  console.log(`\nfetchLever(palantir, ad-hoc): ${leverJobs.length} jobs`);
  if (leverJobs[0]) preview(leverJobs[0]);

  const remotive = await fetchRemotive("engineer", { limit: 20 });
  console.log(`\nfetchRemotive("engineer"): ${remotive.length} jobs`);
  if (remotive[0]) preview(remotive[0]);

  const remoteok = await fetchRemoteOK();
  console.log(`\nfetchRemoteOK(): ${remoteok.length} jobs`);
  if (remoteok[0]) preview(remoteok[0]);
}

async function main(): Promise<void> {
  await probeSingleSources();

  line();
  console.log('ORCHESTRATOR: titleKeywords ["engineer", "intern"], limit 50');
  line();

  const t0 = Date.now();
  const { results, source_counts, errors } = await discoverNoKey({
    titleKeywords: ["engineer", "intern"],
    limit: 50,
  });
  const ms = Date.now() - t0;

  console.log(`took ${ms}ms  results=${results.length}`);
  console.log("source_counts:", JSON.stringify(source_counts));
  console.log("errors:", errors.length ? JSON.stringify(errors, null, 2) : "none");

  const withDescription = results.filter((r) => r.description.length > 0).length;
  const withCountry = results.filter((r) => r.country_code).length;
  const withPosted = results.filter((r) => r.posted_at).length;
  console.log(
    `field coverage: description=${withDescription}/${results.length} ` +
      `country_code=${withCountry}/${results.length} ` +
      `posted_at=${withPosted}/${results.length}`,
  );

  console.log("\n5 SAMPLE NORMALIZED RESULTS");
  line();
  for (const r of results.slice(0, 5)) {
    preview(r);
    console.log();
  }

  line();
  console.log("ORCHESTRATOR: remote-only, location filter, single company");
  line();
  const remoteOnly = await discoverNoKey({
    titleKeywords: ["engineer"],
    remote: true,
    limit: 10,
  });
  console.log(
    `remote:true -> ${remoteOnly.results.length} results`,
    JSON.stringify(remoteOnly.source_counts),
  );

  const nyc = await discoverNoKey({
    titleKeywords: ["engineer"],
    location: "new york",
    includeRemoteBoards: false,
    limit: 10,
  });
  console.log(
    `location:"new york" (ATS only) -> ${nyc.results.length} results`,
    JSON.stringify(nyc.source_counts),
  );
  for (const r of nyc.results.slice(0, 3)) {
    console.log(`    ${r.company_name} | ${r.title} | ${r.location}`);
  }

  const oneCompany = await discoverNoKey({
    companies: ["anthropic"],
    titleKeywords: ["engineer"],
    includeRemoteBoards: false,
    limit: 5,
  });
  console.log(
    `\ncompanies:["anthropic"] -> ${oneCompany.results.length} results`,
    JSON.stringify(oneCompany.source_counts),
  );
  for (const r of oneCompany.results) {
    console.log(`    ${r.title} | ${r.location} | ${r.posted_at}`);
  }

  line();
  console.log("FAILURE PATHS: errors must be reported, never thrown");
  line();

  for (const [label, fn] of [
    ["greenhouse", fetchGreenhouse],
    ["ashby", fetchAshby],
    ["lever", fetchLever],
  ] as const) {
    try {
      const rows = await fn({
        slug: "notarealslugxyz",
        name: "Nope",
        domain: "nope.test",
        ats: label,
      });
      console.log(`  ${label} bad slug -> UNEXPECTED ${rows.length} rows`);
    } catch (e) {
      console.log(`  ${label} bad slug -> rejects: ${(e as Error).message}`);
    }
  }

  // A 1ms budget forces every board to abort, which is the same code path a
  // dead endpoint takes. The orchestrator must still resolve.
  const starved = await discoverNoKey({
    titleKeywords: ["engineer"],
    timeoutMs: 1,
    hydrateDescriptions: false,
  });
  console.log(
    `\n  timeoutMs:1 -> resolved with ${starved.results.length} results, ` +
      `${starved.errors.length} error rows (aggregated per source)`,
  );
  for (const e of starved.errors) {
    console.log(`    ${e.source}: ${e.message.slice(0, 110)}`);
  }

  const noMatch = await discoverNoKey({
    companies: ["stripe"],
    titleKeywords: ["nonexistenttitlekeywordxyz"],
    includeRemoteBoards: false,
  });
  console.log(
    `\n  no-match search -> ${noMatch.results.length} results, ` +
      `errors=${noMatch.errors.length}`,
  );
}

main().catch((e) => {
  console.error("FAILED", e);
  process.exit(1);
});
