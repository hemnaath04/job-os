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
   * words. Shown next to the template: two of the six are two-column designs
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
  archived_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ResumeVersionSummary {
  id: string;
  resume_id: string;
  ats_score: string | null;
  approved_by_user: boolean;
  pdf_r2_key: string | null;
  docx_r2_key: string | null;
  spawned_from_job_id: string | null;
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

export interface UserSettings {
  theme: "system" | "dark" | "light";
  default_resume_id: string | null;
  default_function: string | null;
  default_level: string | null;
  default_location: string | null;
  timezone: string | null;
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

export interface DiscoverySearchResponse {
  results: DiscoveryResult[];
  source_counts: Record<string, number>;
  errors: DiscoverySourceError[];
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
