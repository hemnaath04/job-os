/**
 * The same fit score the Job Finder shows, computed from a tracked
 * application's own job instead of a fresh discovery result.
 *
 * `scoreJob` reads `title`, `description` and `technologies` off a
 * `DiscoveryResult`. A tracked `Job` has no `description` field, only the
 * structured `jd_parsed` the import already extracted, so this builds a
 * synthetic result from that structure rather than duplicating the matching
 * logic itself.
 */
import type { Job } from "@/lib/types";
import { scoreJob, type FitResult, type ProfileVocab } from "@/lib/discover/fit-score";

export function scoreApplicationJob(job: Job, vocab: ProfileVocab): FitResult {
  const parsed = job.jd_parsed;
  const description = [
    ...(parsed?.required_skills ?? []),
    ...(parsed?.preferred_skills ?? []),
    ...(parsed?.keywords ?? []),
  ].join(". ");
  return scoreJob(
    {
      source: job.source,
      source_label: null,
      source_id: job.id,
      source_url: job.source_url ?? "",
      title: job.title,
      company_name: job.company?.name ?? null,
      company_domain: job.company?.domain ?? null,
      location: job.location ?? null,
      country_code: null,
      posted_at: job.posted_at ?? null,
      description,
      technologies: parsed?.technologies ?? [],
      already_imported: true,
    },
    vocab,
  );
}
