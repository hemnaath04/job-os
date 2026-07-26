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

export interface Resume {
  id: string;
  name: string;
  base_role: string | null;
  is_master: boolean;
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

export interface ResumeChatResponse {
  message: string;
  suggestions: string[];
  proposal_id: string | null;
  proposed_json_resume: JsonResume | null;
  version: ResumeVersion | null;
  review: ResumeReviewResult | null;
}

export interface RevisionMessage {
  id: string;
  resume_version_id: string;
  role: "user" | "assistant";
  content: string;
  suggestions: string[];
  proposed_json_resume: JsonResume | null;
  applied: boolean;
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
  weekly_summary_email: boolean;
}

export type UserSettingsPatch = Partial<UserSettings>;

export interface MeRead {
  id: string;
  email: string;
  display_name: string | null;
  settings: UserSettings;
}

export type DiscoverySource = "theirstack" | "github";

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
  source: DiscoverySource;
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
