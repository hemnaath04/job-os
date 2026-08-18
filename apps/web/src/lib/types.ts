// Hand-rolled mirror of apps/api Pydantic schemas.
// TODO: replace with auto-generated from OpenAPI once pnpm gen:types runs.

export type AppStatus =
  | "wishlist"
  | "ready_to_apply"
  | "applied"
  | "oa_received"
  | "interview_scheduled"
  | "offer"
  | "accepted"
  | "rejected"
  | "withdrawn"
  | "ghosted";

/**
 * What a resume version's state is called on screen. Without this the raw
 * column value was printed with its underscores swapped for spaces, so the UI
 * said "needs changes" in lower case beside properly cased copy.
 */
export const VERSION_STATUS_LABELS: Record<string, string> = {
  draft: "Draft",
  reviewed: "Reviewed",
  needs_changes: "Needs changes",
  final: "Final",
};

/** Falls back to the raw value so an unknown state still shows something. */
export function versionStatusLabel(status: string): string {
  return VERSION_STATUS_LABELS[status] ?? status.replaceAll("_", " ");
}

export const STATUS_LABELS: Record<AppStatus, string> = {
  wishlist: "Wishlist",
  ready_to_apply: "Ready",
  applied: "Applied",
  oa_received: "OA",
  interview_scheduled: "Interview",
  offer: "Offer",
  accepted: "Accepted",
  rejected: "Rejected",
  withdrawn: "Withdrawn",
  ghosted: "Ghosted",
};

export const KANBAN_STATUSES: AppStatus[] = [
  "wishlist",
  "ready_to_apply",
  "applied",
  "oa_received",
  "interview_scheduled",
  "offer",
  "rejected",
];

export interface Company {
  id: string;
  name: string;
  domain?: string | null;
  industry?: string | null;
}

export interface Job {
  id: string;
  company?: Company | null;
  title: string;
  level?: string | null;
  function?: string | null;
  location?: string | null;
  remote?: string | null;
  // The backend's JobRead has always returned these; they were never added
  // here because nothing displayed salary until the Applications inspector.
  salary_min?: number | null;
  salary_max?: number | null;
  salary_currency?: string;
  source: string;
  source_url?: string | null;
  posted_at?: string | null;
  active: boolean;
  jd_parsed?: {
    required_skills?: string[];
    preferred_skills?: string[];
    technologies?: string[];
    keywords?: string[];
  } | null;
}

export interface FactBullet {
  id: string;
  text: string;
  target_role: string | null;
  metric_verified: boolean;
  created_at: string;
  updated_at: string;
}

export interface ProfileFact {
  id: string;
  kind: "education" | "experience" | "project" | "skill" | "certification" |
        "publication" | "award" | "volunteering" | "leadership";
  title: string;
  org: string | null;
  start_date: string | null;
  end_date: string | null;
  location: string | null;
  payload: Record<string, unknown>;
  verified: boolean;
  source_url: string | null;
  bullets: FactBullet[];
  created_at: string;
  updated_at: string;
}

/**
 * A saved look: Jinja HTML plus CSS, rendered against the documented resume
 * context. Templates are look-only and hold no resume data, so applying one
 * never touches the resume it renders. `created_from_resume_id` is provenance
 * only, a record of where the look came from, not a link that owns anything.
 */
export interface ResumeTemplate {
  id: string;
  name: string;
  description: string | null;
  created_from_resume_id: string | null;
  source_file_id: string | null;
  /** First page of the sample render, as a PNG. What the picker shows. */
  preview_file_id: string | null;
  /** The whole sample render, for anyone who wants to read the real thing. */
  preview_pdf_file_id?: string | null;
  created_at: string;
  updated_at: string;
  /**
   * `builtin` ships with the app and its LaTeX lives in the API container.
   * `custom` was built from the user's own upload and its LaTeX is stored on
   * the row. `legacy_html` predates the LaTeX engine and can no longer render,
   * so the picker leaves it out; the rows are kept rather than deleted.
   */
  kind?: "builtin" | "custom" | "legacy_html";
  engine?: string;
  /** Which vendored template directory a builtin names. */
  builtin_key?: string | null;
  columns?: number;
  /**
   * How this layout tends to fare in an applicant tracking system, in plain
   * words. Shown next to the template: two of the seven are two-column designs
   * that some parsers read badly, and hiding that would cost somebody an
   * interview.
   */
  ats_note?: string;
  tags?: string[];
  author?: string;
  licence?: string;
  upstream?: string;
  /** What was changed from upstream to make it compile here. */
  changes?: string;
  /** For a custom template: what the model said it could not match. */
  notes?: string | null;
}

export interface Resume {
  id: string;
  name: string;
  base_role: string | null;
  is_master: boolean;
  /**
   * Legacy. An earlier iteration split the library by tagging resumes; looks now
   * live as their own template rows, so this is read from old snapshots and
   * otherwise ignored. Every resume is a source resume.
   */
  category?: string;
  /**
   * The job posting this resume was created for, when Tailor made it. Lets a
   * second run on the same job add a version to the same resume instead of
   * creating a near-identical one. Absent on resumes made by hand or before
   * tailoring started naming its output after the job.
   */
  job_posting_id?: string | null;
  source_kind: string | null;
  source_label: string | null;
  /**
   * Set when this resume container itself was made for one specific company
   * (an MCP-uploaded tailored resume), not a general-purpose data identity.
   * Separates the two in the UI — otherwise indistinguishable rows.
   */
  spawned_from_application_id?: string | null;
  archived_at: string | null;
  created_at: string;
  updated_at: string;
  /** Non-archived versions with spawned_from_job_id set — real tailored output. */
  tailored_count: number;
}

export interface ResumeVersionSummary {
  id: string;
  resume_id: string;
  ats_score: string | null;
  approved_by_user: boolean;
  pdf_r2_key: string | null;
  docx_r2_key: string | null;
  spawned_from_job_id: string | null;
  spawned_from_application_id: string | null;
  status: "draft" | "reviewed" | "needs_changes" | "final" | string;
  review_score: string | null;
  review_report: ResumeReviewResult | null;
  parent_version_id: string | null;
  source_filename: string | null;
  revision_note: string | null;
  finalized_at: string | null;
  archived_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ProvenanceEntry {
  section: "work" | "projects" | "volunteer";
  text: string;
  fact_bullet_id: string;
  fact_id: string;
}

export interface GapQuestion {
  requirement: string;
  why_no_match: string;
  suggested_fact_ids: string[];
}

export interface JsonResume {
  basics?: {
    name?: string;
    label?: string;
    email?: string;
    phone?: string;
    url?: string;
    summary?: string;
    location?: { address?: string; city?: string; region?: string; countryCode?: string };
    profiles?: { network: string; username: string; url: string }[];
  };
  work?: {
    name?: string;
    position?: string;
    startDate?: string | null;
    endDate?: string | null;
    location?: string | null;
    summary?: string | null;
    url?: string | null;
    highlights?: string[];
    keywords?: string[];
  }[];
  projects?: {
    name?: string;
    description?: string | null;
    startDate?: string | null;
    endDate?: string | null;
    url?: string | null;
    highlights?: string[];
    keywords?: string[];
    roles?: string[];
    entity?: string | null;
    type?: string | null;
  }[];
  volunteer?: {
    organization?: string;
    position?: string;
    startDate?: string | null;
    endDate?: string | null;
    url?: string | null;
    summary?: string | null;
    highlights?: string[];
  }[];
  education?: {
    institution?: string;
    area?: string | null;
    studyType?: string | null;
    startDate?: string | null;
    endDate?: string | null;
    score?: string | null;
    courses?: string[];
    location?: string | null;
    url?: string | null;
  }[];
  skills?: { name: string; keywords: string[] }[];
  certificates?: { name: string; issuer?: string | null; date?: string | null; url?: string | null }[];
  languages?: { language: string; fluency?: string | null }[];
  publications?: {
    name: string;
    publisher?: string | null;
    releaseDate?: string | null;
    url?: string | null;
    summary?: string | null;
  }[];
  awards?: {
    title: string;
    awarder?: string | null;
    date?: string | null;
    summary?: string | null;
  }[];
}

export interface ResumeVersion extends ResumeVersionSummary {
  json_resume: JsonResume;
  provenance: ProvenanceEntry[];
  ats_report: {
    matched: string[];
    missing: string[];
    matched_count: number;
    missing_count: number;
  } | null;
  latex_source: string | null;
}

export interface ResumeReviewIssue {
  severity: "blocking" | "warning" | "suggestion";
  code: string;
  message: string;
  section: string | null;
}

export interface ResumeReviewResult {
  score: string;
  passed: boolean;
  page_count: number;
  text_selectable: boolean;
  issues: ResumeReviewIssue[];
  strengths: string[];
  github_projects_checked: string[];
  model_summary: string;
}

/**
 * One claim an "Edit with AI" request tried to add that no verified Profile fact
 * supports, so the revise guard left it out while applying the rest of the edit.
 * Mirrors the backend BlockedClaim (job_os.services.resume_engine): `metric` is
 * the offending number(s), `text` the sentence they appeared in, `reason` why it
 * was dropped, `remedy` how to keep it. Optional on the responses below because
 * an edit with nothing unsupported carries none.
 */
export interface BlockedClaim {
  metric: string;
  text: string;
  reason: string;
  remedy: string;
}

export interface ResumeChatResponse {
  message: string;
  suggestions: string[];
  proposal_id: string | null;
  proposed_json_resume: JsonResume | null;
  version: ResumeVersion | null;
  review: ResumeReviewResult | null;
  blocked_claims?: BlockedClaim[];
}

export interface RevisionMessage {
  id: string;
  resume_version_id: string;
  role: "user" | "assistant";
  content: string;
  suggestions: string[];
  proposed_json_resume: JsonResume | null;
  applied: boolean;
  /** Present on an assistant message when the guard dropped unsupported claims. */
  blocked_claims?: BlockedClaim[];
  created_at: string;
  updated_at: string;
}

export interface ResumeImportItem {
  filename: string;
  resume_id: string | null;
  version_id: string | null;
  imported: boolean;
  is_master: boolean;
  note: string;
}

export interface TailorResponse extends ResumeVersion {
  gap_questions: GapQuestion[];
  agent_note: string;
}

export interface CalendarEntry {
  application_id: string;
  when: string; // ISO datetime
  label: string;
  status: AppStatus;
  job_id: string;
  job_title: string;
  company_name: string | null;
}

/** One past status change (applied, rejected, ...), for the calendar's month view. */
export interface CalendarHistoryEntry {
  application_id: string;
  occurred_at: string; // ISO datetime
  status: AppStatus;
  job_id: string;
  job_title: string;
  company_name: string | null;
}

/**
 * The candidate's own eligibility status, which is the half a posting cannot
 * tell you. `lib/discover/work-auth.ts` reads the employer's half off the
 * posting; on its own that can only warn, because "does not sponsor" is
 * disqualifying for one user and irrelevant to the next.
 *
 * `null` and "other" are different: null is never asked, and nothing should be
 * inferred from it.
 */
export type WorkAuthorization =
  | "us_citizen"
  | "permanent_resident"
  | "visa_holder_needs_transfer"
  | "needs_sponsorship"
  | "other";

export type SeniorityLevel = "intern" | "new-grad" | "mid" | "senior" | "staff";

export type WorkModel = "onsite" | "hybrid" | "remote";

/** Inclusive band. Either end may be null, meaning no bound on that side. */
export interface SeniorityRange {
  min: SeniorityLevel | null;
  max: SeniorityLevel | null;
}

export interface UserSettings {
  theme: "system" | "dark" | "light";
  default_resume_id: string | null;
  default_function: string | null;
  default_level: string | null;
  /**
   * Superseded by `locations`, and still live: the Job Finder reads it to decide
   * what counts as local. The API keeps the two in step, and the Settings page
   * sends the first location here on save, so nothing reading it goes stale.
   */
  default_location: string | null;
  timezone: string | null;
  /**
   * Roles to search for, most wanted first. Plain strings today; a canonical
   * title taxonomy will own these ids later.
   */
  target_titles: string[];
  work_authorization: WorkAuthorization | null;
  /** Lowest acceptable base pay per year, in `salary_currency`. */
  salary_floor: number | null;
  /** ISO-4217 alpha-3, upper case. */
  salary_currency: string;
  seniority_range: SeniorityRange;
  /** Empty means all three are acceptable, not none of them. */
  work_models: WorkModel[];
  /** Surfaced first. Not a whitelist: it ranks, it does not gate. */
  target_companies: string[];
  /** Dropped outright. This one does gate. */
  excluded_companies: string[];
  max_job_age_days: number;
  /** Cities, regions, or "Remote". Empty means anywhere. */
  locations: string[];
}

export type UserSettingsPatch = Partial<UserSettings>;

export interface MeRead {
  id: string;
  email: string;
  display_name: string | null;
  settings: UserSettings;
}

// "theirstack" and "github" come from the FastAPI backend; the boards in the
// middle are the key-free ones aggregated in lib/discover/no-key-sources.ts.
// "jsearch" and "adzuna" run through that same route but on a key the user
// pastes into their own browser (lib/discover/keys.ts).
export type DiscoverySource =
  | "theirstack"
  | "github"
  | "greenhouse"
  | "lever"
  | "ashby"
  | "remotive"
  | "remoteok"
  | "jsearch"
  | "adzuna"
  // Board-wide feeds, prefixed so the orchestrator can route them by shape
  // rather than by an ever-growing list of names. Unlike the ATS entries above,
  // these carry every company on their board, so coverage does not depend on a
  // slug list. Registered in ./discover/board-feeds.
  | "feed:himalayas"
  | "feed:jobicy"
  | "feed:arbeitnow";

/**
 * One score component from `/index/search`'s `explain=true`. Debugging shape,
 * not rendered on a card; kept typed so a future breakdown view has it ready.
 */
export interface IndexScoreExplain {
  rank: number;
  retrieve_score: number;
  freshness_weight: number;
  mix_weight: number;
  text_rank_raw: number;
  age_days: number;
  effective_date: string;
  company_rank: number;
  matched_keywords: boolean;
  formula: string;
}

/**
 * One row from the pre-built index (`apps/api/src/job_os/services/job_index.py`),
 * populated by the overnight ingest crawl rather than a live per-search
 * fetch. Deliberately close to `DiscoveryResult` in shape (see
 * `docs/ingest-index.md`) so mapping one into the other is a field copy, not
 * a UI rewrite -- `first_seen_at`/`last_seen_at`/`posted_at_estimated` are the
 * real difference: freshness here is evidence, not a single asserted date.
 */
export interface IndexHitRead {
  id: string;
  source: string;
  source_id: string;
  source_url: string;
  title: string;
  company_name: string;
  company_domain: string | null;
  location: string | null;
  country_code: string | null;
  remote: boolean;
  department: string | null;
  employment_type: string | null;
  salary_min: number | null;
  salary_max: number | null;
  salary_currency: string | null;
  snippet: string;
  description_available: boolean;
  posted_at: string | null;
  posted_at_basis: string;
  posted_at_estimated: boolean;
  first_seen_at: string;
  last_seen_at: string;
  active: boolean;
  inactive_since: string | null;
  repost_count: number;
  rank: number;
  explain: IndexScoreExplain | null;
}

export interface IndexSearchRequest {
  title_keywords?: string[];
  query?: string | null;
  location?: string | null;
  country_codes?: string[];
  company?: string | null;
  sources?: string[];
  remote?: boolean | null;
  max_age_days?: number | null;
  posted_within_days?: number | null;
  include_inactive?: boolean;
  include_duplicates?: boolean;
  require_description?: boolean;
  salary_min?: number | null;
  limit?: number;
  offset?: number;
  explain?: boolean;
}

export interface IndexSearchResponse {
  results: IndexHitRead[];
  total_matched: number;
  total_matched_capped: boolean;
  candidates_considered: number;
  took_ms: number;
  keyword_query: string | null;
}

export interface DiscoveryResult {
  source: DiscoverySource | string;
  source_label: string | null;
  source_id: string;
  source_url: string;
  title: string;
  company_name: string | null;
  company_domain: string | null;
  location: string | null;
  country_code: string | null;
  posted_at: string | null;
  description: string;
  technologies: string[];
  already_imported: boolean;
}

export interface DiscoverySearchRequest {
  sources?: DiscoverySource[];
  title_keywords?: string[];
  description_keywords?: string[];
  country_codes?: string[];
  technology_slugs?: string[];
  max_age_days?: number;
  limit?: number;
  page?: number;
}

export interface SavedSearch {
  id: string;
  name: string;
  query: DiscoverySearchRequest;
  last_run_at: string | null;
  last_run_count: number | null;
  created_at: string;
  updated_at: string;
}

export interface SmartSearchResponse {
  filters: DiscoverySearchRequest;
  explanation: string;
}

export interface DiscoverySourceError {
  // Widened for the same reason DiscoveryResult.source is: a user's own feed
  // reports as "custom:<id>", which no union can enumerate.
  source: DiscoverySource | string;
  message: string;
}

/**
 * Where a discovery search spent its time, measured rather than guessed.
 *
 * Only /api/discover fills this in: it is the route that fans out to dozens of
 * boards itself, so it is the one whose latency needs an explanation. Labels
 * are redacted to provider/slug, never a URL, because a source URL can carry a
 * key in its query string.
 */
export interface DiscoveryTimings {
  /** Wall time inside the orchestrator. */
  total_ms: number;
  /** Wall time of the whole route handler, orchestrator included. */
  route_ms?: number;
  /** Per phase. The phases run concurrently, so these overlap by design. */
  phases: Record<string, number>;
  /** Outbound requests the instrumented fetchers made. */
  requests: number;
  /** Decoded response bytes actually read. Excludes anything skipped. */
  bytes: number;
  /** Bytes the payload guard declined to download. */
  skipped_bytes: number;
  /** Sources skipped by the payload guard, with the size they declared. */
  oversized: { source: string; bytes: number }[];
  /** The slowest few requests, so one bad board is nameable from a log line. */
  slowest: { source: string; ms: number; bytes: number }[];
  /** The few biggest downloads, which is where the bytes above actually went. */
  heaviest: { source: string; ms: number; bytes: number }[];
}

export interface DiscoverySearchResponse {
  results: DiscoveryResult[];
  source_counts: Record<string, number>;
  errors: DiscoverySourceError[];
  /** Present on /api/discover responses only. */
  timings?: DiscoveryTimings;
}

export interface Application {
  id: string;
  job: Job;
  status: AppStatus;
  applied_at: string | null;
  recruiter_name: string | null;
  recruiter_email: string | null;
  notes: string | null;
  next_action_at: string | null;
  next_action_label: string | null;
  archived: boolean;
  created_at: string;
  updated_at: string;
}

// ── Interview prep ─────────────────────────────────────────────────────────
// Mirrors apps/api/src/job_os/schemas/interviews.py. A pack is generated from
// the parsed job description and the candidate's own verified fact vault, so
// every scaffolded answer arrives with the rows it was built from and every
// unanswerable question arrives as a declared gap.

export type QuestionCategory =
  | "technical"
  | "behavioral"
  | "resume_probe"
  | "candidate_ask";

export type ReadinessBand = "strong" | "mixed" | "thin" | "not_scored";

export type QuestionConfidence = "shaky" | "workable" | "solid";

/** One verified row behind a scaffolded answer. `text` is the vault's own wording. */
export interface EvidenceCitation {
  fact_id: string;
  fact_bullet_id: string | null;
  label: string;
  text: string;
}

export interface AnswerScaffold {
  situation: string;
  task: string;
  action: string;
  result: string;
}

export interface TopicReadiness {
  topic: string;
  preferred: boolean;
  status: "evidenced" | "gap";
  citations: string[];
  alternatives: string[];
}

export interface DefenceRisk {
  text: string;
  where: string;
  reason: string;
}

/**
 * Why the readiness number is what it is.
 *
 * `score` is derived by the server from must-have coverage and is the grade.
 * `model_estimate` is the generating model's own guess, carried for context and
 * never authoritative, which is the same split `ResumeReviewResult` makes. The
 * UI has to keep them visually distinct or the distinction stops existing.
 */
export interface ReadinessReport {
  score: string | number | null;
  band: ReadinessBand;
  scored_topics: number;
  evidenced_topics: number;
  topics: TopicReadiness[];
  defence_risks: DefenceRisk[];
  unscored_requirements: string[];
  formula: string;
  thresholds: Record<string, number>;
  model_estimate: number | null;
}

export interface InterviewQuestion {
  id: string;
  prep_id: string;
  category: QuestionCategory;
  position: number;
  question: string;
  topic: string | null;
  difficulty: string;
  why_asked: string;
  scaffold: AnswerScaffold | null;
  evidence: EvidenceCitation[];
  gap: boolean;
  gap_note: string | null;
  removed_claims: string[];
  flagged: boolean;
  confidence: string | null;
  times_reviewed: number;
  last_reviewed_at: string | null;
  next_review_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface InterviewPrep {
  id: string;
  application_id: string;
  job_id: string | null;
  resume_version_id: string | null;
  // Numeric(4,1) crosses the wire as a JSON string from the FastAPI side, so it
  // is widened here rather than being parsed at three call sites.
  readiness_score: string | number | null;
  model_estimate: number | null;
  note: string;
  readiness_report: Partial<ReadinessReport>;
  questions: InterviewQuestion[];
  job_title: string | null;
  company_name: string | null;
  created_at: string;
  updated_at: string;
}

/** The four situations a job search actually produces. */
export type OutreachVariant =
  | "cold_hiring_manager"
  | "referral_ask"
  | "alumni"
  | "post_application_followup";

export type ContactRelationship =
  | "hiring_manager"
  | "recruiter"
  | "engineer"
  | "alumni"
  | "other";

export interface OutreachContact {
  id: string;
  application_id: string;
  full_name: string;
  title: string | null;
  company_name: string | null;
  email: string | null;
  /**
   * Where the address came from. Shown to the user rather than smoothed away:
   * an address they read off a company page and one a provider inferred from a
   * domain pattern are different bets, and the bounce is theirs to accept.
   */
  email_source: string | null;
  confidence: number | null;
  linkedin_url: string | null;
  evidence_url: string | null;
  relationship_kind: ContactRelationship;
  provider: string;
  /**
   * What the user asserts they share with this person. Filling these in does
   * NOT license the message to say it: the API intersects them with the
   * verified fact vault first, and an assertion with no matching verified fact
   * produces nothing the draft may claim.
   */
  shared_school: string | null;
  shared_employer: string | null;
  referred_by: string | null;
  do_not_contact: boolean;
  notes: string | null;
  messages_sent: number;
  last_sent_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface OutreachContactCreate {
  full_name: string;
  title?: string | null;
  company_name?: string | null;
  email?: string | null;
  linkedin_url?: string | null;
  evidence_url?: string | null;
  relationship_kind?: ContactRelationship;
  shared_school?: string | null;
  shared_employer?: string | null;
  referred_by?: string | null;
  notes?: string | null;
}

/** One phrase in the message and the verified row that backs it. */
export interface OutreachProvenanceRow {
  phrase: string;
  evidence_kind: string;
  evidence_id: string;
  evidence_text: string;
}

export interface OutreachSharedContextRow {
  id: string;
  kind: string;
  claim: string;
}

export interface OutreachFollowUp {
  suggested_at: string | null;
  label: string;
  is_final: boolean;
}

export interface OutreachDraft {
  contact_id: string;
  variant: OutreachVariant;
  subject: string;
  body: string;
  word_count: number;
  word_cap: number;
  provenance: OutreachProvenanceRow[];
  shared_context_used: OutreachSharedContextRow[];
  follow_up: OutreachFollowUp;
  warnings: string[];
  note: string;
}

export interface OutreachStatus {
  can_draft: boolean;
  blocked_reason: string | null;
  messages_sent: number;
}

export interface OutreachHistoryRow {
  kind: string;
  occurred_at: string;
  contact_id: string | null;
  contact_name: string | null;
  variant: string | null;
  channel: string | null;
  subject: string | null;
}
