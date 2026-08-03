// Profile-aware fit scoring for discovery results.
//
// The Job Finder feed used to be ordered by recency and, at best, re-sorted by
// whether the words the user typed appeared in the posting. That answers "is
// this the search I asked for", not "is this a job I am a fit for". This module
// answers the second question the way the tailor does: deterministic overlap of
// the candidate's verified skills against the skills a posting names. No LLM, no
// embeddings, no per-job network call, so it can score a whole page instantly
// and for free.
//
// The score is a coverage ratio the user can read off the card: of the key
// skills THIS posting names, how many the profile already backs. That keeps it
// honest (a long generic JD cannot inflate it) and explainable (the matched and
// missing skills are shown, not just a number).

import type { DiscoveryResult, ProfileFact } from "@/lib/types";

// One canonical skill, the way it should read on a card, and every surface form
// that means the same thing. Aliases are what make this more than a string
// match: "k8s" in a JD and "Kubernetes" on the profile are the same skill, and
// the tailor treats them that way too. Keep forms lowercase.
interface LexEntry {
  canon: string;
  display: string;
  forms: string[];
}

const LEXICON: LexEntry[] = [
  // Languages
  { canon: "python", display: "Python", forms: ["python"] },
  { canon: "java", display: "Java", forms: ["java"] },
  { canon: "c++", display: "C++", forms: ["c++", "cpp"] },
  { canon: "c#", display: "C#", forms: ["c#"] },
  { canon: "go", display: "Go", forms: ["golang", "go lang"] },
  { canon: "typescript", display: "TypeScript", forms: ["typescript"] },
  { canon: "javascript", display: "JavaScript", forms: ["javascript"] },
  { canon: "rust", display: "Rust", forms: ["rust"] },
  { canon: "scala", display: "Scala", forms: ["scala"] },
  { canon: "sql", display: "SQL", forms: ["sql"] },
  { canon: "bash", display: "Bash", forms: ["bash", "shell scripting"] },
  // Backend and web frameworks
  { canon: "fastapi", display: "FastAPI", forms: ["fastapi", "fast api"] },
  { canon: "django", display: "Django", forms: ["django"] },
  { canon: "flask", display: "Flask", forms: ["flask"] },
  { canon: "node", display: "Node.js", forms: ["node.js", "nodejs", "node js"] },
  { canon: "express", display: "Express", forms: ["express.js", "expressjs"] },
  { canon: "spring", display: "Spring", forms: ["spring boot", "springboot", "spring framework"] },
  { canon: "react", display: "React", forms: ["react.js", "reactjs", "react"] },
  { canon: "next", display: "Next.js", forms: ["next.js", "nextjs"] },
  // Data stores and messaging
  { canon: "postgresql", display: "PostgreSQL", forms: ["postgresql", "postgres", "psql"] },
  { canon: "mysql", display: "MySQL", forms: ["mysql"] },
  { canon: "mongodb", display: "MongoDB", forms: ["mongodb", "mongo"] },
  { canon: "redis", display: "Redis", forms: ["redis"] },
  { canon: "kafka", display: "Kafka", forms: ["kafka"] },
  { canon: "rabbitmq", display: "RabbitMQ", forms: ["rabbitmq"] },
  { canon: "elasticsearch", display: "Elasticsearch", forms: ["elasticsearch", "elastic search"] },
  { canon: "snowflake", display: "Snowflake", forms: ["snowflake"] },
  { canon: "databricks", display: "Databricks", forms: ["databricks"] },
  { canon: "vector-db", display: "Vector database", forms: ["vector database", "vector db", "vector store", "pinecone", "weaviate", "pgvector", "faiss", "milvus", "qdrant", "chroma"] },
  // Cloud and infra
  { canon: "aws", display: "AWS", forms: ["aws", "amazon web services"] },
  { canon: "gcp", display: "GCP", forms: ["gcp", "google cloud"] },
  { canon: "azure", display: "Azure", forms: ["azure"] },
  { canon: "docker", display: "Docker", forms: ["docker", "containerization", "containers"] },
  { canon: "kubernetes", display: "Kubernetes", forms: ["kubernetes", "k8s"] },
  { canon: "terraform", display: "Terraform", forms: ["terraform"] },
  { canon: "iac", display: "Infrastructure as Code", forms: ["infrastructure as code", "pulumi", "cloudformation"] },
  { canon: "ci-cd", display: "CI/CD", forms: ["ci/cd", "cicd", "continuous integration", "continuous delivery"] },
  { canon: "github-actions", display: "GitHub Actions", forms: ["github actions"] },
  { canon: "jenkins", display: "Jenkins", forms: ["jenkins"] },
  { canon: "serverless", display: "Serverless", forms: ["serverless", "lambda", "cloud functions"] },
  { canon: "vercel", display: "Vercel", forms: ["vercel"] },
  // ML and AI
  { canon: "pytorch", display: "PyTorch", forms: ["pytorch"] },
  { canon: "tensorflow", display: "TensorFlow", forms: ["tensorflow"] },
  { canon: "scikit-learn", display: "scikit-learn", forms: ["scikit-learn", "scikit learn", "sklearn"] },
  { canon: "pandas", display: "pandas", forms: ["pandas"] },
  { canon: "numpy", display: "NumPy", forms: ["numpy"] },
  { canon: "machine-learning", display: "Machine learning", forms: ["machine learning", "ml models", "ml pipeline"] },
  { canon: "deep-learning", display: "Deep learning", forms: ["deep learning", "neural networks"] },
  { canon: "nlp", display: "NLP", forms: ["nlp", "natural language processing"] },
  { canon: "computer-vision", display: "Computer vision", forms: ["computer vision", "image recognition", "object detection"] },
  { canon: "llm", display: "LLMs", forms: ["llm", "llms", "large language model", "large language models"] },
  { canon: "rag", display: "RAG", forms: ["rag", "retrieval augmented generation", "retrieval-augmented generation"] },
  { canon: "langchain", display: "LangChain", forms: ["langchain"] },
  { canon: "langgraph", display: "LangGraph", forms: ["langgraph"] },
  { canon: "llamaindex", display: "LlamaIndex", forms: ["llamaindex", "llama index"] },
  { canon: "prompt-engineering", display: "Prompt engineering", forms: ["prompt engineering", "prompting"] },
  { canon: "embeddings", display: "Embeddings", forms: ["embeddings", "semantic search", "vector search"] },
  { canon: "fine-tuning", display: "Fine-tuning", forms: ["fine-tuning", "fine tuning", "lora", "peft"] },
  { canon: "agents", display: "AI agents", forms: ["ai agents", "agentic", "agent orchestration", "multi-agent", "multi agent"] },
  { canon: "transformers", display: "Transformers", forms: ["transformer", "transformers", "attention mechanism"] },
  { canon: "mlops", display: "MLOps", forms: ["mlops", "model serving", "model deployment", "model inference"] },
  { canon: "openai", display: "OpenAI API", forms: ["openai", "gpt-4", "gpt-3"] },
  { canon: "anthropic", display: "Anthropic / Claude", forms: ["anthropic", "claude"] },
  { canon: "huggingface", display: "Hugging Face", forms: ["hugging face", "huggingface", "transformers library"] },
  // APIs and systems
  { canon: "rest", display: "REST APIs", forms: ["rest api", "rest apis", "restful"] },
  { canon: "graphql", display: "GraphQL", forms: ["graphql"] },
  { canon: "grpc", display: "gRPC", forms: ["grpc"] },
  { canon: "microservices", display: "Microservices", forms: ["microservices", "microservice"] },
  { canon: "distributed-systems", display: "Distributed systems", forms: ["distributed systems", "distributed system"] },
  { canon: "api-design", display: "API design", forms: ["api design", "api development"] },
  { canon: "websockets", display: "WebSockets", forms: ["websocket", "websockets"] },
  { canon: "concurrency", display: "Concurrency", forms: ["concurrency", "concurrent", "multithreading", "async"] },
  { canon: "event-driven", display: "Event-driven", forms: ["event-driven", "event driven", "message queue", "pub/sub"] },
  // Testing and quality
  { canon: "testing", display: "Testing", forms: ["unit testing", "integration testing", "test automation", "automated testing"] },
  { canon: "pytest", display: "pytest", forms: ["pytest"] },
  { canon: "selenium", display: "Selenium", forms: ["selenium"] },
  { canon: "playwright", display: "Playwright", forms: ["playwright"] },
  // Data and observability
  { canon: "data-pipeline", display: "Data pipelines", forms: ["data pipeline", "data pipelines", "etl", "elt"] },
  { canon: "airflow", display: "Airflow", forms: ["airflow"] },
  { canon: "spark", display: "Spark", forms: ["spark", "pyspark"] },
  { canon: "observability", display: "Observability", forms: ["observability", "monitoring", "prometheus", "grafana", "datadog", "tracing"] },
];

// Reverse index: every surface form points at its entry. Built once at module
// load, not per score.
const FORM_TO_ENTRY = new Map<string, LexEntry>();
for (const e of LEXICON) {
  for (const f of e.forms) FORM_TO_ENTRY.set(f, e);
}
const SINGLE_WORD_FORMS: [string, LexEntry][] = [];
const PHRASE_FORMS: [string, LexEntry][] = [];
for (const e of LEXICON) {
  for (const f of e.forms) {
    (f.includes(" ") ? PHRASE_FORMS : SINGLE_WORD_FORMS).push([f, e]);
  }
}

export interface ProfileVocab {
  // False when the profile carries no skill we can recognise, so callers can
  // fall back to recency instead of showing everyone a 0% fit.
  ready: boolean;
  // Canonical skills the candidate has, plus any custom skill titles that are
  // not in the lexicon (kept verbatim so a niche skill still counts).
  canon: Set<string>;
  custom: Set<string>;
  display: Map<string, string>;
}

export interface FitResult {
  score: number; // 0-100
  matched: string[]; // display skills the posting names and the profile backs
  gaps: string[]; // display skills the posting names and the profile lacks
  // False when the posting carried too little text to judge (for example a
  // GitHub listing whose description is only fetched at import time).
  confident: boolean;
}

function normalize(s: string): string {
  return s.toLowerCase().replace(/\s+/g, " ").trim();
}

// Word-boundary presence for a single token, so "go" does not match "goal" and
// "sql" does not match "mssql-only" prose. Phrases are matched as substrings of
// the normalized text, which is enough for "machine learning" and friends.
function tokenSet(norm: string): Set<string> {
  return new Set(norm.split(/[^a-z0-9+#.]+/).filter(Boolean));
}

function formsPresent(
  norm: string,
  tokens: Set<string>,
): { canon: Set<string>; hitForm: Set<string> } {
  const canon = new Set<string>();
  const hitForm = new Set<string>();
  for (const [form, entry] of SINGLE_WORD_FORMS) {
    if (tokens.has(form)) {
      canon.add(entry.canon);
      hitForm.add(form);
    }
  }
  for (const [form, entry] of PHRASE_FORMS) {
    if (norm.includes(form)) {
      canon.add(entry.canon);
      hitForm.add(form);
    }
  }
  return { canon, hitForm };
}

// Build the candidate's skill vocabulary from their verified facts. Skill facts
// contribute their title (the strongest signal), and every fact's bullets are
// scanned for lexicon skills so a technology named in a work or project bullet
// counts even when it is not listed as a standalone skill.
export function buildProfileVocab(facts: ProfileFact[]): ProfileVocab {
  const canon = new Set<string>();
  const custom = new Set<string>();
  const display = new Map<string, string>();
  for (const e of LEXICON) display.set(e.canon, e.display);

  for (const fact of facts) {
    const title = normalize(fact.title ?? "");
    if (fact.kind === "skill" && title) {
      const { canon: c } = formsPresent(title, tokenSet(title));
      if (c.size > 0) {
        for (const t of c) canon.add(t);
      } else {
        // A real skill the lexicon does not know (a niche tool, a domain term).
        // Keep it verbatim so it can still match a posting that names it.
        custom.add(title);
        display.set(title, fact.title);
      }
    }
    const blob = normalize(
      [fact.title, ...(fact.bullets ?? []).map((b) => b.text ?? "")].join(" "),
    );
    if (blob) {
      const { canon: c } = formsPresent(blob, tokenSet(blob));
      for (const t of c) canon.add(t);
    }
  }

  return { ready: canon.size + custom.size > 0, canon, custom, display };
}

// The skills a posting names: lexicon skills found in its title, description and
// technology tags, plus any custom profile skill the posting mentions verbatim.
// This is the denominator, "the skills this role asks for", not every word in a
// long JD.
function jobKeyTerms(
  result: DiscoveryResult,
  vocab: ProfileVocab,
): { key: Set<string>; displayOf: (t: string) => string } {
  const techBlob = (result.technologies ?? []).join(" ");
  const norm = normalize(`${result.title} ${result.description} ${techBlob}`);
  const tokens = tokenSet(norm);
  const { canon } = formsPresent(norm, tokens);

  const key = new Set<string>(canon);
  // A technology tag the source already extracted is an explicit ask even if the
  // prose did not spell it out. Map it onto a canonical skill when we can.
  for (const raw of result.technologies ?? []) {
    const t = normalize(raw);
    const entry = FORM_TO_ENTRY.get(t);
    if (entry) key.add(entry.canon);
    else if (t) key.add(`tech:${t}`);
  }
  // Custom profile skills the posting names verbatim.
  for (const c of vocab.custom) {
    if (norm.includes(c)) key.add(c);
  }

  const displayOf = (t: string): string => {
    if (t.startsWith("tech:")) return t.slice(5);
    return vocab.display.get(t) ?? t;
  };
  return { key, displayOf };
}

const MIN_TERMS_FOR_CONFIDENCE = 3;

/**
 * Smallest denominator the score may use.
 *
 * A raw matched/named ratio rewards a posting for saying little. A mechanical
 * engineering internship that happens to name computer vision, git and R scored
 * 100% against a backend profile, because all three matched and there was
 * nothing else to miss, which ranked it above roles that are genuinely a fit.
 *
 * Dividing by at least this many terms turns "few signals" into "low
 * confidence" rather than "perfect match". A posting that names eight or more
 * skills is scored on its own terms; a thinner one is capped in proportion to
 * how little it actually told us.
 */
const MIN_SCORE_DENOMINATOR = 8;

export function scoreJob(result: DiscoveryResult, vocab: ProfileVocab): FitResult {
  if (!vocab.ready) {
    return { score: 0, matched: [], gaps: [], confident: false };
  }
  const { key, displayOf } = jobKeyTerms(result, vocab);
  if (key.size === 0) {
    return { score: 0, matched: [], gaps: [], confident: false };
  }

  const has = (t: string): boolean =>
    t.startsWith("tech:")
      ? vocab.canon.has(t.slice(5)) || vocab.custom.has(t.slice(5))
      : vocab.canon.has(t) || vocab.custom.has(t);

  const matched: string[] = [];
  const gaps: string[] = [];
  for (const t of key) {
    if (has(t)) matched.push(displayOf(t));
    else gaps.push(displayOf(t));
  }

  const score = Math.round(
    (matched.length / Math.max(key.size, MIN_SCORE_DENOMINATOR)) * 100,
  );
  return {
    score,
    matched: dedupe(matched),
    gaps: dedupe(gaps),
    confident: key.size >= MIN_TERMS_FOR_CONFIDENCE,
  };
}

function dedupe(items: string[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const i of items) {
    const k = i.toLowerCase();
    if (seen.has(k)) continue;
    seen.add(k);
    out.push(i);
  }
  return out;
}
