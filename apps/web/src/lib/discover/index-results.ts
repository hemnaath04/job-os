// Maps an indexed-search hit into the same shape the live discovery path
// already renders, so ResultCard, the fit scorer, and sorting all work on
// index results with no separate code path. See docs/ingest-index.md, which
// calls this exact mapping the intended next step after the index landed.
import type { DiscoveryResult, IndexHitRead } from "@/lib/types";

/**
 * `already_imported` defaults to false here: `GET /jobs` (JobRead) does not
 * return `source_id`, only `source`, so there is no accurate way to match an
 * index hit against the user's existing jobs client-side today. Worst case
 * an already-imported role still shows an Import button; the import call
 * itself is safe to repeat.
 */
export function indexHitToDiscoveryResult(hit: IndexHitRead): DiscoveryResult {
  return {
    source: hit.source,
    source_label: null,
    source_id: hit.source_id,
    source_url: hit.source_url,
    title: hit.title,
    company_name: hit.company_name,
    company_domain: hit.company_domain,
    location: hit.location,
    country_code: hit.country_code,
    posted_at: hit.posted_at,
    description: hit.snippet,
    technologies: [],
    already_imported: false,
    match: hit.match,
  };
}
