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
  { slug: "andurilindustries", name: "Anduril Industries", domain: "anduril.com", ats: "greenhouse" },
  { slug: "gleanwork", name: "Glean", domain: "glean.com", ats: "greenhouse" },
  { slug: "sambanovasystems", name: "SambaNova Systems", domain: "sambanova.ai", ats: "greenhouse" },
  { slug: "togetherai", name: "Together AI", domain: "together.ai", ats: "greenhouse" },
  { slug: "scaleai", name: "Scale AI", domain: "scale.com", ats: "greenhouse" },
  { slug: "coreweave", name: "CoreWeave", domain: "coreweave.com", ats: "greenhouse" },
  { slug: "figureai", name: "Figure", domain: "figure.ai", ats: "greenhouse" },
  { slug: "nuro", name: "Nuro", domain: "nuro.ai", ats: "greenhouse" },
  { slug: "waymo", name: "Waymo", domain: "waymo.com", ats: "greenhouse" },
  { slug: "wayve", name: "Wayve", domain: "wayve.ai", ats: "greenhouse" },
  { slug: "vannevarlabs", name: "Vannevar Labs", domain: "vannevarlabs.com", ats: "greenhouse" },
  { slug: "snorkelai", name: "Snorkel AI", domain: "snorkel.ai", ats: "greenhouse" },
  { slug: "arizeai", name: "Arize AI", domain: "arize.com", ats: "greenhouse" },
  { slug: "labelbox", name: "Labelbox", domain: "labelbox.com", ats: "greenhouse" },
  { slug: "fireblocks", name: "Fireblocks", domain: "fireblocks.com", ats: "greenhouse" },
  { slug: "verkada", name: "Verkada", domain: "verkada.com", ats: "greenhouse" },
  { slug: "cockroachlabs", name: "Cockroach Labs", domain: "cockroachlabs.com", ats: "greenhouse" },
  { slug: "elastic", name: "Elastic", domain: "elastic.co", ats: "greenhouse" },
  { slug: "grafanalabs", name: "Grafana Labs", domain: "grafana.com", ats: "greenhouse" },
  { slug: "tailscale", name: "Tailscale", domain: "tailscale.com", ats: "greenhouse" },

  { slug: "palantir", name: "Palantir", domain: "palantir.com", ats: "lever" },
  { slug: "veeva", name: "Veeva Systems", domain: "veeva.com", ats: "lever" },
  { slug: "gopuff", name: "Gopuff", domain: "gopuff.com", ats: "lever" },
  { slug: "wealthfront", name: "Wealthfront", domain: "wealthfront.com", ats: "lever" },
  { slug: "shieldai", name: "Shield AI", domain: "shield.ai", ats: "lever" },
  { slug: "waabi", name: "Waabi", domain: "waabi.ai", ats: "lever" },
  { slug: "dexterity", name: "Dexterity", domain: "dexterity.ai", ats: "lever" },
  { slug: "zilliz", name: "Zilliz", domain: "zilliz.com", ats: "lever" },
  { slug: "metabase", name: "Metabase", domain: "metabase.com", ats: "lever" },

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
  { slug: "cerebras", name: "Cerebras Systems", domain: "cerebras.ai", ats: "ashby" },
  { slug: "perplexity", name: "Perplexity", domain: "perplexity.ai", ats: "ashby" },
  { slug: "harvey", name: "Harvey", domain: "harvey.ai", ats: "ashby" },
  { slug: "sierra", name: "Sierra", domain: "sierra.ai", ats: "ashby" },
  { slug: "writer", name: "Writer", domain: "writer.com", ats: "ashby" },
  { slug: "elevenlabs", name: "ElevenLabs", domain: "elevenlabs.io", ats: "ashby" },
  { slug: "deepgram", name: "Deepgram", domain: "deepgram.com", ats: "ashby" },
  { slug: "langchain", name: "LangChain", domain: "langchain.com", ats: "ashby" },
  { slug: "llamaindex", name: "LlamaIndex", domain: "llamaindex.ai", ats: "ashby" },
  { slug: "crusoe", name: "Crusoe", domain: "crusoe.ai", ats: "ashby" },
  { slug: "lambda", name: "Lambda", domain: "lambdalabs.com", ats: "ashby" },
  { slug: "cursor", name: "Cursor", domain: "cursor.com", ats: "ashby" },
  { slug: "baseten", name: "Baseten", domain: "baseten.co", ats: "ashby" },
  { slug: "modal", name: "Modal", domain: "modal.com", ats: "ashby" },
  { slug: "fireworks", name: "Fireworks AI", domain: "fireworks.ai", ats: "ashby" },
  { slug: "pinecone", name: "Pinecone", domain: "pinecone.io", ats: "ashby" },
  { slug: "weaviate", name: "Weaviate", domain: "weaviate.io", ats: "ashby" },
  { slug: "decagon", name: "Decagon", domain: "decagon.ai", ats: "ashby" },
  { slug: "cartesia", name: "Cartesia", domain: "cartesia.ai", ats: "ashby" },
  { slug: "distyl", name: "Distyl AI", domain: "distyl.ai", ats: "ashby" },
  { slug: "physicalintelligence", name: "Physical Intelligence", domain: "physicalintelligence.company", ats: "ashby" },
  { slug: "saronic", name: "Saronic", domain: "saronic.com", ats: "ashby" },
  { slug: "standardbots", name: "Standard Bots", domain: "standardbots.com", ats: "ashby" },
  { slug: "vanta", name: "Vanta", domain: "vanta.com", ats: "ashby" },
  { slug: "confluent", name: "Confluent", domain: "confluent.io", ats: "ashby" },
  { slug: "sentry", name: "Sentry", domain: "sentry.io", ats: "ashby" },
];

export function companiesByAts(ats: AtsProvider): AtsCompany[] {
  return ATS_COMPANIES.filter((c) => c.ats === ats);
}

export function findCompany(slug: string): AtsCompany | undefined {
  return ATS_COMPANIES.find((c) => c.slug === slug);
}
