// Curated list of companies whose applicant-tracking boards expose a public,
// key-free JSON API. Each provider has one predictable URL shape keyed by the
// company's board slug, so a single fetch per company is all we need:
//
//   greenhouse  https://boards-api.greenhouse.io/v1/boards/{slug}/jobs
//   lever       https://api.lever.co/v0/postings/{slug}?mode=json
//   ashby       https://api.ashbyhq.com/posting-api/job-board/{slug}
//
// `domain` is carried here rather than parsed out of the posting URL: ATS
// boards are hosted on the vendor's domain (jobs.ashbyhq.com, boards.
// greenhouse.io), so the employer's real domain is only knowable from a
// curated mapping. DiscoveryResult.company_domain is what the backend uses to
// dedupe against already-imported jobs, so it has to be the employer's.

export type AtsProvider = "greenhouse" | "lever" | "ashby";

export interface AtsCompany {
  /** Board slug used in the provider's API URL. */
  slug: string;
  name: string;
  /** Employer's own domain, not the ATS vendor's. */
  domain: string;
  ats: AtsProvider;
}

export const ATS_COMPANIES: AtsCompany[] = [
  { slug: "stripe", name: "Stripe", domain: "stripe.com", ats: "greenhouse" },
  { slug: "airbnb", name: "Airbnb", domain: "airbnb.com", ats: "greenhouse" },
  { slug: "coinbase", name: "Coinbase", domain: "coinbase.com", ats: "greenhouse" },
  { slug: "databricks", name: "Databricks", domain: "databricks.com", ats: "greenhouse" },
  { slug: "robinhood", name: "Robinhood", domain: "robinhood.com", ats: "greenhouse" },
  { slug: "dropbox", name: "Dropbox", domain: "dropbox.com", ats: "greenhouse" },
  { slug: "brex", name: "Brex", domain: "brex.com", ats: "greenhouse" },
  { slug: "figma", name: "Figma", domain: "figma.com", ats: "greenhouse" },
  { slug: "discord", name: "Discord", domain: "discord.com", ats: "greenhouse" },
  { slug: "reddit", name: "Reddit", domain: "reddit.com", ats: "greenhouse" },
  { slug: "cloudflare", name: "Cloudflare", domain: "cloudflare.com", ats: "greenhouse" },
  { slug: "gitlab", name: "GitLab", domain: "gitlab.com", ats: "greenhouse" },
  { slug: "mongodb", name: "MongoDB", domain: "mongodb.com", ats: "greenhouse" },
  { slug: "datadog", name: "Datadog", domain: "datadoghq.com", ats: "greenhouse" },
  { slug: "anthropic", name: "Anthropic", domain: "anthropic.com", ats: "greenhouse" },
  { slug: "mercury", name: "Mercury", domain: "mercury.com", ats: "greenhouse" },
  { slug: "vercel", name: "Vercel", domain: "vercel.com", ats: "greenhouse" },
  { slug: "chime", name: "Chime", domain: "chime.com", ats: "greenhouse" },
  { slug: "affirm", name: "Affirm", domain: "affirm.com", ats: "greenhouse" },
  { slug: "imc", name: "IMC", domain: "imc.com", ats: "greenhouse" },

  { slug: "plaid", name: "Plaid", domain: "plaid.com", ats: "ashby" },
  { slug: "ramp", name: "Ramp", domain: "ramp.com", ats: "ashby" },
  { slug: "notion", name: "Notion", domain: "notion.so", ats: "ashby" },
  { slug: "openai", name: "OpenAI", domain: "openai.com", ats: "ashby" },
  { slug: "benchling", name: "Benchling", domain: "benchling.com", ats: "ashby" },
  { slug: "linear", name: "Linear", domain: "linear.app", ats: "ashby" },
  { slug: "replit", name: "Replit", domain: "replit.com", ats: "ashby" },
  { slug: "posthog", name: "PostHog", domain: "posthog.com", ats: "ashby" },
  { slug: "cohere", name: "Cohere", domain: "cohere.com", ats: "ashby" },
  { slug: "runway", name: "Runway", domain: "runwayml.com", ats: "ashby" },
];

export function companiesByAts(ats: AtsProvider): AtsCompany[] {
  return ATS_COMPANIES.filter((c) => c.ats === ats);
}

export function findCompany(slug: string): AtsCompany | undefined {
  return ATS_COMPANIES.find((c) => c.slug === slug);
}
